from __future__ import annotations
import re,time,unicodedata
from difflib import SequenceMatcher
from .core import Error,writejson,key,check_revision
from .jobs import clip_key,valid_clip,summary,final_path,verify_publication,take_signature
from .planner import count_words,SMALL,TENS,COUNT_RE
from .speech_compare import POLICY,comparison_tokens,quantities,negations,mathematical_markers,spoken_currency_observation


def tokens(s):
    from .linguistic import safe_unicode
    return re.findall(r"[\w]+(?:[.'’][\w]+)*",safe_unicode(s).lower().replace('’',"'"))
def distance(a,b):
    prev=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        row=[i]
        for k,y in enumerate(b,1): row.append(min(row[-1]+1,prev[k]+1,prev[k-1]+(x!=y)))
        prev=row
    return prev[-1]

def missing_final_clause(expected,observed):
    """Flag an ASR ending at an earlier authored clause, even below 20% WER.

    This is uncertain recognizer evidence, never an automatic listening verdict.
    Require a close prefix match and a whole missing tail of at least four words.
    """
    ref=comparison_tokens(expected);hyp=comparison_tokens(observed)
    for match in re.finditer(r'[.!?;:,]\s+',expected):
        prefix=comparison_tokens(expected[:match.end()]);tail=comparison_tokens(expected[match.end():])
        if len(prefix)<12 or len(tail)<4 or len(hyp)>len(prefix)+1:continue
        long_sentence=len(tail)>16
        if long_sentence and match[0][0] not in '.!?':continue
        tolerance=.12 if long_sentence else .06
        if distance(prefix,hyp)>max(1,int(len(prefix)*tolerance)):continue
        # Longer losses need a strongly recovered ending of the earlier
        # sentence. This permits a few unrelated ASR substitutions upstream.
        if long_sentence and distance(prefix[-6:],hyp[-6:])>1:continue
        n=min(6,len(tail),len(hyp))
        if n>=4 and distance(tail[-n:],hyp[-n:])>=n-1:return True
    return False

def missing_final_words(expected,observed):
    """Locate a close source prefix cut inside a sentence, not only at clauses.

    At least four final words must be absent after twelve recovered words.
    A recovered authored ending or repeated ending abstains. Like other ASR
    findings this is a review lead; bounded retakes require a second observer.
    """
    ref=comparison_tokens(expected);hyp=comparison_tokens(observed)
    if len(hyp)<12 or len(ref)-len(hyp)<4:return False
    n=min(6,len(hyp))
    if distance(ref[-n:],hyp[-n:])<n-1:return False
    for end in range(max(12,len(hyp)-2),min(len(ref)-4,len(hyp)+2)+1):
        prefix=ref[:end]
        if distance(prefix,hyp)<=max(1,int(len(prefix)*.06)) and distance(prefix[-n:],hyp[-n:])<=1:
            return True
    return False

def content_gaps(expected,observed):
    """Review multiword internal losses bracketed by recovered source anchors."""
    ref=comparison_tokens(expected);hyp=comparison_tokens(observed)
    operations=SequenceMatcher(a=ref,b=hyp,autojunk=False).get_opcodes();gaps=[]
    for i,(tag,a,b,c,d) in enumerate(operations):
        if tag not in ('delete','replace') or b-a-(d-c)<3 or i==0 or i+1==len(operations):continue
        left,right=operations[i-1],operations[i+1]
        if left[0]==right[0]=='equal' and left[2]-left[1]>=4 and right[2]-right[1]>=4:gaps.append({'expected_token_range':[a,b],'observed_token_range':[c,d]})
    return gaps


