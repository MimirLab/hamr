"""Human nonverbal findings cannot be cleared by a matching transcript."""
import json
import subprocess
import sys
from pathlib import Path
import pytest
from test_media import prepared
from audiobook.core import Error,atomic,writejson
from audiobook.backend import validate_pcm
from audiobook.jobs import clip_key,valid_clip,render,repair,cleanup
from audiobook.qa import check,flag,accept


def matching_observations(monkeypatch):
    def observe(store,job,utterances,python,model):
        rows=[]
        for u in utterances:
            r=valid_clip(store,clip_key(u,job))
            rows.append({'utterance':u['id'],'text':u['text'],'segments':[{'start':0,'end':r['seconds'],'text':u['text']}],
                         'audio_seconds':r['seconds'],'audio_samples':r['samples'],'pcm_sha256':r['sha256']})
        return rows,{'model_files':{'control':'Matching-text substitute; no real ASR claim.'}},{}
    monkeypatch.setattr('audiobook.qa_cache.observe',observe)


def test_flag_survives_perfect_asr_and_unrelated_repair_and_blocks_publication(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('The visible words may match while a voice adds a sound.')
    s,j=prepared(tmp_path,source);matching_observations(monkeypatch);u=j['plan']['utterances'][0]
    check(s,j['id'],Path('/unused'),Path('/unused'),require_review=True)
    flag(s,j['id'],u['id'],'An unwanted nonverbal sound interrupts the sentence.')
    repair(s,j['id'],metadata={'title':'A new display title'})
    result=check(s,j['id'],Path('/unused'),Path('/unused'),require_review=True)
    assert result['wer']==0 and result['status']=='needs_review' and result['flagged_passages']==1
    assert s.get(j['id'])['listening_findings'][0]['status']=='open'
    assert render(s,j['id'],allow_synthesis=False)['status']=='needs_review'


def test_reflag_revokes_prior_exact_pcm_approval_and_explicit_listening_can_resolve_it(tmp_path,monkeypatch):
    source=tmp_path/'book.md';source.write_text('A canary source passage.')
    s,j=prepared(tmp_path,source);matching_observations(monkeypatch);u=j['plan']['utterances'][0]
    flag(s,j['id'],u['id'],'Inspect a reported sound.')
    with pytest.raises(Error):accept(s,j['id'],u['id'],'Not enough current QA yet')
    check(s,j['id'],Path('/unused'),Path('/unused'),require_review=True)
    accept(s,j['id'],u['id'],'Listened to exact PCM; the reported sound was not present.')
    assert s.get(j['id'])['listening_findings'][0]['status']=='accepted'
    flag(s,j['id'],u['id'],'New listening observation for this exact PCM.')
    assert not s.get(j['id'])['audio_acceptances'] and s.get(j['id'])['revoked_audio_acceptances']
    assert check(s,j['id'],Path('/unused'),Path('/unused'),require_review=True)['status']=='needs_review'


def test_real_cli_records_bound_finding_and_invalid_target_does_not_mutate(tmp_path):
    source=tmp_path/'book.md';source.write_text('A canary source passage.')
    s,j=prepared(tmp_path,source);u=j['plan']['utterances'][0]
    command=[sys.executable,'-m','audiobook','--home',str(s.root),'review',j['id'],'--flag',u['id'],'--reason','Heard an added sound.','--json']
    result=subprocess.run(command,capture_output=True,text=True,check=True)
    assert json.loads(result.stdout)['status']=='needs_review'
    record=s.get(j['id'])['listening_findings'][0]
    assert record['pcm_sha256']==valid_clip(s,clip_key(u,j))['sha256']
    before=s.db.execute('select payload from jobs where id=?',(j['id'],)).fetchone()[0]
    with pytest.raises(Error):flag(s,j['id'],'u-does-not-exist','Invalid target')
    assert s.db.execute('select payload from jobs where id=?',(j['id'],)).fetchone()[0]==before


@pytest.mark.parametrize('same_key',[False,True])
def test_replacement_supersedes_old_finding_without_inheriting_human_approval(tmp_path,monkeypatch,same_key):
    source=tmp_path/'book.md';source.write_text('The source sentence remains exactly the same.')
    s,j=prepared(tmp_path,source);matching_observations(monkeypatch);u=j['plan']['utterances'][0]
    flag(s,j['id'],u['id'],'Reported a nonverbal sound in the original recording.')
    original=s.get(j['id'])['listening_findings'][0]['pcm_sha256']
    if not same_key:repair(s,j['id'],retake=u['id'])
    current=s.get(j['id']);k=clip_key(current['plan']['utterances'][0],current)
    pcm=(b'\x10\x27\xf0\xd8')*6000
    _,receipt=validate_pcm(pcm);receipt['key']=k
    atomic(s.root/'clips'/k/'audio.pcm',pcm);writejson(s.root/'clips'/k/'receipt.json',receipt)
    result=check(s,j['id'],Path('/unused'),Path('/unused'),require_review=True)
    finding=s.get(j['id'])['listening_findings'][0]
    assert finding['status']=='superseded' and finding['pcm_sha256']==original
    assert not s.get(j['id']).get('audio_acceptances') and result['flagged_passages']==0
    from audiobook.jobs import summary
    assert summary(s,s.get(j['id']))['listening_findings']==[finding]


def test_cleanup_keeps_reported_bad_audio_until_its_owning_job_is_purged(tmp_path):
    source=tmp_path/'book.md';source.write_text('Retain the reported recording for comparison.')
    s,j=prepared(tmp_path,source);u=j['plan']['utterances'][0];old=s.root/'clips'/clip_key(u,j)
    flag(s,j['id'],u['id'],'Reported a nonverbal artifact.')
    repair(s,j['id'],retake=u['id'])
    assert str(old) not in cleanup(s)['candidates']
    assert str(old) in cleanup(s,purge_job=j['id'])['candidates']
