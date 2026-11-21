import json,os,subprocess,sys
from pathlib import Path
import numpy as np,pytest
from audiobook.core import Store,Error,RATE,atomic,key,writejson,digest
from audiobook.jobs import create,render,repair,cleanup,rename_new,timeline,clip_key,valid_clip,take_signature
from audiobook.backend import validate_pcm

@pytest.fixture
def job(tmp_path):
    s=Store(tmp_path/'state'); source=tmp_path/'book.md'; source.write_text('# First\n\nKeep 0.08 mg. Do not restart.\n\n# Last\n\nThe final passage remains unchanged.\n')
    output=tmp_path/'outputs'; output.mkdir(); r=create(s,source,output=output,policy={'document':{'source_sha256':digest(source.read_bytes()),'delivery':{'flow':'segmented'}}}); j=s.get(r['job']); j['backend']={'test':'signal-control-flow-only'}; j['voice']={'name':'synthetic-control-flow','test':'no real voice'};j['audio_gate']=False; s.save(j)
    for u in j['plan']['utterances']:
        k=clip_key(u,j); p=s.root/'clips'/k; p.mkdir(exist_ok=True)
        samples=np.sin(np.arange(RATE*3)*2*np.pi*200/RATE)*10000; data=samples.astype('<i2').tobytes(); _,receipt=validate_pcm(data)
        receipt.update(key=k); atomic(p/'audio.pcm',data); writejson(p/'receipt.json',receipt)
    return s,j,source

def test_snapshot_path_replacement(job):
    s,j,source=job; source.write_text('Replaced source at same path'); assert Path(j['snapshot']).read_text().startswith('# First')

def test_publish_and_idempotent_resume(job):
    s,j,_=job; a=render(s,j['id']); assert a['status']=='ready'; b=render(s,j['id']); assert b['output']==a['output']; assert b['metrics']['clips_rendered']==0
    companion=json.loads((Path(a['output'])/'companion.json').read_text()); assert companion['chapters'][1]['start_sample']>companion['chapters'][0]['start_sample']

def test_cpu_only_resume_refuses_missing_take_before_engine(job,monkeypatch):
    import audiobook.jobs as module
    s,j,_=job;k=clip_key(j['plan']['utterances'][0],j);s.safe_remove(s.root/'clips'/k)
    def forbidden(*args):raise AssertionError('CPU-only path must never initialize the speech backend')
    monkeypatch.setattr(module,'Engine',forbidden)
    with pytest.raises(Error,match='CPU-only resume'):render(s,j['id'],allow_synthesis=False)
    assert s.get(j['id'])['error']['code']=='synthesis_required'

def test_cpu_only_resume_assembles_cached_speech_without_engine(job,monkeypatch):
    import audiobook.jobs as module
    s,j,_=job
    def forbidden(*args):raise AssertionError('CPU-only path must never initialize the speech backend')
    monkeypatch.setattr(module,'Engine',forbidden)
    assert render(s,j['id'],allow_synthesis=False)['status']=='ready'


def test_oversized_missing_request_is_located_before_backend_initialization(job,monkeypatch):
    import audiobook.jobs as module
    s,j,_=job;kept=clip_key(j['plan']['utterances'][-1],j);before=valid_clip(s,kept)['sha256']
    target=j['plan']['utterances'][0];target['text']='x'*6000;s.save(j)
    def forbidden(*args):raise AssertionError('Input guard must run before GPU startup')
    monkeypatch.setattr(module,'Engine',forbidden)
    with pytest.raises(Error) as error:render(s,j['id'])
    assert error.value.code=='speech_input_limit'
    assert s.get(j['id'])['failed_take']['utterance']==target['id']
    assert valid_clip(s,kept)['sha256']==before