def missing_measurement_instruction(expected,observed):
    """Catch a dropped measurement verb at an otherwise recovered boundary."""
    from .boundaries import sentence_spans
    starts=set()
    for sentence in sentence_spans(expected):
        words=comparison_tokens(sentence['text'])
        if words and words[0] in ('add','remove') and any(word.startswith('#') for word in words[1:4]):
            starts.add(len(comparison_tokens(expected[:sentence['start']])))
    if not starts:return False
    ref,hyp=comparison_tokens(expected),comparison_tokens(observed)
    alignment=SequenceMatcher(None,ref,hyp,autojunk=False).get_opcodes()
    for index,(action,start,end,_,_) in enumerate(alignment[:-1]):
        if action not in ('delete','replace') or start not in starts or end-start!=1:
            continue
        following=alignment[index+1]
        if following[0]=='equal' and following[2]-following[1]>=3 and (start==0 or alignment[index-1][0]=='equal'):
            return True
    return False

def negation_contexts(text):
    from collections import Counter
    ts=comparison_tokens(text);ignored={'a','an','the','to','of','is','be','been','being','was','were','are','and','or','that','this'}
    return Counter(tuple(t for t in ts[i+1:i+6] if t not in ignored)[:2] for i,t in enumerate(ts) if t in ('not','never','no',"ain't"))

def misplaced_negation(expected,observed):
    a,b=negation_contexts(expected),negation_contexts(observed)
    return bool(a) and sum(a.values())==sum(b.values()) and a!=b

def reference_leakage(expected,observed,reference):
    ref=comparison_tokens(reference or '');target=comparison_tokens(expected);hyp=comparison_tokens(observed)
    wanted={tuple(target[i:i+7]) for i in range(len(target)-6)}
    reference_only={tuple(ref[i:i+7]) for i in range(len(ref)-6)}-wanted
    return any(tuple(hyp[i:i+7]) in reference_only for i in range(len(hyp)-6))

def missing_parenthetical_counts(expected,observed,protected=None):
    wanted=COUNT_RE.findall(expected) if protected is None else protected
    missing=[];hyp=tokens(observed);used=set()
    for value in wanted:
        spoken=count_words(int(value)) if value.isdigit() else value.lower()
        alternatives=[tokens(spoken)]
        if value.isdigit():alternatives.append(tokens(value))
        else:
            known={w:str(i) for i,w in enumerate(SMALL)}
            known.update({w:str(i*10) for i,w in enumerate(TENS) if w})
            if value.lower() in known:alternatives.append([known[value.lower()]])
        match=next((set(range(i,i+len(a))) for a in alternatives for i in range(len(hyp)-len(a)+1) if hyp[i:i+len(a)]==a and not used.intersection(range(i,i+len(a)))),None)
        if match is None:missing.append(value)
        else:used.update(match)
    return missing

