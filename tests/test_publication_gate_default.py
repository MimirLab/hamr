from copy import deepcopy
from test_media import prepared
from audiobook import jobs


def test_new_job_cannot_publish_cached_signal_without_required_speech_review(tmp_path):
    source=tmp_path/'book.md';source.write_text('The complete source sentence stays intact.')
    s,control=prepared(tmp_path,source)
    (tmp_path/'new-output').mkdir()
    new=s.get(jobs.create(s,source,output=tmp_path/'new-output')['job'])
    assert new['speech_contract']==2 and new['audio_gate'] is True
    new['voice']=control['voice'];new['backend']=control['backend'];s.save(new)
    result=jobs.render(s,new['id'],allow_synthesis=False)
    assert result['status']=='needs_review' and not jobs.final_path(new).exists()
    assert not s.get(new['id']).get('audio_gate_approved_plan')


def test_validation_contract_separates_new_recordings_without_rewriting_legacy_keys(tmp_path):
    source=tmp_path/'book.md';source.write_text('The same request still has distinct validation provenance.')
    s,new=prepared(tmp_path,source);legacy=deepcopy(new);legacy.pop('speech_contract')
    u=new['plan']['utterances'][0]
    assert jobs.clip_key(u,new)!=jobs.clip_key(u,legacy)
    legacy['speech_contract']=1
    assert jobs.clip_key(u,legacy)==jobs.clip_key(u,{k:v for k,v in legacy.items() if k!='speech_contract'})


def test_new_comparison_policy_requires_rechecking_saved_pcm_before_publication(tmp_path):
    from audiobook.speech_compare import POLICY
    source=tmp_path/'book.md';source.write_text('A preserved recording still needs the current verification policy.')
    s,j=prepared(tmp_path,source)
    j['clips']={u['id']:jobs.clip_key(u,j) for u in j['plan']['utterances']};j['audio_gate']=True
    j['audio_gate_approved_plan']=j['plan']['plan_sha256'];j['audio_gate_approved_takes']=jobs.take_signature(s,j)
    j['audio_gate_approved_comparison_policy']='older comparison';s.save(j)
    before={k:jobs.valid_clip(s,k)['sha256'] for k in j['clips'].values()}
    result=jobs.render(s,j['id'],allow_synthesis=False)
    assert result['status']=='needs_review' and not jobs.final_path(j).exists()
    assert all(jobs.valid_clip(s,k)['sha256']==sha for k,sha in before.items())
    # Test-only signal fixture: no ASR or voice acceptance is asserted here.
    j=s.get(j['id']);j['audio_gate_approved_comparison_policy']=POLICY;s.save(j)
    assert jobs.render(s,j['id'],allow_synthesis=False)['status']=='ready'