def test_render_budget_closes_engine_and_resumes_without_repeating_completed_takes(job,monkeypatch):
    import audiobook.jobs as module
    s,j,_=job
    cached=next((s.root/'clips').iterdir());data=(cached/'audio.pcm').read_bytes();receipt=json.loads((cached/'receipt.json').read_text())
    for k in {clip_key(u,j) for u in j['plan']['utterances']}:s.safe_remove(s.root/'clips'/k)
    events=[]
    class Fake:
        def __init__(self,*args):pass
        def __enter__(self):events.append('enter');return self
        def __exit__(self,*args):events.append('exit')
        def speech(self,text,voice,seed=42):events.append(text);return data,dict(receipt)
    monkeypatch.setattr(module,'Engine',Fake)
    first=render(s,j['id'],max_new_takes=1)
    assert first['status']=='paused' and first['error']['code']=='render_batch_complete'
    assert first['progress']['unique_takes_rendered_this_run']==1
    assert events[0]=='enter' and events[-1]=='exit'
    kept={k:valid_clip(s,k)['sha256'] for k in s.get(j['id'])['clips'].values()}
    second=render(s,j['id'],max_new_takes=1)
    assert second['status']=='ready' and second['metrics']['clips_rendered']==1
    assert all(valid_clip(s,k)['sha256']==sha for k,sha in kept.items())
    assert events.count('enter')==events.count('exit')==2


def test_invalid_render_budget_does_not_change_job(job):
    s,j,_=job;before=s.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]
    with pytest.raises(Error,match='new takes'):render(s,j['id'],max_new_takes=0)
    assert s.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]==before

@pytest.mark.parametrize('point',['during_assembly','before_publish_rename','after_publish_rename','before_ready_db','after_ready_db'])
def test_crash_commit_boundaries(job,point):
    s,j,_=job
    code='from audiobook.core import Store; from audiobook.jobs import render; import sys; render(Store(sys.argv[1]),sys.argv[2])'
    r=subprocess.run([sys.executable,'-c',code,str(s.root),j['id']],env={**os.environ,'AUDIOBOOK_FAULT':point}); assert r.returncode==86
    result=render(s,j['id']); assert result['status']=='ready'
    assert len(list(Path(j['output_parent']).glob(j['id']+'-r*')))==1

def test_corrupt_ready_does_not_stay_ready(job):
    from audiobook.jobs import summary
    s,j,_=job; a=render(s,j['id']); (Path(a['output'])/'book.m4b').unlink(); assert summary(s,s.get(j['id']))['status']=='failed'

def test_no_clobber_directory_race(tmp_path):
    a=tmp_path/'a'; b=tmp_path/'b'; a.mkdir(); b.mkdir()
    with pytest.raises(Error,match='exists'): rename_new(a,b)
    assert a.exists() and b.exists()

def test_repair_reuses_unaffected_clip(job):
    s,j,_=job; render(s,j['id']); old=[clip_key(u,j) for u in j['plan']['utterances']]; repair(s,j['id'],alias='0.08=zero point zero eight'); new=s.get(j['id']); newkeys=[clip_key(u,new) for u in new['plan']['utterances']]
    assert old[0]!=newkeys[0] and old[-1]==newkeys[-1] and valid_clip(s,newkeys[-1])


def test_abbreviation_upgrade_changes_only_affected_keys_and_invalidates_qa(job):
    from audiobook.planner import plan,RULES
    from audiobook.jobs import document
    s,j,_=job
    doc=document(j);doc.blocks[0].source='Dr. Henry spoke.'
    j['document']=doc.json();j['plan']=plan(doc,j['plan']['policy'],rule_revision='faithful-4')
    j['audio_gate']=True;j['audio_gate_checked_plan']=j['plan']['plan_sha256']
    j['audio_gate_approved_plan']=j['plan']['plan_sha256'];j['audio_gate_approved_takes']='old'
    j['audio_review']=[{'utterance':'u00001','flags':['old positional review']}]
    data=next((s.root/'clips').glob('*/audio.pcm')).read_bytes()
    for u in j['plan']['utterances']:
        k=clip_key(u,j);_,receipt=validate_pcm(data);receipt['key']=k
        atomic(s.root/'clips'/k/'audio.pcm',data);writejson(s.root/'clips'/k/'receipt.json',receipt)
    oldkeys=[clip_key(u,j) for u in j['plan']['utterances']];s.save(j)
    repair(s,j['id'],upgrade_rules=True)
    new=s.get(j['id']);newkeys=[clip_key(u,new) for u in new['plan']['utterances']]
    assert new['plan']['rule_revision']==RULES
    assert oldkeys[0]!=newkeys[0] and oldkeys[-1]==newkeys[-1]
    assert valid_clip(s,newkeys[-1]) and not valid_clip(s,newkeys[0])
    assert new['document']==j['document'] and new['voice']==j['voice']
    assert new['audio_gate'] and new['audio_gate_approved_plan'] is None
    assert new['audio_gate_approved_takes'] is None and new['audio_review']==[]