def check(store,jid,python,model,all_clips=False,require_review=False):
    with store.lock():
        j=store.get(jid); utterances=j['plan']['utterances']
        if require_review: all_clips=True
        listening_keys={r['clip_key'] for r in j.get('listening_findings',[]) if r['status']=='open'}
        if not all_clips: utterances=[u for i,u in enumerate(utterances) if i in (0,len(utterances)-1) or re.search(r'\d|\bnot\b|\bnever\b|--',u['text']) or mathematical_markers(u['text']) or clip_key(u,j) in listening_keys]
        if not utterances: raise Error('qa_empty','No eligible speech passages.')
        from .qa_cache import observe
        observations,observer,checkpoint=observe(store,j,utterances,python,model)
        rows=[]; errors=0; words=0;raw_errors=0;raw_words=0
        for u,row in zip(utterances,observations):
            if row['utterance']!=u['id']: raise Error('asr_contract','ASR returned an unexpected utterance identity.')
            literal_ref=tokens(u['text']);literal_hyp=tokens(row['text']);literal_edits=distance(literal_ref,literal_hyp);raw_errors+=literal_edits;raw_words+=len(literal_ref)
            plausible=[s for s in row['segments'] if s['start']<row['audio_seconds']+.1]
            comparison_text=spoken_currency_observation(u['text'],' '.join(s['text'].strip() for s in plausible))
            ref=comparison_tokens(u['text']);hyp=comparison_tokens(comparison_text);edits=distance(ref,hyp);errors+=edits;words+=len(ref)
            expected_numbers=re.findall(r'(?<!\w)[+-]?\d+(?:[.,]\d+)*',u['text']); observed_numbers=re.findall(r'(?<!\w)[+-]?\d+(?:[.,]\d+)*',row['text'])
            flags=[]
            findings=[r for r in j.get('listening_findings',[]) if r['status']=='open' and r['source_sha256']==j['document']['source_sha256'] and r['clip_key']==clip_key(u,j) and r['pcm_sha256']==row['pcm_sha256']]
            flags.extend('Listening finding: '+r['reason'] for r in findings)
            if quantities(ref)!=quantities(hyp): flags.append('Quantity disagreement requires inspection; raw numbers and cardinal spellings are compared by value.')
            if negations(ref)!=negations(hyp): flags.append('Negation disagreement requires listening.')
            if misplaced_negation(u['text'],comparison_text):flags.append('Negation is associated with different neighboring words; inspect its scope.')
            gaps=content_gaps(u['text'],comparison_text)
            if gaps:flags.append('ASR misses an internal source phrase between recovered anchors; inspect possible missing speech.')
            if missing_measurement_instruction(u['text'],comparison_text):flags.append('ASR loses the opening verb of a measurement instruction; inspect the sentence boundary.')
            if reference_leakage(u['text'],comparison_text,(j.get('voice') or {}).get('transcript')):flags.append('ASR contains a reference-only phrase; inspect possible conditioning-audio leakage.')
            if [t for t in ref if t in ('checked','unchecked')]!=[t for t in hyp if t in ('checked','unchecked')]:flags.append('Checklist status disagreement requires listening.')
            if mathematical_markers(u['text'])!=mathematical_markers(comparison_text):flags.append('Fraction, mathematical operator or physical-unit disagreement requires inspection.')
            if edits/max(1,len(ref))>.2: flags.append('WER above 20%; inspect this passage for drops/insertions.')
            if missing_final_clause(u['text'],comparison_text):flags.append('ASR stops near an earlier clause boundary; inspect a possibly missing final clause.')
            if missing_final_words(u['text'],comparison_text):flags.append('ASR recovers a source prefix but misses its ending; inspect possible mid-sentence truncation.')
            missing_counts=missing_parenthetical_counts(u['text'],comparison_text,u.get('protected_counts'))
            if missing_counts:flags.append('Parenthetical count not recovered by ASR; inspect the source and take: '+', '.join(missing_counts))
            warnings=[]
            if any(s['start']>row['audio_seconds']+.1 or s['end']>row['audio_seconds']+1 for s in row['segments']):warnings.append('ASR timestamps extend beyond the physical clip; transcript tail is unreliable. Check recognition before changing the audio.')
            rows.append({**row,'expected':u['text'],'source_blocks':u['sources'],'listening_findings':findings,'comparison_text':comparison_text,'ASR_segments_wholly_after_audio':len(row['segments'])-len(plausible),'word_errors':edits,'reference_words':len(ref),'wer':edits/max(1,len(ref)),'raw_word_errors':literal_edits,'raw_reference_words':len(literal_ref),'raw_wer':literal_edits/max(1,len(literal_ref)),'comparison_quantities_expected':quantities(ref),'comparison_quantities_observed':quantities(hyp),'expected_numbers':expected_numbers,'asr_numbers':observed_numbers,'missing_parenthetical_counts':missing_counts,'observation_warnings':warnings,'flags':flags,'retake_action':f'hamr repair {jid} --retake {u["id"]}'})
        report={'schema_version':1,'job':jid,'revision':j['revision'],'plan_sha256':j['plan']['plan_sha256'],'asr_model_files':observer['model_files'],'observer':observer,'observation_checkpoints':checkpoint,'comparison_policy':POLICY,'device':'cpu','compute_type':'int8','prompt':None,'clips_checked':len(rows),'reference_words':words,'word_errors':errors,'wer':errors/max(1,words),'raw_reference_words':raw_words,'raw_word_errors':raw_errors,'raw_wer':raw_errors/max(1,raw_words),'flagged_passages':sum(bool(r['flags']) for r in rows),'disposition':'ASR disagreements are uncertain evidence. Recorded listening findings remain independent holds until the exact audio is accepted or replaced; a matching transcript cannot clear them.','rows':rows}
        dest=store.path(j)/f'qa-r{j["revision"]}.json'; writejson(dest,report)
        if require_review:
            current={(clip_key(u,j),r['pcm_sha256']) for u,r in zip(utterances,rows)}
            for finding in j.get('listening_findings',[]):
                if finding['status']=='open' and (finding['clip_key'],finding['pcm_sha256']) not in current:
                    finding.update(status='superseded',resolution='The reported PCM is no longer in this plan/take set. Replacement audio has not inherited any listening approval.',superseded_at=time.time())
            accepted={(r['clip_key'],r.get('pcm_sha256')) for r in j.get('audio_acceptances',[]) if r['source_sha256']==j['document']['source_sha256']}
            by_id={u['id']:u for u in utterances}
            observed_signature=key(sorted({(clip_key(by_id[r['utterance']],j),r['pcm_sha256'],r['audio_samples']) for r in rows}))
            if observed_signature!=take_signature(store,j):raise Error('qa_clip_changed','PCM changed during speech QA.','Repeat QA on the current verified takes.')
            j['audio_gate']=True
            j['audio_gate_checked_plan']=j['plan']['plan_sha256']
            j['audio_gate_checked_takes']=observed_signature
            j['audio_gate_checked_comparison_policy']=POLICY
            j['audio_review']=[{'utterance':r['utterance'],'clip_key':clip_key(by_id[r['utterance']],j),'pcm_sha256':r['pcm_sha256'],'sources':by_id[r['utterance']]['sources'],'flags':r['flags'],'report':str(dest)} for r in rows if r['flags'] and (clip_key(by_id[r['utterance']],j),r['pcm_sha256']) not in accepted]
            if j['audio_review']:
                j['audio_gate_approved_plan']=None;j['audio_gate_approved_takes']=None;j['audio_gate_approved_comparison_policy']=None; j['status']='needs_review'; j['error']={'code':'speech_qa_review','message':'Speech QA or recorded listening evidence requires review. Verified clips and unrelated jobs are retained.','action':f'hamr show {jid} --content; inspect the QA report, then retake or explicitly accept the checked clip.'}
            else:
                j['audio_gate_approved_plan']=j['plan']['plan_sha256']; j['error']=None
                j['audio_gate_approved_takes']=observed_signature
                j['audio_gate_approved_comparison_policy']=POLICY
                j['status']='needs_review' if j['plan']['issues'] else ('ready' if verify_publication(final_path(j),j) else 'queued')
            store.save(j)
        return {'schema_version':1,'status':j['status'],'job':jid,'clips_checked':len(rows),'wer':report['wer'],'flagged_passages':report['flagged_passages'],'report':str(dest),'device':'cpu','prompt':None}


