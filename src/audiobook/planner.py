from __future__ import annotations
import re
from .core import Document,key,Error,digest

RULES='faithful-14'
SOURCE_STRUCTURE=('faithful-12','faithful-13',RULES)
NORMALIZATION_TRACE=('faithful-13',RULES)
MODERN=('faithful-6','faithful-7','faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE)
ASIDES=('faithful-3','faithful-4','faithful-5','faithful-6','faithful-7','faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE)
BOUNDED=('faithful-4','faithful-5','faithful-6','faithful-7','faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE)
KNOWN_RULES=('faithful-1','faithful-2',*ASIDES)
PAUSE_ONLY=re.compile(r'''[\s.,…—–\-"'“”‘’!?;:]+''')
SCENE_MARKER=re.compile(r'(?:\*\s*){3,}')
SMALL=['zero','one','two','three','four','five','six','seven','eight','nine','ten','eleven','twelve','thirteen','fourteen','fifteen','sixteen','seventeen','eighteen','nineteen']
TENS=['','','twenty','thirty','forty','fifty','sixty','seventy','eighty','ninety']
def count_words(n):
    if n<20:return SMALL[n]
    if n<100:return TENS[n//10]+(' '+SMALL[n%10] if n%10 else '')
    return SMALL[n//100]+' hundred'+(' '+count_words(n%100) if n%100 else '')

COUNT_RE=re.compile(r'\(\s*((?:0|[1-9]\d{0,2})|'+ '|'.join(SMALL+TENS[2:])+r')\s*\)',re.I)

def normalize(text,aliases=None,rule_revision=RULES,abbreviation_audit=None,preserve_lines=False,*,normalization_audit=None,source=None,literals=(),typed=True):
    text=text.replace('\u00ad','').replace('\u00a0',' ')
    # Literal spelling is distinct from the runtime's performance-cue syntax.
    from .linguistic import literal_cues
    text=literal_cues(text)
    if rule_revision in ('faithful-5','faithful-6','faithful-7','faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE):
        if rule_revision in MODERN:
            from .linguistic import technical
            from .boundaries import expand
            text=technical(text,preserve_lines=preserve_lines)
        else:
            from .abbreviations import expand
        expanded,edits,pending=expand(text,aliases)
        if abbreviation_audit is not None:
            abbreviation_audit.update(readings=edits,review=pending)
            if rule_revision in NORMALIZATION_TRACE and (edits or pending):
                abbreviation_audit['input_basis']='block_source' if text==source else 'abbreviation_input'
                if text!=source:abbreviation_audit['input']=text
        text=expanded
    else:
        for src,dst in sorted((aliases or {}).items(),key=lambda p:-len(p[0])):
            text=re.sub(r'(?<!\w)'+re.escape(src)+r'(?!\w)',lambda _:dst,text)
    if rule_revision in ('faithful-2',)+ASIDES:
        comparisons={'>=':'greater than or equal to','<=':'less than or equal to','>':'greater than','<':'less than','≥':'greater than or equal to','≤':'less than or equal to'}
        text=re.sub(r'(?<![<>=])(?:>=|<=|>|<|≥|≤)(?=\s*[+-]?\d)',lambda m:' '+comparisons[m[0]]+' ',text)
        digits=dict(zip('0123456789',['zero','one','two','three','four','five','six','seven','eight','nine']))
        text=re.sub(r'\b(\d{1,2})/(\d{1,2})/(\d{4})\b',lambda m:' slash '.join(' '.join(digits[c] for c in group) for group in m.groups()),text)
    if rule_revision in ASIDES:
        # Breeze may treat parenthesized counts as performance cues. Keep their
        # values in the spoken line, with ordinary aside punctuation instead.
        text=COUNT_RE.sub(lambda m:', '+(count_words(int(m[1])) if m[1].isdigit() else m[1])+',',text)
        text=re.sub(r'(?<!\S)#{2,}!([^\s:]+)(?=:|\s+signed (?:in|out))',lambda m:m[1].replace('_',' '),text)
        text=re.sub(r'([\w.+-]+)@([\w.-]+\.[A-Za-z]{2,})',lambda m:m[1]+' at '+m[2].replace('.',' dot '),text)
        text=re.sub(r'\s+,',',',text)
        text=re.sub(r',\s*([.!?;:])',r'\1',text)
    if rule_revision in SOURCE_STRUCTURE:
        from .spoken_forms import expand as written_forms
        text=written_forms(text,literal_clocks=rule_revision not in NORMALIZATION_TRACE or not typed)
    text=re.sub(r'[^\S\n]+' if preserve_lines else r'\s+',' ',text).strip()
    if rule_revision in NORMALIZATION_TRACE and typed and (not literals or text==source):
        from .typed_readings import expand as typed_readings
        revised,edits=typed_readings(text,literals)
        if normalization_audit is not None and edits:
            normalization_audit.update(input_basis='block_source' if text==source else 'normalization_input',
                                       offsets='half-open Unicode code points',edits=edits)
            if text!=source:normalization_audit['input']=text
        text=revised
    return text

def repeat_runs(text):
    """Find the earliest shortest exact word-period; retain original case/tokens."""
    patterns=[(n,re.compile(r'(\b[\w’]+(?:\s+[\w’]+){'+str(n-1)+r'}\b)(?:\s+\1){3,}',re.I)) for n in range(2,5)]
    cursor=0
    while cursor<len(text):
        matches=[(m.start(),n,m) for n,p in patterns if (m:=p.search(text,cursor))]
        if not matches:return
        _,n,m=min(matches,key=lambda x:(x[0],x[1]));yield m,n;cursor=m.end()

def split(text,words=65,rule_revision=RULES):
    # End sentences at punctuation followed by whitespace, preserving version/decimal tokens.
    # Repeated commands and verbose chat/email labels can produce much more
    # speech than whitespace word counts predict. Bound these observed cases.
    if rule_revision in BOUNDED:
        runs=list(repeat_runs(text))
        if runs:
            pieces=[];cursor=0;units=set()
            for m,n in runs:
                pieces.extend(split(text[cursor:m.start()],words,rule_revision))
                ts=m[0].split();current=[' '.join(ts[i:i+n]) for i in range(0,len(ts),n)];units.update(current);pieces.extend(current);cursor=m.end()
            pieces.extend(split(text[cursor:],words,rule_revision))
            if len(pieces)>1 and pieces[0] not in units and len(pieces[0].split())<3:
                first=pieces.pop(0);pieces[0]=first+' '+pieces[0]
            if len(pieces)>1 and pieces[-1] not in units and len(pieces[-1].split())<3:
                last=pieces.pop();pieces[-1]+=' '+last
            return pieces
    structured=rule_revision in ASIDES and (bool(re.search(r'@|\bat\b [\w.-]+ dot \w+|#{2,}!',text)) or len(re.findall(r'\b[\w’_]+:',text))>=2)
    repeated=rule_revision=='faithful-3' and bool(re.search(r'(\b[\w’]+\s+[\w’]+\b)(?:\s+\1){3,}',text,re.I))
    budget=18 if repeated else (32 if structured else words)
    def cost(s):
        if not structured:return len(s.split())
        return sum(max(1,(len(w)+9)//10) for w in re.findall(r"[^\W_]+(?:['’][^\W_]+)*",s))
    from .abbreviations import attached_boundary
    if rule_revision in MODERN:
        from .boundaries import sentence_parts,safe_cut
        sentences=sentence_parts(text)
    elif rule_revision=='faithful-5':
        from .abbreviations import sentence_parts
        sentences=sentence_parts(text)
    else:sentences=re.split(r'(?<=[.!?;])\s+',text)
    pieces=[]; buf=[]
    for sentence in sentences:
        toks=sentence.split()
        while cost(' '.join(toks))>budget:
            if buf: pieces.append(' '.join(buf)); buf=[]
            end=1
            while end<len(toks) and cost(' '.join(toks[:end+1]))<=budget:end+=1
            if rule_revision=='faithful-5':end=attached_boundary(toks,end)
            elif rule_revision in MODERN:end=safe_cut(toks,end)
            pieces.append(' '.join(toks[:end])); toks=toks[end:]
        if sum(cost(x) for x in buf)+cost(' '.join(toks))>budget and buf: pieces.append(' '.join(buf)); buf=[]
        if toks: buf.append(' '.join(toks))
    if buf: pieces.append(' '.join(buf))
    if rule_revision in BOUNDED and len(pieces)>1:
        # Shorter structured budgets exposed one-word leftovers. Keep those in
        # neighboring context, allowing at most two words of budget overhang.
        i=0
        while i<len(pieces):
            if cost(pieces[i])<3:
                if i>0 and cost(pieces[i-1]+' '+pieces[i])<=budget+2:
                    pieces[i-1]+=' '+pieces.pop(i);continue
                if i+1<len(pieces) and cost(pieces[i]+' '+pieces[i+1])<=budget+2:
                    pieces[i]+=' '+pieces.pop(i+1);continue
            i+=1
    return pieces

def plan(doc:Document,policy=None,aliases=None,rule_revision=RULES):
    if rule_revision not in KNOWN_RULES:raise Error('newer_rules','This narration rule revision is not supported by this build.','Use the matching or newer application; the saved job and approved plan are retained.')
    selected=policy or {}; mode=selected.get('mode','listen')
    if selected.get('notes','after_paragraph') not in ('after_paragraph','inline','companion'):raise Error('note_policy','Notes must be after_paragraph, inline or companion.')
    policy={'mode':mode,'tables':'speak','code':'speak' if mode=='procedure' else 'companion','references':'speak' if mode=='study' else 'companion',**selected}
    document_policy=policy.get('document',{})
    if not isinstance(document_policy,dict): raise Error('document_policy_format','Document policy must be a JSON object.')
    overrides=document_policy.get('blocks',{})
    if document_policy:
        if document_policy.get('source_sha256')!=doc.source_sha256:
            raise Error('document_policy_source','Document policy does not match these source bytes.')
        known={b.id for b in doc.blocks}
        if not isinstance(overrides,dict) or set(overrides)-known:
            raise Error('document_policy_blocks','Document policy names unknown source blocks; review the current import.')
        by_id={b.id:b for b in doc.blocks}
        for bid,override in overrides.items():
            if not isinstance(override,dict) or override.get('action') not in ('companion','speak','pause') or override.get('verified') is not True or not isinstance(override.get('reason'),str) or not override['reason'].strip():
                raise Error('document_policy_verification','Each source override needs a supported action, verified=true and a source-supported reason.')
            if override['action']=='speak' and (override.get('expected_source_sha256')!=digest(by_id[bid].source.encode()) or ('spoken' in override and (not isinstance(override['spoken'],str) or not override['spoken'].strip()))):
                raise Error('document_policy_verification','A verified speech reading must bind the exact extracted source text and contain a nonempty reading.')
            if override['action']=='pause' and (override.get('expected_source_sha256')!=digest(by_id[bid].source.encode()) or not (by_id[bid].role=='scene_break' or SCENE_MARKER.fullmatch(by_id[bid].source.strip()))):
                raise Error('document_policy_verification','A pause override must bind an exact source scene marker; words cannot be replaced by a pause.')
            if override.get('announce') not in (None,'table','figure'):
                raise Error('document_policy_verification','Companion announcements support only a table or figure.')
    resolver=None
    if rule_revision==RULES:
        from .contextual import Resolver
        resolver=Resolver(doc,policy,document_policy,aliases or {})
    section_defaults={k:v for k,v in policy.items() if k not in ('document','context','context_implementation','context_review')}
    from .pdf_layout import structure_policy,spoken_paragraph
    document_title,pdf_chapters=structure_policy(doc,document_policy,by_source=rule_revision in SOURCE_STRUCTURE)
    source_positions={b.id:i for i,b in enumerate(doc.blocks)}
    delivery=document_policy.get('delivery',{})
    if not isinstance(delivery,dict) or delivery.get('flow','continuous') not in ('continuous','segmented'):raise Error('delivery_policy','Delivery flow must be continuous or segmented.')
    continuous=delivery.get('flow','continuous' if rule_revision in ('faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE) else 'segmented')=='continuous'
    anchor=delivery.get('anchor','paragraph' if rule_revision in ('faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE) else 'passage')
    if anchor not in ('paragraph','passage'):raise Error('delivery_policy','Delivery anchor must be paragraph or passage.')
    whole_sentences=delivery.get('whole_sentences',False)
    if type(whole_sentences) is not bool or whole_sentences and not continuous:
        raise Error('delivery_policy','Whole-sentence delivery requires continuous flow and a Boolean setting.')
    decisions=[]; utterances=[]; buffer=[]; source_ids=[]; chapter=None;chapter_id=None;flow_issues=[];captions=[];after_source_gap=False
    protected_readings={}
    heading_ids={item['source_block'] for item in document_policy.get('structure',{}).get('chapters',[])}
    flow_disputes={}
    if continuous and doc.format=='pdf':
        from .pdf_flow import insertions
        for insertion in insertions(doc.blocks,doc.inventory,expanded=rule_revision in ('faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE),margin_numbers=rule_revision in ('faithful-11',*SOURCE_STRUCTURE)):
            choice=overrides.get(insertion['block'],{})
            if choice.get('action')=='companion' or choice.get('placement')=='caption_after_paragraph':continue
            for bid in insertion['source_blocks']:flow_disputes[bid]=insertion['reason']
    original_by_id={b.id:b.source for b in doc.blocks}
    authored_lines={b.id for b in doc.blocks if b.locator.get('authored_line_breaks')}
    table_context=' '.join(b.source for b in doc.blocks) if rule_revision in SOURCE_STRUCTURE else ''
    def flush(hold_tail=False):
        nonlocal buffer,source_ids
        if buffer:
            joined=' '.join(buffer)
            protected=[];position=0
            for fragment,bid in zip(buffer,source_ids):
                if bid in protected_readings:
                    spoken,edits=protected_readings[bid]
                    shift=len(fragment)-len(spoken)
                    if fragment==spoken or fragment=='Illustration caption. '+spoken:
                        protected.extend((position+shift+edit['output_start'],position+shift+edit['output_end']) for edit in edits)
                position+=len(fragment)+1
            if hold_tail:
                from .boundaries import sentence_spans
                spans=list(sentence_spans(joined))
                if spans and not re.search(r'[.!?]["”’\')\]]*$',spans[-1]['text']):
                    flow_issues.append({'block':source_ids[-1],'reason':'An unfinished source sentence meets an unresolved region. Keep the fragment out of speech until its continuation is accounted for.','source_blocks':list(source_ids)})
                    if protected:
                        joined=joined[:spans[-1]['start']].rstrip()
                        protected=[(a,b) for a,b in protected if b<=len(joined)]
                    else:joined=' '.join(s['text'] for s in spans[:-1])
            repeated_units=set()
            repetitions=list(repeat_runs(joined)) if rule_revision in BOUNDED or continuous else []
            if rule_revision in BOUNDED and not protected:
                for m,n in repetitions:
                    ts=m[0].split();repeated_units.update(' '.join(ts[i:i+n]) for i in range(0,len(ts),n))
            if rule_revision in SOURCE_STRUCTURE and len(source_ids)==1 and source_ids[0] in authored_lines:
                pieces=[{'text':joined,'boundary':'authored_lines','over_budget':len(joined.split())>160 or len(joined.encode())>1000}]
            elif protected or continuous and not repetitions:
                from .continuous import passages
                limit=120 if continuous else 65
                sentence_limit=(160 if rule_revision in ('faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE) else 120) if continuous else limit
                pieces=passages(joined,max_words=limit,separate_incomplete=continuous and (rule_revision in ('faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE) or anchor=='paragraph'),max_sentence_words=sentence_limit,whole_sentences=whole_sentences,protected=protected)
            else:pieces=[{'text':part,'boundary':'legacy','over_budget':False} for part in split(joined,rule_revision=rule_revision)]
            for piece in pieces:
                part=piece['text']
                if hold_tail and piece['boundary']=='incomplete':
                    flow_issues.append({'block':source_ids[-1],'reason':'An unfinished source sentence meets an unresolved region. Keep the fragment out of speech until its continuation is accounted for.','source_blocks':list(source_ids)})
                    continue
                if piece['over_budget']:
                    reason='A source token or protected phrase exceeds the bounded continuous input budget. Supply an explicit source-supported reading.'
                    if whole_sentences:
                        reason='A source sentence exceeds the bounded one-request budget. Review the source and qualify an intact reading.'
                    flow_issues.append({'block':source_ids[0],'reason':reason,'source_blocks':list(source_ids)})
                    continue
                if rule_revision in ASIDES and PAUSE_ONLY.fullmatch(part):
                    if utterances and utterances[-1]['chapter']==chapter and utterances[-1].get('chapter_id')==chapter_id:
                        utterances[-1]['pause_samples']=max(12000,utterances[-1]['pause_samples'])
                        utterances[-1]['sources']=list(dict.fromkeys(utterances[-1]['sources']+source_ids))
                    continue
                utterance={'text':part,'sources':list(dict.fromkeys(source_ids)),'chapter':chapter,'language':'en','pause_samples':4320 if continuous else 7200}
                if continuous:utterance['delivery_boundary']=piece['boundary']
                if chapter_id is not None:utterance['chapter_id']=chapter_id
                if part in repeated_units:
                    utterance['literal_repeat']=True;utterance['pause_samples']=1200
                if rule_revision in ASIDES:
                    counts={v.lower() for bid in source_ids for v in COUNT_RE.findall(original_by_id[bid])}
                    spellings={count_words(int(v)) if v.isdigit() else v:v for v in counts}
                    protected=[spellings[m.strip().lower()] for m in re.findall(r',\s*([A-Za-z -]+)(?=[,.;!?])',part) if m.strip().lower() in spellings]
                    if protected:utterance['protected_counts']=protected
                utterances.append(utterance)
        buffer=[]; source_ids=[]
    def flush_captions():
        nonlocal captions
        if captions:
            flush()
            for caption,bid in captions:buffer.append('Illustration caption. '+caption);source_ids.append(bid)
            flush();captions=[]
    for b in doc.blocks:
        block_chapter=b.chapter
        block_chapter_id=b.locator.get('chapter_id')
        if rule_revision in SOURCE_STRUCTURE and b.locator.get('format')=='pdf' and not pdf_chapters and block_chapter_id is None:
            block_chapter='Opening';block_chapter_id='pdf:unclassified'
        if b.locator.get('format')=='pdf':
            if rule_revision in SOURCE_STRUCTURE and pdf_chapters:block_chapter='Opening';block_chapter_id='pdf:opening'
            for start,title in pdf_chapters.items():
                if start<=(source_positions[b.id] if rule_revision in SOURCE_STRUCTURE else b.locator['page']):
                    block_chapter=title
                    if rule_revision in SOURCE_STRUCTURE:block_chapter_id='pdf-source:'+str(start)
        if rule_revision in SOURCE_STRUCTURE and b.locator.get('auxiliary'):
            block_chapter=chapter or block_chapter;block_chapter_id=chapter_id
        section=policy.get('sections',{}).get(block_chapter,{})
        effective={**section_defaults,**section}
        if 'mode' in section:
            effective={**section_defaults,'code':'speak' if section['mode']=='procedure' else 'companion','references':'speak' if section['mode']=='study' else 'companion',**section}
        action='speak'; reason='Preserve authored content and qualifiers.'; text=spoken_paragraph(b); connector=False;force_pause=False;cells=();prepared_spans=None
        if rule_revision in MODERN and b.locator.get('inline_semantics'):
            from .epub_semantics import reading
            if resolver:
                text,prepared_spans=reading(b,effective.get('notes','after_paragraph'),with_spans=True)
            else:
                text=reading(b,effective.get('notes','after_paragraph'))
        prepared_text=text
        task=b.locator.get('checklist')
        if task and text.startswith(task['marker']):
            text=('Checked. ' if task['checked'] else 'Unchecked. ')+text[len(task['marker']):]
        optional_reference=rule_revision in ('faithful-7','faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE) and b.role=='reference' and effective['references']=='companion'
        if optional_reference: action='companion';reason='Positively identified optional reference material retained in the companion by the selected policy.';text=''
        elif continuous and b.role=='furniture':action='companion';reason='Repeated margin furniture retained in companion.';text=''
        elif b.id in flow_disputes:action='unreadable';reason=flow_disputes[b.id];text=''
        elif b.issue: action='unreadable'; reason=b.issue; text=''
        elif b.role in ('hidden','navigation'):action='companion';reason='Explicit hidden or navigation structure retained in source companion; primary prose continues in spine order.';text=''
        elif b.role=='furniture': action='companion'; reason='Position and repeated page-number sequence identify a printed folio; original text is retained.'; text=''
        elif b.role=='cover': action='companion'; reason='Package-declared cover asset preserved in the original EPUB and source companion.'; text=''
        elif b.role=='placeholder': action='companion'; reason='Source edition supplies only an illustration placeholder; preserve it without inventing visual content.'; text=''
        elif b.role=='reference' and effective['references']=='companion': action='companion'; reason='Explicitly labeled publisher/license lookup material retained by the approved reference policy.'; text=''
        elif b.role=='note': action='companion'; reason='Note preserved beside its linked claim in speech and in the companion.'; text=''
        elif b.role=='code' and effective['code']=='companion':
            action='companion'; reason='Exact literal retained in timed companion.'; text='A literal block is available in the companion.'; connector=True
        elif b.role=='code':
            text='Literal block. '+b.source; reason='Follow a procedure: speak the exact literal block by explicit policy.'; connector=True
        elif b.role=='table':
            rows=b.locator.get('rows',[])
            if effective['tables']=='companion': action='companion'; reason='User explicitly selected table lookup in companion.'; text='The full table is available in the companion.'; connector=True
            elif rows:
                if rule_revision in SOURCE_STRUCTURE:
                    from .table_readings import rows_for_speech
                    marker=resolver.table_ditto(b,block_chapter,block_chapter_id) if resolver else None
                    rows,table_issue=rows_for_speech(rows,table_context,marker=marker)
                    if table_issue:action='unreadable';reason=table_issue;text='';rows=[]
            if rows and action=='speak':
                headers=rows[0]
                if any(len(r)!=len(headers) for r in rows): action='unreadable'; reason='Table cells do not have reliable header associations.'; text=''
                else:
                    if resolver:
                        from .contextual import table_view
                        text,cells=table_view(rows)
                    else:text='Table. '+' '.join('; '.join(f'{h}: {v}' for h,v in zip(headers,row))+'.' for row in rows[1:])
                    reason='Speak every row with explicit header labels; keep exact table in companion.'
        if effective['references']=='companion' and re.search(r'^(bibliography|references|works cited|index)$',block_chapter,re.I):
            action='companion'; reason='Bibliography lookup retained in companion by selected policy.'; text=''
        if b.id in overrides:
            override=overrides[b.id];action=override['action'];reason=override['reason'];connector=False
            force_pause=action=='pause'
            text='' if action in ('companion','pause') else override.get('spoken',text if rule_revision in MODERN and not b.issue else spoken_paragraph(b))
            if action=='companion' and override.get('announce'):
                text='The '+override['announce']+' is available in the companion.';connector=True
        if rule_revision in SOURCE_STRUCTURE and action=='speak' and b.id not in overrides:
            from .pronunciation import unresolved
            from .epub_semantics import note_issues
            hints=unresolved(b,doc.language)+note_issues(b,effective.get('notes','after_paragraph'))
            if hints:action='unreadable';reason=' '.join(hints);text=''
        damaged=None
        if rule_revision in NORMALIZATION_TRACE and text:
            from .text_evidence import suspect_characters
            damaged=suspect_characters(text)
            if damaged:
                damaged['input_basis']='block_source' if text==b.source else 'prepared_reading'
                if text!=b.source:damaged['input']=text
                action='unreadable';reason='Source reading has an unmapped, private-use or control character. Check the original and supply a verified reading.';text=''
        if rule_revision in ('faithful-10','faithful-11',*SOURCE_STRUCTURE):
            if chapter!=block_chapter or chapter_id!=block_chapter_id:after_source_gap=False
            placement=overrides.get(b.id,{}).get('placement')
            independent_caption=placement=='caption_after_paragraph'
            prose=bool(text) and not (PAUSE_ONLY.fullmatch(text) or SCENE_MARKER.fullmatch(text))
            if action=='speak' and after_source_gap and prose and not independent_caption and b.role!='heading' and b.id not in heading_ids and placement!='standalone' and re.match(r'^[\s"“‘(\[]*[a-z]',text):
                action='unreadable';reason='A source continuation follows an unresolved body region. Verify its missing prefix before narration.';text=''
            if action=='unreadable':after_source_gap=True
            elif action=='speak' and prose and not independent_caption:after_source_gap=False
        scene_marker=rule_revision in MODERN and b.id not in overrides and action=='speak' and (b.role=='scene_break' or bool(SCENE_MARKER.fullmatch(text.strip())))
        pause_marker=force_pause or scene_marker or (rule_revision in ASIDES and action=='speak' and bool(PAUSE_ONLY.fullmatch(text)))
        if pause_marker:
            action='pause'; reason='Punctuation-only narrative pause; preserve the exact source without asking the voice model to invent speech.';text=''
        abbreviation_audit={};normalization_audit={}
        explicit_reading=rule_revision in MODERN and b.id in overrides and 'spoken' in overrides[b.id]
        preserve_lines=rule_revision in SOURCE_STRUCTURE and b.id in authored_lines and not explicit_reading
        literals=tuple((s['source_start'],s['source_end']) for s in b.locator.get('inline_semantics',[]) if s['kind']=='literal')
        projection=None;context_events=[];context_spans=[]
        if resolver and action=='speak' and b.id not in overrides:
            projection,context_events=resolver.prepare(b,text,chapter=block_chapter,chapter_id=block_chapter_id,cells=cells,
                                                       prepared_spans=prepared_spans if text==prepared_text else None)
        spoken=normalize(projection.text if projection else text,{} if explicit_reading else aliases,rule_revision,abbreviation_audit,
                         preserve_lines=preserve_lines,normalization_audit=normalization_audit,source=b.source,
                         literals=() if projection and projection.slots else literals,typed=b.role!='code' and not explicit_reading)
        if projection:
            spoken,context_spans=projection.finish(spoken,abbreviation_audit,normalization_audit)
        decisions.append({'block':b.id,'action':action,'reason':reason,'evidence':b.evidence,'rule':rule_revision,'destination':'source.html#'+b.id,'source':b.source,'spoken':spoken,'connector':connector,'section_policy':effective})
        if rule_revision in SOURCE_STRUCTURE:decisions[-1].update(chapter=block_chapter,chapter_occurrence=block_chapter_id or block_chapter)
        if normalization_audit:
            decisions[-1]['normalization']=normalization_audit
        if normalization_audit or context_spans:
            protected_readings[b.id]=(spoken,normalization_audit.get('edits',[])+[{'output_start':a,'output_end':z} for a,z in context_spans])
        if context_events:
            decisions[-1]['context_events']=context_events
            if any(row['view']['kind']=='prepared_reading' for row in context_events):decisions[-1]['context_input']=text
        if resolver and (structure:=resolver.structural_evidence(b,cells)):
            decisions[-1]['context_structure']=structure
        if damaged:decisions[-1]['source_characters']=damaged
        if (optional_reference or continuous and b.role=='furniture') and b.issue:decisions[-1]['source_warning']=b.issue
        if rule_revision in ('faithful-5','faithful-6','faithful-7','faithful-8','faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE):decisions[-1]['abbreviations']=abbreviation_audit
        if rule_revision in MODERN:
            from .boundaries import sentence_spans
            decisions[-1]['sentence_spans']=list(sentence_spans(spoken))
        if chapter!=block_chapter or chapter_id!=block_chapter_id:
            flush();flush_captions()
            if continuous and utterances:utterances[-1]['pause_samples']=36000
            chapter=block_chapter;chapter_id=block_chapter_id
        heading=continuous and (b.role=='heading' or b.id in heading_ids)
        if continuous and action=='unreadable':flush(hold_tail=rule_revision in ('faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE));flush_captions()
        placement=overrides.get(b.id,{}).get('placement')
        if placement is not None and (placement not in ('caption_after_paragraph','standalone') or action!='speak'):
            raise Error('delivery_policy','Caption or standalone placement requires a verified speech reading.')
        if continuous and placement=='caption_after_paragraph':
            captions.append((spoken,b.id));continue
        if continuous and placement=='standalone':flush();flush_captions()
        if heading:flush()
        if pause_marker:
            flush()
            if utterances and utterances[-1]['chapter']==chapter and utterances[-1].get('chapter_id')==chapter_id:
                utterances[-1]['pause_samples']=max(12000,utterances[-1]['pause_samples'])
                utterances[-1]['sources']=list(dict.fromkeys(utterances[-1]['sources']+[b.id]))
        if text:
            if preserve_lines:flush()
            if not continuous and sum(len(x.split()) for x in buffer)+len(text.split())>65: flush()
            if continuous and buffer and source_ids and re.search(r'[A-Za-z]{2,}-$',buffer[-1]) and b.locator.get('format')=='pdf':
                previous=next(x for x in doc.blocks if x.id==source_ids[-1])
                if previous.locator.get('page')!=b.locator.get('page'):
                    reason='Unresolved printed word split across source pages. Verify discretionary versus lexical hyphenation; no word is guessed.'
                    for decision in decisions:
                        if decision['block'] in (previous.id,b.id):decision.update(action='unreadable',reason=reason)
                    buffer.pop();source_ids.pop();flush(hold_tail=rule_revision in ('faithful-9','faithful-10','faithful-11',*SOURCE_STRUCTURE));flush_captions();after_source_gap=rule_revision in ('faithful-10','faithful-11',*SOURCE_STRUCTURE);continue
            buffer.append(spoken); source_ids.append(b.id)
            if preserve_lines:flush();continue
            if continuous and placement=='standalone':
                flush()
                if utterances:utterances[-1]['pause_samples']=8400
            if heading:
                flush()
                if utterances:utterances[-1]['pause_samples']=14400
            if continuous and anchor=='paragraph' and not heading and re.search(r'[.!?]["”’\')\]]*$',spoken):
                flush()
                if utterances:utterances[-1]['pause_samples']=8400
            if continuous and captions and re.search(r'[.!?]["”’\')\]]*$',spoken):
                flush_captions()
    flush();flush_captions()
    for i,u in enumerate(utterances): u['id']=f'u{i+1:05d}'
    issues=[{'block':d['block'],'location':doc.blocks[i].locator,'reason':d['reason'],'repair':f'Use repair --block {d["block"]} --text-file verified.txt'} for i,d in enumerate(decisions) if d['action']=='unreadable']
    for i,d in enumerate(decisions):
        for reading in d.get('abbreviations',{}).get('review',[]):
            if resolver and resolver.config['mode']=='automatic':continue
            issues.append({'block':d['block'],'location':doc.blocks[i].locator,'reason':reading['reason'],'abbreviation':reading['source'],'repair':f'Use repair --pronounce "{reading["source"]}=VERIFIED READING" or a source-bound document policy for this occurrence.'})
        held=[event for event in d.get('context_events',[]) if event.get('hold')]
        if held:
            issues.append({'block':d['block'],'location':doc.blocks[i].locator,'kind':'context',
                           'reason_code':'ambiguous_context','reason':'The selected strict reading policy requires a decision for ambiguous expressions in this source block.',
                           'occurrences':[event['occurrence_id'] for event in held],
                           'repair':'Use contextual settings for the applicable scope or supply validated candidate selections.'})
    for issue in flow_issues:
        location=next(b.locator for b in doc.blocks if b.id==issue['block'])
        issues.append({**issue,'location':location,'repair':'Use a verified reading for this exact source occurrence.'})
    catalog=None
    if 'chapters' in policy:
        if rule_revision not in SOURCE_STRUCTURE:raise Error('selection_rules','Chapter selection requires an explicit narration-rules upgrade.')
        from .selection import parse,project
        selected_chapters=parse(policy['chapters']);policy['chapters']=selected_chapters
        utterances,issues,catalog=project(decisions,utterances,issues,selected_chapters)
    words=sum(len(u['text'].split()) for u in utterances)
    result={'schema_version':1,'rule_revision':rule_revision,'source_sha256':doc.source_sha256,'policy':policy,'aliases':aliases or {},'decisions':decisions,'utterances':utterances,'issues':issues,'estimated_minutes':round(words/150,1),'estimate_basis':'150 words/minute; excludes unresolved material','chapters':[u['chapter'] for i,u in enumerate(utterances) if i==0 or (utterances[i-1]['chapter'],utterances[i-1].get('chapter_id'))!=(u['chapter'],u.get('chapter_id'))], 'warnings':doc.warnings}
    if document_title:result['document_title']=document_title
    if catalog is not None:result['chapter_catalog']=catalog
    if resolver:result['contextual_resolution']=resolver.report([event for row in decisions for event in row.get('context_events',[])])
    if rule_revision in SOURCE_STRUCTURE:
        from .review_grouping import source_groups
        result['review_groups']=source_groups(doc.json(),result)
    result['plan_sha256']=key(result)
    return result