def test_cleanup_protects_symlink_and_pins(job,tmp_path):
    s,j,_=job; outside=tmp_path/'sentinel'; outside.mkdir(); (outside/'keep').write_text('keep'); (s.root/'clips'/'symlink').symlink_to(outside,target_is_directory=True)
    cleanup(s,apply=True); assert (outside/'keep').read_text()=='keep'; assert (s.root/'clips'/'symlink').is_symlink()
    assert all(valid_clip(s,clip_key(u,j)) for u in j['plan']['utterances'])

def test_duplicate_writer_and_gc(job):
    s,j,_=job
    with s.lock():
        with pytest.raises(Error,match='writer'): cleanup(s,apply=True)

def test_long_book_64bit_samples():
    us=[{'id':'x','text':'x','sources':['b'],'chapter':'day one','pause_samples':0},{'id':'y','text':'y','sources':['c'],'chapter':'day two','pause_samples':0}]
    rows,chapters,total=timeline(us,[{'samples':RATE*3600*30},{'samples':RATE*3600*30}]); assert total==RATE*3600*60 and total>2**32 and rows[1]['start']==108000

@pytest.mark.parametrize('data',[b'',b'\x00',b'\x00\x00'*24000,b'\xff\x7f'*24000])
def test_bad_pcm(data):
    with pytest.raises(Error): validate_pcm(data)

def test_missing_output_mount_is_recoverable(job):
    s,j,_=job; output=Path(j['output_parent']); output.rename(output.with_name('disconnected'))
    with pytest.raises(Error,match='unavailable') as failure: render(s,j['id'])
    assert str(output) in failure.value.message
    assert s.get(j['id'])['status']=='failed' and not output.exists()
    output.with_name('disconnected').rename(output); assert render(s,j['id'])['status']=='ready'

def test_enospc_after_clip_checkpoint(job,monkeypatch):
    import audiobook.jobs as module
    s,j,_=job; original=module.publish
    def fail(*args): raise OSError(28,'No space left on device',j['output_parent'])
    monkeypatch.setattr(module,'publish',fail)
    with pytest.raises(Error,match='No space left on device') as failure: render(s,j['id'])
    assert j['output_parent'] in failure.value.message
    assert s.get(j['id'])['status']=='failed'
    assert all(valid_clip(s,clip_key(u,j)) for u in j['plan']['utterances'])
    monkeypatch.setattr(module,'publish',original); assert render(s,j['id'])['status']=='ready'

def test_newer_state_rejected(job):
    s,j,_=job
    s.db.execute('PRAGMA user_version=999'); s.db.commit()
    with pytest.raises(Error,match='newer'): Store(s.root)

def test_persisted_edits_voice_and_schema_reopen(job):
    s,j,_=job; repair(s,j['id'],alias='mg=milligrams'); s.db.close(); reopened=Store(s.root); saved=reopened.get(j['id']); assert saved['plan']['aliases']=={'mg':'milligrams'} and saved['voice']==j['voice']