def accept(store,jid,utterance,reason,*,expect_revision=None):
    if not reason.strip(): raise Error('review_reason','Record what you checked before accepting a speech reading.')
    with store.lock():
        j=store.get(jid)
        check_revision(j,expect_revision)
        if j.get('audio_gate_checked_plan')!=j['plan']['plan_sha256']: raise Error('review_changed','The plan changed since the last complete required QA pass.','Run qa --all --require-review on the current plan before accepting individual readings.')
        if j.get('speech_contract',1)>=2 and j.get('audio_gate_checked_comparison_policy')!=POLICY:raise Error('review_changed','The speech comparison policy changed since required QA.','Run qa --all --require-review with this application before accepting a reading.')
        if not j.get('audio_gate_checked_takes') or j['audio_gate_checked_takes']!=take_signature(store,j):raise Error('review_changed','The physical takes changed since required QA.','Run qa --all --require-review before accepting a reading.')
        u=next((u for u in j['plan']['utterances'] if u['id']==utterance),None)
        review=next((r for r in j.get('audio_review',[]) if r['utterance']==utterance),None)
        if not u or not review: raise Error('unknown_review','No pending speech review for that utterance.')
        k=clip_key(u,j)
        receipt=valid_clip(store,k)
        if k!=review['clip_key'] or not receipt or review.get('pcm_sha256')!=receipt['sha256']: raise Error('review_changed','The reviewed take changed; run QA on the current plan before accepting it.')
        j.setdefault('audio_acceptances',[]).append({'utterance':utterance,'clip_key':k,'pcm_sha256':receipt['sha256'],'source_sha256':j['document']['source_sha256'],'plan_sha256':j['plan']['plan_sha256'],'reason':reason,'at':time.time()})
        for finding in j.get('listening_findings',[]):
            if finding['status']=='open' and finding['clip_key']==k and finding['pcm_sha256']==receipt['sha256']:
                finding.update(status='accepted',resolution=reason,resolved_at=time.time())
        j['audio_review']=[r for r in j['audio_review'] if r['clip_key']!=k]
        if not j['audio_review']:
            j['audio_gate_approved_plan']=j['plan']['plan_sha256']; j['error']=None
            j['audio_gate_approved_takes']=j['audio_gate_checked_takes']
            j['audio_gate_approved_comparison_policy']=POLICY
            j['status']='needs_review' if j['plan']['issues'] else ('ready' if verify_publication(final_path(j),j) else 'queued')
        store.save(j); return summary(store,j)


