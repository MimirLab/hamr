"""Pipeline lifecycle controls use tones/substitute ASR, not voice certification."""
import json
from pathlib import Path
import numpy as np
import pytest
from test_media import prepared
from test_listening_findings import matching_observations
from audiobook import jobs,pipeline
from audiobook.core import RATE,Error
from audiobook.backend import validate_pcm
from audiobook.qa import flag


def observer(monkeypatch):
    matching_observations(monkeypatch)
    monkeypatch.setattr('audiobook.qa_cache.observer_identity',lambda *a:{'model_files':{'control':'Substitute ASR control only'}})


def test_cached_pipeline_runs_required_qa_before_real_ffmpeg_publication(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('First complete source sentence.\n\nLast complete source sentence.')
    s,j=prepared(tmp_path,source);observer(monkeypatch)
    def forbidden(*a):raise AssertionError('Cached completion must not start an engine.')
    monkeypatch.setattr(jobs,'Engine',forbidden)
    result=pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    assert result['status']=='ready' and s.get(j['id'])['audio_gate_approved_takes']==jobs.take_signature(s,s.get(j['id']))
    assert result['pipeline_progress']['phase']=='complete' and result['pipeline_progress']['remaining_unique_takes']==0
    state=json.loads((s.path(j)/'pipeline.json').read_text());assert state['phase']=='complete' and not state['retakes']
    assert state['remaining_unique_takes']==0 and state['pending_unique_observations']==0


def test_bounded_pipeline_resumes_cached_work_and_observations_without_resetting_budget_history(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('First complete source sentence.\n\nLast complete source sentence.')
    s,j=prepared(tmp_path,source);observer(monkeypatch)
    for k in {jobs.clip_key(u,j) for u in j['plan']['utterances']}:s.safe_remove(s.root/'clips'/k)
    calls=[]
    class Engine:
        def __init__(self,*a):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def speech(self,text,voice,seed=42):
            calls.append(text);data=(np.sin(np.arange(RATE)*2*np.pi*440/RATE)*10000).astype('<i2').tobytes();return validate_pcm(data)
    monkeypatch.setattr(jobs,'Engine',Engine)
    first=pipeline.run(s,j['id'],Path('/unused'),Path('/unused'),max_new_takes=1)
    assert first['status']=='paused' and len(calls)==1 and not jobs.final_path(s.get(j['id'])).exists()
    first_pcm={k:jobs.valid_clip(s,k)['sha256'] for k in s.get(j['id'])['clips'].values()}
    second=pipeline.run(s,j['id'],Path('/unused'),Path('/unused'),max_new_takes=1)
    assert second['status']=='ready' and len(calls)==2
    assert all(jobs.valid_clip(s,k)['sha256']==sha for k,sha in first_pcm.items())
    state=json.loads((s.path(j)/'pipeline.json').read_text())
    assert len(state['runs'])==2 and state['remaining_unique_takes']==0


@pytest.mark.parametrize('prepare_clear',[False,True])
def test_incomplete_source_never_publishes_even_after_clear_work_is_prepared(tmp_path,monkeypatch,prepare_clear):
    source=tmp_path/'book.md';source.write_text('Clear source text.')
    s,j=prepared(tmp_path,source);j['plan']['issues']=[{'block':'unresolved','reason':'Missing essential source.'}];s.save(j);observer(monkeypatch)
    result=pipeline.run(s,j['id'],Path('/unused'),Path('/unused'),prepare_clear=prepare_clear)
    assert result['status']=='needs_review' and result['error']['code']=='source_review'
    assert not jobs.final_path(s.get(j['id'])).exists()


def test_perfect_substitute_asr_does_not_clear_a_recorded_listening_finding(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('The words can match despite an added sound.')
    s,j=prepared(tmp_path,source);observer(monkeypatch);flag(s,j['id'],j['plan']['utterances'][0]['id'],'Reported a nonverbal artifact.')
    result=pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    assert result['status']=='needs_review' and s.get(j['id'])['listening_findings'][0]['status']=='open'
    assert not jobs.final_path(s.get(j['id'])).exists()


def test_model_configuration_change_invalidates_partial_observation_bookkeeping(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('Clear source text.')
    s,j=prepared(tmp_path,source);j['plan']['issues']=[{'block':'unresolved','reason':'Missing essential source.'}];s.save(j);observer(monkeypatch)
    path=s.path(j)/'pipeline.json';path.write_text(json.dumps({'retakes':[{'identity':'preserved-old-budget'}],'observed_pcm':{'fake':'stale'},'runs':[],'binding':pipeline.binding(j),'observer_sha256':'different-model','error':'Previous interrupted attempt'}))
    pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    state=json.loads(path.read_text());assert not state['observed_pcm'] and state['retakes']==[{'identity':'preserved-old-budget'}]
    assert state['error'] is None


def test_user_pause_between_observation_and_publication_is_not_overwritten(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('Clear source text.')
    s,j=prepared(tmp_path,source);observer(monkeypatch);write=pipeline.writejson
    def after_observation(path,data):
        write(path,data)
        if data.get('phase')=='observations_checkpointed':
            with s.lock():
                current=s.get(j['id']);current['status']='paused';current['error']={'code':'user_paused'};s.save(current)
    monkeypatch.setattr(pipeline,'writejson',after_observation)
    with pytest.raises(KeyboardInterrupt):pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    assert s.get(j['id'])['status']=='paused' and not jobs.final_path(s.get(j['id'])).exists()


def test_external_seed_repair_between_observation_and_publication_stops_the_run(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('Clear source text.')
    s,j=prepared(tmp_path,source);observer(monkeypatch);write=pipeline.writejson
    def after_observation(path,data):
        write(path,data)
        if data.get('phase')=='observations_checkpointed':jobs.repair(s,j['id'],retake='u00001')
    monkeypatch.setattr(pipeline,'writejson',after_observation)
    with pytest.raises(Error,match='changed outside this run'):pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    assert not jobs.final_path(s.get(j['id'])).exists()


def test_real_writer_contention_between_rendering_and_observation_waits_and_preserves_pcm(tmp_path,monkeypatch):
    import threading,time
    source=tmp_path/'book.md';source.write_text('The committed source words remain intact.')
    s,j=prepared(tmp_path,source);observer(monkeypatch);write=pipeline.writejson
    before={k:jobs.valid_clip(s,k)['sha256'] for k in j['clips'].values()};held=threading.Event();threads=[];phases=[]
    def occupy():
        with s.lock():held.set();time.sleep(.3)
    def checkpoint(path,data):
        phases.append(data['phase']);write(path,data)
        if data['phase']=='observing' and data.get('pending_unique_observations') and not threads:
            t=threading.Thread(target=occupy);threads.append(t);t.start();assert held.wait(2)
    monkeypatch.setattr(pipeline,'writejson',checkpoint)
    def forbidden(*a):raise AssertionError('Writer contention must reuse committed speech.')
    monkeypatch.setattr(jobs,'Engine',forbidden)
    try:result=pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    finally:
        for t in threads:t.join(2)
    assert result['status']=='ready' and 'waiting_for_writer' in phases
    assert all(jobs.valid_clip(s,k)['sha256']==sha for k,sha in before.items())


def test_repair_after_publication_before_return_cannot_report_stale_ready_state(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('Clear source text.')
    s,j=prepared(tmp_path,source);observer(monkeypatch);write=pipeline.writejson
    def checkpoint(path,data):
        write(path,data)
        if data['phase']=='complete':jobs.repair(s,j['id'],retake='u00001')
    monkeypatch.setattr(pipeline,'writejson',checkpoint)
    with pytest.raises(Error,match='changed outside this run'):pipeline.run(s,j['id'],Path('/unused'),Path('/unused'))
    current=s.get(j['id']);assert current['revision']==2 and current['status']=='queued'
    assert jobs.final_path(j).exists() and not jobs.final_path(current).exists()