def test_oom_failure_retains_completed_work(job,monkeypatch):
    import audiobook.jobs as module
    s,j,_=job; k=clip_key(j['plan']['utterances'][-1],j); s.safe_remove(s.root/'clips'/k)
    class OOM:
        def __init__(self,*args): pass
        def __enter__(self): raise Error('device_oom','Device out of memory.','Free GPU memory and resume.')
        def __exit__(self,*args): pass
    monkeypatch.setattr(module,'Engine',OOM)
    with pytest.raises(Error,match='memory'): render(s,j['id'])
    assert s.get(j['id'])['error']['code']=='device_oom'; assert valid_clip(s,clip_key(j['plan']['utterances'][0],j))

def test_targeted_retake_changes_one_conditioning_key(job):
    s,j,_=job; old=[clip_key(u,j) for u in j['plan']['utterances']]
    repair(s,j['id'],retake='u00001'); changed=s.get(j['id']); keys=[clip_key(u,changed) for u in changed['plan']['utterances']]
    assert old[0]!=keys[0] and old[1:]==keys[1:]
    assert changed['plan']['utterances'][0]['seed']==43
    repair(s,j['id'],alias='absent=unrelated'); again=s.get(j['id']); assert again['plan']['utterances'][0]['seed']==43

def test_companion_links_resolve_and_source_matches(job):
    from bs4 import BeautifulSoup
    from audiobook.core import filehash
    s,j,_=job; result=render(s,j['id']); out=Path(result['output']); companion=json.loads((out/'companion.json').read_text()); soup=BeautifulSoup((out/'source.html').read_text(),'html.parser')
    assert filehash(out/companion['original_source'])==j['document']['source_sha256']
    for d in companion['decisions']: assert soup.find(id=d['destination'].split('#')[1]) is not None
    for row in companion['segments']: assert soup.find(id=row['destination'].split('#')[1]) is not None

def test_cleanup_preserves_unowned_file(job):
    s,j,_=job; sentinel=s.root/'clips'/'unrelated-sentinel.txt'; sentinel.write_text('protected'); result=cleanup(s,apply=True); assert sentinel.read_text()=='protected'; assert str(sentinel) in result['skipped_unowned_or_symlink']

def test_safe_remove_refuses_symlink_ancestor(job,tmp_path):
    s,j,_=job; outside=tmp_path/'outside'; outside.mkdir(); (outside/'keep').write_text('protected'); (s.root/'jobs'/'other').symlink_to(outside,target_is_directory=True)
    with pytest.raises(Error): s.safe_remove(s.root/'jobs'/'other'/'keep')
    assert (outside/'keep').read_text()=='protected'

def test_stale_diagnostic_cannot_overwrite_newer_job(job):
    s,j,_=job; stale=s.get(j['id']); repair(s,j['id'],alias='mg=milligrams'); current=s.get(j['id']); stale['status']='failed'
    assert not s.save_if_current(stale,stale['updated'])
    assert s.get(j['id'])['revision']==current['revision'] and s.get(j['id'])['status']=='queued'

def test_cleanup_crash_after_first_item_is_recoverable(job):
    s,j,_=job; orphan=s.root/'clips'/('f'*64); orphan.mkdir(); atomic(orphan/'audio.pcm',b'\x00\x00'); writejson(orphan/'receipt.json',{'key':'f'*64}); scratch=s.path(j)/'scratch'; scratch.mkdir(); (scratch/'owned-attempt').write_text('attempt')
    code='from audiobook.core import Store; from audiobook.jobs import cleanup; import sys; cleanup(Store(sys.argv[1]),apply=True)'
    r=subprocess.run([sys.executable,'-c',code,str(s.root)],env={**os.environ,'AUDIOBOOK_FAULT':'after_cleanup_item'}); assert r.returncode==86
    cleanup(s,apply=True); assert not orphan.exists() and not scratch.exists(); assert all(valid_clip(s,clip_key(u,j)) for u in j['plan']['utterances'])