def flag(store,jid,utterance,reason,*,expect_revision=None):
    """Record human audio evidence independently of what ASR recognizes."""
    if not reason.strip():raise Error('review_reason','Record the audible problem and source/time context.')
    with store.lock():
        j=store.get(jid)
        check_revision(j,expect_revision)
        u=next((u for u in j['plan']['utterances'] if u['id']==utterance),None)
        if u is None:raise Error('unknown_utterance','Listening finding names no current source occurrence.')
        k=clip_key(u,j);receipt=valid_clip(store,k)
        if not receipt:raise Error('review_missing_clip','A listening finding requires a committed verified take.')
        finding={'utterance':utterance,'revision':j['revision'],'clip_key':k,'pcm_sha256':receipt['sha256'],'source_sha256':j['document']['source_sha256'],'sources':u['sources'],'reason':reason.strip(),'status':'open','created':time.time()}
        j.setdefault('listening_findings',[]).append(finding)
        revoked=[r for r in j.get('audio_acceptances',[]) if r['clip_key']==k and r.get('pcm_sha256')==receipt['sha256']]
        if revoked:j.setdefault('revoked_audio_acceptances',[]).extend(revoked)
        j['audio_acceptances']=[r for r in j.get('audio_acceptances',[]) if r not in revoked]
        j['audio_review']=[r for r in j.get('audio_review',[]) if r['clip_key']!=k]
        j['audio_review'].append({'utterance':utterance,'clip_key':k,'pcm_sha256':receipt['sha256'],'sources':u['sources'],'flags':['Listening finding: '+reason.strip()],'report':'Recorded listening evidence'})
        j['audio_gate']=True;j['audio_gate_approved_plan']=None;j['audio_gate_approved_takes']=None;j['status']='needs_review'
        j['error']={'code':'listening_review','message':'Reported audio needs review independently of ASR.','action':f'Retake the exact passage or inspect it, then use hamr review {jid} --accept {utterance} --reason after complete current-plan QA.'}
        store.save(j);return summary(store,j)
