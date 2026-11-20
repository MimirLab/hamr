"""Model residency/lifecycle controls use tone speech and substitute ASR."""
import json
from pathlib import Path
import numpy as np
import pytest
from audiobook import jobs,pipeline
from audiobook.backend import ResidentEngineSession,validate_pcm
from audiobook.core import Error,RATE
from test_media import prepared
from test_pipeline import observer


def pending_book(tmp_path,monkeypatch):
    source=tmp_path/'book.md'
    source.write_text('\n\n'.join(f'Chapter {name}.\n\nThis is the complete {name} passage.' for name in ('Alpha','Beta','Gamma','Delta')))
    store,job=prepared(tmp_path,source)
    for k in {jobs.clip_key(u,job) for u in job['plan']['utterances']}:store.safe_remove(store.root/'clips'/k)
    observer(monkeypatch)
    return store,job


def engine_control(monkeypatch,fail_after=None):
    events=[];state={'live':False,'calls':0}
    class Engine:
        def __init__(self,*a):pass
        def __enter__(self):
            assert not state['live'];state['live']=True;events.append('start');return self
        def __exit__(self,*a):
            assert state['live'];state['live']=False;events.append('exit')
        def speech(self,text,voice,seed=42):
            assert state['live'];state['calls']+=1
            if fail_after is not None and state['calls']>fail_after:raise Error('controlled_failure','Resident test generation failed.')
            data=(np.sin(np.arange(RATE)*2*np.pi*440/RATE)*10000).astype('<i2').tobytes()
            return validate_pcm(data)
    monkeypatch.setattr(jobs,'Engine',Engine)
    return events,state


def test_model_stays_resident_across_batches_observations_and_rest(tmp_path,monkeypatch):
    store,job=pending_book(tmp_path,monkeypatch);events,state=engine_control(monkeypatch)
    observe=pipeline.qa_cache.observe;write=pipeline.writejson;rests=[]
    def observation(*args,**kwargs):
        assert state['live'];events.append('observe');return observe(*args,**kwargs)
    def checkpoint(path,data):
        if data['phase']=='resident_rest':
            assert state['live'];rests.append(data['new_takes_this_run'])
        write(path,data)
    monkeypatch.setattr(pipeline.qa_cache,'observe',observation)
    monkeypatch.setattr(pipeline,'writejson',checkpoint)
    result=pipeline.run(store,job['id'],Path('/unused'),Path('/unused'),batch_takes=2,resident=True,batch_rest_seconds=.01)
    assert result['status']=='ready' and events.count('start')==events.count('exit')==1
    assert events[0]=='start' and events[-1]=='exit' and len(rests)>=1 and not state['live']
    assert len(store.get(job['id'])['clips'])==len(job['plan']['utterances'])
    saved=json.loads((store.path(job)/'pipeline.json').read_text())
    assert saved['resident_engine_starts']==1 and saved['resident_model'] is True


def test_cached_resident_pipeline_never_starts_a_model(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('Entirely cached complete source.')
    store,job=prepared(tmp_path,source);observer(monkeypatch)
    def forbidden(*a):pytest.fail('Cached resident completion must remain model-free.')
    monkeypatch.setattr(jobs,'Engine',forbidden)
    assert pipeline.run(store,job['id'],Path('/unused'),Path('/unused'),resident=True)['status']=='ready'


def test_generation_failure_closes_resident_engine_and_retains_prior_commit(tmp_path,monkeypatch):
    store,job=pending_book(tmp_path,monkeypatch);events,state=engine_control(monkeypatch,fail_after=1)
    with pytest.raises(Error,match='Resident test generation failed'):
        pipeline.run(store,job['id'],Path('/unused'),Path('/unused'),batch_takes=1,resident=True)
    assert events.count('start')==events.count('exit')==1 and not state['live']
    current=store.get(job['id']);assert len(current['clips'])==1
    assert all(jobs.valid_clip(store,k) for k in current['clips'].values())
    assert not jobs.final_path(current).exists()


def test_user_pause_during_resident_rest_closes_engine_without_new_dispatch(tmp_path,monkeypatch):
    store,job=pending_book(tmp_path,monkeypatch);events,state=engine_control(monkeypatch)
    write=pipeline.writejson
    def checkpoint(path,data):
        write(path,data)
        if data['phase']=='resident_rest':
            current=store.get(job['id']);current.update(status='paused',error={'code':'user_paused'});store.save(current)
    monkeypatch.setattr(pipeline,'writejson',checkpoint)
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(store,job['id'],Path('/unused'),Path('/unused'),batch_takes=1,resident=True,batch_rest_seconds=1)
    assert state['calls']==1 and not state['live'] and events.count('exit')==1
    current=store.get(job['id']);assert current['status']=='paused' and len(current['clips'])==1


def test_resident_session_rejects_backend_drift_and_cleans_once(monkeypatch):
    events,state=engine_control(monkeypatch)
    with ResidentEngineSession(None,factory=jobs.Engine) as session:
        with session.use({'model':'first'}):pass
        with session.use({'model':'first'}):pass
        with pytest.raises(Error,match='configuration changed'):
            with session.use({'model':'other'}):pytest.fail('Changed model must not be used.')
        assert session.starts==1 and state['live']
    assert events==['start','exit']
    with pytest.raises(Error,match='explicitly restart'):
        with session.use({'model':'first'}):pytest.fail('Closed session must not auto-restart.')


@pytest.mark.parametrize('kwargs',[{'batch_rest_seconds':1},{'resident':True,'batch_rest_seconds':float('nan')},{'resident':True,'batch_rest_seconds':-1}])
def test_invalid_resident_budget_is_rejected_before_state_access(kwargs):
    with pytest.raises(Error):pipeline.run(None,'unused',Path('/unused'),Path('/unused'),**kwargs)