def test_explicit_reimport_preserves_saved_verified_edit(job):
    s,j,_=job; b=j['document']['blocks'][1]
    repair(s,j['id'],block_id=b['id'],text='Keep the verified zero point zero eight milligrams.'); repair(s,j['id'],reimport=True)
    saved=s.get(j['id']); assert any(x['source']=='Keep the verified zero point zero eight milligrams.' for x in saved['document']['blocks']); assert saved['voice']==j['voice']

def test_repair_pins_old_rule_until_explicit_upgrade(job):
    from audiobook.jobs import document
    from audiobook.planner import plan
    s,j,_=job; j['plan']=plan(document(j),rule_revision='faithful-1'); s.save(j)
    repair(s,j['id'],alias='mg=milligrams'); assert s.get(j['id'])['plan']['rule_revision']=='faithful-1'
    from audiobook.planner import RULES
    repair(s,j['id'],upgrade_rules=True); assert s.get(j['id'])['plan']['rule_revision']==RULES

def test_required_speech_review_is_durable_and_resume_does_not_clear_it(job):
    s,j,_=job; render(s,j['id']); j=s.get(j['id']); j.update(audio_gate=True,audio_gate_approved_plan=None,audio_gate_checked_plan=j['plan']['plan_sha256'],audio_gate_checked_takes=take_signature(s,j),audio_review=[{'utterance':'u00001','clip_key':j['clips']['u00001'],'pcm_sha256':valid_clip(s,j['clips']['u00001'])['sha256'],'sources':j['plan']['utterances'][0]['sources'],'flags':['Negation disagreement requires listening.'],'report':'qa.json'}]); s.save(j)
    from audiobook.qa import accept
    from audiobook.speech_compare import POLICY
    with pytest.raises(Error,match='comparison policy changed'):accept(s,j['id'],'u00001','A stale comparison cannot approve this signal fixture.')
    j=s.get(j['id']);j['audio_gate_checked_comparison_policy']=POLICY;s.save(j)
    held=render(s,j['id']); assert held['status']=='needs_review' and held['needs_review'][0]['utterance']=='u00001'
    assert 'exact' not in held['needs_review'][0]['location'][0]
    from audiobook.qa import accept
    approved=accept(s,j['id'],'u00001','Synthetic control-flow test approval, not human release evidence.'); assert approved['status']=='ready'


def test_changed_review_cannot_be_accepted_without_new_qa(job):
    from audiobook.qa import accept
    s,j,_=job; render(s,j['id']); j=s.get(j['id']); j.update(audio_gate=True,audio_gate_approved_plan=None,audio_review=[{'utterance':'u00001','clip_key':j['clips']['u00001'],'sources':j['plan']['utterances'][0]['sources'],'flags':['Check reading.'],'report':'qa.json'}]); s.save(j); repair(s,j['id'],retake='u00001')
    with pytest.raises(Error,match='changed'): accept(s,j['id'],'u00001','Checked previous take only.')

@pytest.mark.parametrize('previous_schema',range(1,13))
def test_prerelease_schemas_migrate_without_changing_payload(job,previous_schema):
    from audiobook.core import SCHEMA
    s,j,_=job; original=s.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]; s.db.execute(f'PRAGMA user_version={previous_schema}');s.db.commit();s.db.close()
    migrated=Store(s.root);assert migrated.db.execute('PRAGMA user_version').fetchone()[0]==SCHEMA;assert migrated.db.execute('SELECT payload FROM jobs WHERE id=?',(j['id'],)).fetchone()[0]==original

def test_accepting_unchanged_old_review_cannot_approve_a_changed_other_take(job):
    from audiobook.qa import accept
    s,j,_=job;render(s,j['id']);j=s.get(j['id']);first=j['plan']['utterances'][0];last=j['plan']['utterances'][-1]
    j.update(audio_gate=True,audio_gate_approved_plan=None,audio_gate_checked_plan=j['plan']['plan_sha256'],audio_review=[{'utterance':first['id'],'clip_key':j['clips'][first['id']],'sources':first['sources'],'flags':['Check reading.'],'report':'old-report.json'}]);s.save(j)
    repair(s,j['id'],retake=last['id'])
    with pytest.raises(Error,match='plan changed'):accept(s,j['id'],first['id'],'Old unchanged reading does not qualify a changed different take.')
    assert s.get(j['id'])['audio_gate_approved_plan'] is None

