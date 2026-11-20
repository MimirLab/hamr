import json,subprocess
from pathlib import Path
from types import SimpleNamespace
import numpy as np,pytest
from audiobook.core import Store,Error,RATE,atomic,writejson,loadjson,key
from audiobook.jobs import create,clip_key,valid_clip,repair,cleanup,render
from audiobook.backend import validate_pcm
from audiobook.qa import check,missing_parenthetical_counts,accept
import audiobook.qa_cache as cache

@pytest.fixture
def case(tmp_path,monkeypatch):
    s=Store(tmp_path/'state'); source=tmp_path/'book.md'
    source.write_text('# First\n\nThere are students (57) and faculty (four).\n\n# Second\n\nDo not lose this condition.\n')
    j=s.get(create(s,source)['job']);j['backend']={'test':'not a real runtime'};j['voice']={'name':'test'};s.save(j)
    def clips():
        j=s.get(jid)
        for u in j['plan']['utterances']:
            k=clip_key(u,j);p=s.root/'clips'/k;p.mkdir(exist_ok=True)
            data=(np.sin(np.arange(RATE*3)*2*np.pi*200/RATE)*10000).astype('<i2').tobytes()
            _,receipt=validate_pcm(data);receipt['key']=k;atomic(p/'audio.pcm',data);writejson(p/'receipt.json',receipt)
    jid=j['id'];clips();profile={'model_files':{'model.bin':'fake control'},'worker':'control; no real speech'}
    monkeypatch.setattr(cache,'observer_identity',lambda *args:dict(profile))
    calls=[]
    def run(args,**kwargs):
        request=json.loads(kwargs['input']);calls.append(request)
        texts={u['id']:u['text'] for u in s.get(jid)['plan']['utterances']}
        rows=[{'utterance':c['utterance'],'text':texts[c['utterance']],
               'segments':[{'start':0,'end':2.9,'text':texts[c['utterance']],'avg_logprob':-.1}],'seconds':.1} for c in request['clips']]
        return SimpleNamespace(stdout=json.dumps({'rows':rows}).encode())
    monkeypatch.setattr(cache.subprocess,'run',run)
    return s,jid,profile,calls,run,clips

def qa(case,**kwargs):
    s,jid,*_=case
    return check(s,jid,Path('/not-executed'),Path('/not-executed-model'),all_clips=True,**kwargs)

def test_repeat_qa_reuses_only_raw_observations_and_rescores(case):
    s,jid,profile,calls,_,_=case;qa(case,require_review=True);first=len(calls);result=qa(case,require_review=True)
    assert len(calls)==first and result['flagged_passages']==0
    report=loadjson(s.path(s.get(jid))/'qa-r1.json')
    assert report['observation_checkpoints']['recognized_unique_takes']==0
    stored=next((s.root/'clips').glob('*/asr-*.json'))
    assert set(loadjson(stored)['observation'])=={'text','segments','seconds'}
    cleanup(s,apply=True);assert stored.exists()

def test_interrupted_batch_resumes_completed_observations(case,monkeypatch):
    s,jid,profile,calls,run,_=case;monkeypatch.setattr(cache,'BATCH_SIZE',1)
    count=0
    def fail_second(*args,**kwargs):
        nonlocal count
        count+=1
        if count==2:raise subprocess.CalledProcessError(1,args[0])
        return run(*args,**kwargs)
    monkeypatch.setattr(cache.subprocess,'run',fail_second)
    with pytest.raises(Error,match='retained'):qa(case)
    progress=loadjson(s.path(s.get(jid))/'qa-progress.json');assert progress['completed_unique_takes']==1
    assert progress['phase']=='interrupted_or_failed'
    monkeypatch.setattr(cache.subprocess,'run',run);before=len(calls);qa(case)
    assert len(calls)-before==len(s.get(jid)['plan']['utterances'])-1

def test_changed_observer_and_retake_invalidate_cache(case):
    s,jid,profile,calls,_,clips=case;qa(case);profile['worker']='new observer';before=len(calls);qa(case);assert len(calls)>before
    old=s.get(jid);repair(s,jid,retake=old['plan']['utterances'][0]['id']);clips();before=len(calls);qa(case)
    requested=[r for c in calls[before:] for r in c['clips']]
    assert len(requested)==1

