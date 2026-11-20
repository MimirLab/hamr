import json
from pathlib import Path
import numpy as np
from test_media import prepared
from audiobook import jobs,pipeline
from audiobook.planner import plan
from audiobook.backend import validate_pcm


def test_critical_bad_first_take_is_repaired_before_later_clear_work_and_budget_survives_resume(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('Do not restart the process before the observer checks the final independent source reading.\n\nThe next clear paragraph stays in order.\n\nAn unresolved source region stays held.')
    s,j=prepared(tmp_path,source);j['document']['blocks'][-1]['issue']='Inspect the original source.'
    j['plan']=plan(jobs.document(j));s.save(j)
    for k in {jobs.clip_key(u,j) for u in j['plan']['utterances']}:s.safe_remove(s.root/'clips'/k)
    rendered=[]
    class Engine:
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def speech(self,text,voice,seed=42):
            rendered.append((text,seed));data=(np.sin(np.arange(6000)*2*np.pi*(440+seed)/24000)*10000).astype('<i2').tobytes();return validate_pcm(data)
    monkeypatch.setattr(jobs,'Engine',Engine)
    def observe(store,job,utterances,python,model):
        rows=[]
        for u in utterances:
            r=jobs.valid_clip(store,jobs.clip_key(u,job));text=u['text'].replace('not ','') if u.get('seed',42)==42 else u['text']
            rows.append({'utterance':u['id'],'text':text,'segments':[{'start':0,'end':r['seconds'],'text':text}],'audio_seconds':r['seconds'],'audio_samples':r['samples'],'pcm_sha256':r['sha256']})
        return rows,{'model_files':{'control':'Substitute ASR lifecycle control only'}},{}
    monkeypatch.setattr('audiobook.qa_cache.observe',observe)
    monkeypatch.setattr('audiobook.qa_cache.observer_identity',lambda *a:{'control':'fake'})
    args=(s,j['id'],Path('/unused'),Path('/first'))
    pipeline.run(*args,second_model=Path('/second'),prepare_clear=True,max_new_takes=1)
    current=s.get(j['id']);state=json.loads((s.path(j)/'pipeline.json').read_text())
    assert len(rendered)==1 and len(state['retakes'])==1
    assert state['observed_unique_takes']==0 and state['historical_observed_unique_takes']==1
    assert current['plan']['utterances'][0]['seed']==43
    assert not jobs.valid_clip(s,jobs.clip_key(current['plan']['utterances'][0],current))
    pipeline.run(*args,second_model=Path('/second'),prepare_clear=True,max_new_takes=1)
    assert rendered[1][0]==rendered[0][0] and rendered[1][1]==43
    current=s.get(j['id']);first=jobs.clip_key(current['plan']['utterances'][0],current);receipt=jobs.valid_clip(s,first)
    state=json.loads((s.path(j)/'pipeline.json').read_text());assert state['observed_unique_takes']==1 and state['historical_observed_unique_takes']==2
    pipeline.run(*args,second_model=Path('/second'),prepare_clear=True,max_new_takes=1)
    current=s.get(j['id']);state=json.loads((s.path(j)/'pipeline.json').read_text())
    assert len(rendered)==3 and len(state['retakes'])==1 and state['phase']=='source_review'
    assert jobs.valid_clip(s,first)==receipt and not jobs.final_path(current).exists()
