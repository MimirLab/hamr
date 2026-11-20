"""Rest scheduling controls: actual pipeline, tones, substitute CPU observations.

The clock advances deterministically; these are not GPU stability measurements.
"""
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from audiobook import jobs,pipeline
from audiobook.core import Error
from test_resident_pipeline import pending_book,engine_control


class Clock:
    def __init__(self):self.now=0.;self.on_sleep=lambda:None
    def monotonic(self):return self.now
    def time(self):return 1000+self.now
    def sleep(self,seconds):self.now+=seconds;self.on_sleep()


def timed_pipeline(tmp_path,monkeypatch,review_seconds=8):
    store,job=pending_book(tmp_path,monkeypatch)
    events,state=engine_control(monkeypatch);clock=Clock();dispatch=[];rests=[]
    speech=jobs.Engine.speech;observe=pipeline.qa_cache.observe;write=pipeline.writejson
    def generate(*args,**kwargs):
        dispatch.append(clock.now);result=speech(*args,**kwargs);clock.now+=3;return result
    def observation(*args,**kwargs):
        assert state['live'];clock.now+=review_seconds;return observe(*args,**kwargs)
    def checkpoint(path,data):
        if data['phase']=='resident_rest':rests.append(dict(data))
        write(path,data)
    monkeypatch.setattr(jobs.Engine,'speech',generate)
    monkeypatch.setattr(pipeline.qa_cache,'observe',observation)
    monkeypatch.setattr(pipeline,'writejson',checkpoint)
    monkeypatch.setattr(pipeline,'time',clock)
    return SimpleNamespace(store=store,job=job,clock=clock,dispatch=dispatch,rests=rests,events=events)


@pytest.mark.parametrize('overlap,review,expected_gap,credit',[(False,8,41,0),(True,8,33,8),(True,35,38,30)])
def test_review_overlaps_only_when_requested_and_keeps_minimum_rest(tmp_path,monkeypatch,overlap,review,expected_gap,credit):
    c=timed_pipeline(tmp_path,monkeypatch,review)
    result=pipeline.run(c.store,c.job['id'],Path('/unused'),Path('/unused'),resident=True,
                        batch_rest_seconds=30,overlap_batch_rest=overlap,max_new_takes=2)
    assert result['status']=='paused' and len(c.dispatch)==2
    assert c.dispatch[1]-c.dispatch[0]==pytest.approx(expected_gap)
    # The three-second complete generation precedes all credited review/rest.
    assert c.dispatch[1]-c.dispatch[0]-3>=30
    assert c.rests[0]['post_render_seconds_counted_as_rest']==credit
    assert c.events.count('start')==c.events.count('exit')==1
    current=c.store.get(c.job['id'])
    assert len(current['clips'])==2 and all(jobs.valid_clip(c.store,k) for k in current['clips'].values())
    assert not jobs.final_path(current).exists()


def test_unchanged_rest_avoids_redecoding_job_on_each_poll(tmp_path,monkeypatch):
    c=timed_pipeline(tmp_path,monkeypatch);get=c.store.get;rest_reads=[]
    def read(jid):
        if c.rests:rest_reads.append(c.clock.now)
        return get(jid)
    monkeypatch.setattr(c.store,'get',read)
    pipeline.run(c.store,c.job['id'],Path('/unused'),Path('/unused'),resident=True,
                 batch_rest_seconds=30,overlap_batch_rest=True,max_new_takes=2)
    # Full checks bookend the wait; quarter-second polls do not parse the book.
    assert sum(11<=t<33 for t in rest_reads)==1


@pytest.mark.parametrize('external',[False,True])
def test_change_token_catches_pause_even_with_unchanged_timestamp(tmp_path,monkeypatch,external):
    c=timed_pipeline(tmp_path,monkeypatch);changed=False
    def pause():
        nonlocal changed
        if changed:return
        changed=True
        db=sqlite3.connect(c.store.root/'state.sqlite') if external else c.store.db
        row=db.execute('SELECT payload,updated FROM jobs WHERE id=?',(c.job['id'],)).fetchone()
        job=json.loads(row[0]);job.update(status='paused',error={'code':'user_paused'})
        with db:db.execute('UPDATE jobs SET payload=?,status=? WHERE id=?',(json.dumps(job),'paused',job['id']))
        assert db.execute('SELECT updated FROM jobs WHERE id=?',(job['id'],)).fetchone()[0]==row[1]
        if external:db.close()
    c.clock.on_sleep=pause
    with pytest.raises(KeyboardInterrupt):
        pipeline.run(c.store,c.job['id'],Path('/unused'),Path('/unused'),resident=True,
                     batch_rest_seconds=30,overlap_batch_rest=True,max_new_takes=2)
    assert len(c.dispatch)==1 and c.events.count('exit')==1
    assert c.clock.now==11.25  # Detected on the very next polling iteration.


def test_code_change_during_unchanged_database_rest_still_stops(tmp_path,monkeypatch):
    c=timed_pipeline(tmp_path,monkeypatch);filehash=pipeline.filehash;changed=False
    def changed_hash(path):return 'changed' if changed else filehash(path)
    def edit():
        nonlocal changed
        changed=True
    monkeypatch.setattr(pipeline,'filehash',changed_hash);c.clock.on_sleep=edit
    with pytest.raises(Error) as exc:
        pipeline.run(c.store,c.job['id'],Path('/unused'),Path('/unused'),resident=True,
                     batch_rest_seconds=30,overlap_batch_rest=True,max_new_takes=2)
    assert exc.value.code=='pipeline_code_changed' and len(c.dispatch)==1 and c.events.count('exit')==1


@pytest.mark.parametrize('options',[{'overlap_batch_rest':True},{'overlap_batch_rest':True,'resident':True},
                                  {'overlap_batch_rest':1,'resident':True,'batch_rest_seconds':30}])
def test_invalid_overlap_settings_rejected_before_state_access(options):
    with pytest.raises(Error):pipeline.run(None,'unused',Path('/unused'),Path('/unused'),**options)