def test_corrupt_observation_is_recomputed(case):
    s,jid,_,calls,_,_=case;qa(case);p=next((s.root/'clips').glob('*/asr-*.json'));p.write_text('{}');before=len(calls);qa(case)
    assert sum(len(c['clips']) for c in calls[before:])==1

def test_symlink_observation_is_never_followed_or_overwritten(case,tmp_path):
    s,jid,_,calls,_,_=case;qa(case);p=next((s.root/'clips').glob('*/asr-*.json'));p.unlink();sentinel=tmp_path/'sentinel';sentinel.write_text('untouched');p.symlink_to(sentinel)
    with pytest.raises(Error,match='symlink'):qa(case)
    assert sentinel.read_text()=='untouched' and p.is_symlink()

def test_parenthetical_count_cannot_hide_below_wer_threshold():
    assert missing_parenthetical_counts('The student body (57) and faculty (four).','The student body 57 and faculty.')==['four']
    assert missing_parenthetical_counts('Body (57) and faculty (four).','Body fifty-seven and faculty 4.')==[]
    assert missing_parenthetical_counts('Code (007) stays literal.','Code stays literal.')==[]
    assert missing_parenthetical_counts('(four) plus (four)','4')==['four']

def test_same_conditioning_key_with_different_pcm_requires_new_qa(case):
    import hashlib
    s,jid,_,_,_,_=case;qa(case,require_review=True);j=s.get(jid);u=j['plan']['utterances'][0];k=clip_key(u,j)
    p=s.root/'clips'/k/'audio.pcm';data=bytearray(p.read_bytes());data[2]^=1;atomic(p,bytes(data))
    receipt=loadjson(p.parent/'receipt.json');receipt['sha256']=hashlib.sha256(data).hexdigest();writejson(p.parent/'receipt.json',receipt)
    assert valid_clip(s,k)
    with pytest.raises(Error,match='physical takes changed'):accept(s,jid,u['id'],'Old QA is bound to different actual bytes.')
    repair(s,jid);held=render(s,jid)
    assert held['status']=='needs_review' and held['error']['code']=='speech_qa_pending'

def test_one_review_covers_identical_pcm_occurrences(case,monkeypatch):
    s,jid,_,_,run,_=case;j=s.get(jid);first=j['plan']['utterances'][0];second=j['plan']['utterances'][-1]
    j['plan']['utterances']=[first,{**first,'id':second['id']}];j['plan']['plan_sha256']=key(j['plan']);s.save(j)
    def uncertain(*args,**kwargs):
        result=run(*args,**kwargs);observed=json.loads(result.stdout)
        for r in observed['rows']:
            r['text']='wrong control observation';r['segments'][0]['text']=r['text']
        return SimpleNamespace(stdout=json.dumps(observed).encode())
    monkeypatch.setattr(cache.subprocess,'run',uncertain);qa(case,require_review=True)
    assert len(s.get(jid)['audio_review'])==2
    accept(s,jid,first['id'],'Synthetic review control, not a real voice-quality pass.')
    j=s.get(jid);assert not j['audio_review'] and len(j['audio_acceptances'])==1
    assert j['audio_acceptances'][0]['pcm_sha256']==valid_clip(s,clip_key(first,j))['sha256']

def test_impossible_asr_tail_is_preserved_but_not_counted_as_spoken(case,monkeypatch):
    s,jid,_,_,run,_=case
    def tail(*args,**kwargs):
        result=run(*args,**kwargs);observed=json.loads(result.stdout)
        for r in observed['rows']:
            r['segments'].append({'start':4,'end':8,'text':'not 999 impossible words','avg_logprob':-1})
            r['text']+=' not 999 impossible words'
        return SimpleNamespace(stdout=json.dumps(observed).encode())
    monkeypatch.setattr(cache.subprocess,'run',tail);qa(case,require_review=True)
    report=loadjson(s.path(s.get(jid))/'qa-r1.json')
    assert report['flagged_passages']==0 and report['raw_word_errors']>0
    assert all(r['ASR_segments_wholly_after_audio']==1 and r['observation_warnings'] for r in report['rows'])
    assert all('999' in r['text'] and '999' not in r['comparison_text'] for r in report['rows'])