def test_interrupt_with_closed_stdout_keeps_meaningful_exit(tmp_path):
    code='import os,sys; import audiobook.cli as c\ndef stop(_): raise KeyboardInterrupt\nc.doctor=stop\nos.close(1)\nsys.exit(c.main(["--home",sys.argv[1],"doctor"]))'
    r=subprocess.run([sys.executable,'-c',code,str(tmp_path/'closed-output')],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    assert r.returncode==130 and not r.stderr

def test_parallel_checkpoint_deduplicates_identical_missing_takes(job,monkeypatch):
    import audiobook.jobs as module
    import copy,threading,time
    s,j,_=job
    for p in (s.root/'clips').iterdir(): s.safe_remove(p)
    original=j['plan']['utterances'][0];duplicate=copy.deepcopy(original);duplicate['id']='u00003';duplicate['chapter']='Repeated'
    j['plan']['utterances'].append(duplicate);j['backend']['settings']={'parallel':2};s.save(j)
    calls=[];lock=threading.Lock();active=[0,0]
    class Fake:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def speech(self,text,voice,seed=42):
            with lock: calls.append(text);active[0]+=1;active[1]=max(active)
            time.sleep(.03)
            with lock:active[0]-=1
            data=(np.sin(np.arange(RATE*3)*2*np.pi*200/RATE)*10000).astype('<i2').tobytes()
            return validate_pcm(data)
    monkeypatch.setattr(module,'Engine',Fake)
    result=render(s,j['id']);saved=s.get(j['id'])
    assert result['status']=='ready' and len(calls)==2 and active[1]==2
    assert saved['clips']['u00001']==saved['clips']['u00003']
    assert saved['progress']['completed_utterances']==3
    companion=json.loads((Path(result['output'])/'companion.json').read_text())
    assert [u['id'] for u in companion['segments']]==['u00001','u00002','u00003']

def test_parallel_interrupt_stops_requests_before_join():
    from audiobook.jobs import generate_takes
    import threading
    stopped=threading.Event();both=threading.Barrier(2)
    class Fake:
        def speech(self,text,voice,seed=42):
            both.wait(timeout=2)
            if text=='interrupt':raise KeyboardInterrupt
            assert stopped.wait(2),'joining a live stream would block recovery'
            raise Error('stopped','Stopped own stream.')
        def __exit__(self,*args):stopped.set()
    todo=[({'id':'u1','text':'interrupt'},'a'),({'id':'u2','text':'waiting'},'b')]
    with pytest.raises(KeyboardInterrupt):list(generate_takes(Fake(),{'voice':{}},todo,2))
    assert stopped.is_set()

def test_publication_label_does_not_invalidate_speech(job):
    s,j,_=job;keys=[clip_key(u,j) for u in j['plan']['utterances']]
    j['publication_label']='AI-generated comparison';s.save(j)
    assert keys==[clip_key(u,j) for u in j['plan']['utterances']]
    result=render(s,j['id']);probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_format','-of','json',str(Path(result['output'])/'book.m4b')]))
    assert probe['format']['tags']['title'].endswith('AI-generated comparison')

def test_voice_clone_can_preserve_saved_default(tmp_path):
    import wave
    from audiobook.cli import main
    s=Store(tmp_path/'state');writejson(s.root/'preferences.json',{'voice':'existing','other_preference':'protected'})
    audio=tmp_path/'reference.wav';text=tmp_path/'transcript.txt';text.write_text('A reference for a control-flow test, not a qualified narrator.')
    with wave.open(str(audio),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(RATE);w.writeframes((np.sin(np.arange(RATE*3)*2*np.pi*200/RATE)*10000).astype('<i2').tobytes())
    assert main(['--home',str(s.root),'voice','clone','isolated','--reference',str(audio),'--transcript-file',str(text),'--no-default'])==0
    assert json.loads((s.root/'preferences.json').read_text())=={'voice':'existing','other_preference':'protected'}
    assert (s.root/'voices/isolated/voice.json').exists()

def test_cached_excerpt_preserves_consecutive_passages_without_waiting_for_writer(job,tmp_path):
    from audiobook.jobs import excerpt
    import wave
    s,j,_=job;original=s.get(j['id'])
    # The source has two completed sequential utterances. An active writer must not block listening.
    with s.lock():result=excerpt(s,j['id'],'u00001',2,tmp_path/'listen')
    receipt=json.loads(Path(result['receipt']).read_text())
    assert result['utterances']==[u['id'] for u in j['plan']['utterances']]
    assert [u['text'] for u in receipt['utterances']]==[u['text'] for u in j['plan']['utterances']]
    assert receipt['seconds']==6.3 and receipt['ready_publication'] is False
    with wave.open(result['audio']) as w:assert w.getnframes()==RATE*6+7200
    assert s.get(j['id'])==original
    with pytest.raises(Error,match='already exists'):excerpt(s,j['id'],'u00001',2,tmp_path/'listen')
    k=clip_key(j['plan']['utterances'][1],j);s.safe_remove(s.root/'clips'/k)
    with pytest.raises(Error,match='u00002'):excerpt(s,j['id'],'u00001',2,tmp_path/'must-not-skip')
    assert not (tmp_path/'must-not-skip').exists()

def test_status_reconciles_a_real_killed_writer_without_losing_audio(job):
    from audiobook.jobs import summary
    s,j,_=job
    j['clips']={u['id']:clip_key(u,j) for u in j['plan']['utterances']};s.save(j);before=dict(j['clips'])
    code='from audiobook.core import Store; import sys; s=Store(sys.argv[1]);\nwith s.lock():\n j=s.get(sys.argv[2]);j["status"]="rendering";s.save(j);print("locked",flush=True);sys.stdin.read()'
    p=subprocess.Popen([sys.executable,'-c',code,str(s.root),j['id']],stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True)
    try:
        assert p.stdout.readline().strip()=='locked'
        assert summary(s,s.get(j['id']))['status']=='rendering'
        p.kill();p.wait(5)
        result=summary(s,s.get(j['id']))
        assert result['status']=='paused' and result['error']['code']=='worker_interrupted'
        saved=s.get(j['id']);assert saved['clips']==before and all(valid_clip(s,k) for k in before.values())
        assert render(s,j['id'])['status']=='ready'
    finally:
        if p.poll() is None:p.kill();p.wait()

def test_stale_pid_file_cannot_keep_an_orphaned_assembly_active(job):
    from audiobook.jobs import summary
    s,j,_=job;j['status']='assembling';s.save(j)
    (s.root/'runtime/writer.lock').write_text(str(os.getpid()))
    result=summary(s,s.get(j['id']))
    assert result['status']=='paused' and result['error']['code']=='worker_interrupted'





def test_unknown_future_rules_refuse_repair_without_changing_saved_payload(job):
    s,j,source=job;j['plan']['rule_revision']='faithful-999';j['plan'].pop('plan_sha256');j['plan']['plan_sha256']=key(j['plan']);s.save(j)
    before=s.db.execute('select payload from jobs where id=?',(j['id'],)).fetchone()[0];original=Path(j['snapshot']).read_bytes()
    with pytest.raises(Error,match='not supported') as error:repair(s,j['id'],alias='mg=milligrams')
    assert error.value.code=='newer_rules'
    assert s.db.execute('select payload from jobs where id=?',(j['id'],)).fetchone()[0]==before
    assert Path(j['snapshot']).read_bytes()==original
    assert all(valid_clip(s,clip_key(u,j)) for u in j['plan']['utterances'])
