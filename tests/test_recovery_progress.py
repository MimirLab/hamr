from copy import deepcopy
import pytest
from test_media import prepared
from audiobook.core import writejson
from audiobook.jobs import summary,clip_key,repair


def test_orphaned_pipeline_reports_interruption_and_counts_only_current_observations(tmp_path):
    source=tmp_path/'source.md';source.write_text('The first complete sentence.\n\nThe next complete sentence.')
    store,j=prepared(tmp_path,source);old=clip_key(j['plan']['utterances'][0],j)
    repair(store,j['id'],retake=j['plan']['utterances'][0]['id']);j=store.get(j['id']);j['status']='rendering';store.save(j)
    current=clip_key(j['plan']['utterances'][0],j)
    writejson(store.path(j)/'pipeline.json',{'plan_sha256':j['plan']['plan_sha256'],'phase':'rendering','observed_unique_takes':3,'observed_pcm':{old:'prior-seed-recording',current:'current-seed-recording','unrelated':'another-old-recording'},'retakes':[]})
    before=deepcopy(j['plan']);result=summary(store,j)
    assert result['status']=='paused' and result['error']['code']=='worker_interrupted'
    assert result['pipeline_progress']['phase']=='interrupted' and result['pipeline_progress']['last_recorded_phase']=='rendering'
    assert result['pipeline_progress']['observed_unique_takes']==1 and result['pipeline_progress']['historical_observed_unique_takes']==3
    assert store.get(j['id'])['plan']==before


@pytest.mark.parametrize('status,code,checkpoint_phase,expected_phase',[
    ('failed','truncated_stream','rendering','stopped'),
    ('failed','publication_corrupt','complete','stopped'),
    ('cancelled',None,'observing','cancelled'),
    ('paused','render_batch_complete','resident_rest','resident_rest'),
    ('needs_review','source_review','source_review','source_review'),
])
def test_terminal_job_state_wins_over_stale_progress_without_rewriting_evidence(tmp_path,status,code,checkpoint_phase,expected_phase):
    source=tmp_path/'source.md';source.write_text('Keep this complete sentence and its source.')
    store,j=prepared(tmp_path,source);j['status']=status
    j['error']={'code':code,'message':'Retained terminal failure.'} if code else None
    store.save(j)
    path=store.path(j)/'pipeline.json'
    writejson(path,{'plan_sha256':j['plan']['plan_sha256'],'phase':checkpoint_phase,'error':None,'observed_pcm':{},'retakes':[]})
    checkpoint=path.read_bytes();payload=store.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]
    result=summary(store,j);progress=result['pipeline_progress']
    assert result['status']==status and progress['phase']==expected_phase
    if status in ('failed','cancelled'):
        assert progress['last_recorded_phase']==checkpoint_phase
        if code:assert progress['error']==j['error']['message']
    assert path.read_bytes()==checkpoint
    assert store.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]==payload
