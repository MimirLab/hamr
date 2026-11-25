"""Raw ASR observations belong to exact PCM and an explicit observer identity.

Store them beside the pinned clip so normal clip GC also removes its observations.
Never cache expected text, review decisions or a speech-quality pass.
"""
from __future__ import annotations
import contextlib,json,math,shutil,subprocess,sys,time
from pathlib import Path
from .asr_fluid import Recipe, observe_many
from .core import Error,filehash,key,loadjson,writejson,fault
from .jobs import clip_key,valid_clip

BATCH_SIZE=32

def observer_identity(python,model,worker):
    if isinstance(model,Recipe):
        return model.identity()
    model=Path(model).absolute()
    files={p.name:filehash(p) for p in sorted(model.iterdir()) if p.is_file()}
    # Probe the explicitly chosen isolated environment, not the app environment.
    probe="""import hashlib,importlib.metadata as m,json,platform,sys
versions={n:m.version(n) for n in ('faster-whisper','ctranslate2','numpy')}
code={}
for name in ('faster-whisper','ctranslate2'):
 d=m.distribution(name)
 for entry in d.files or []:
  s=str(entry)
  if s.endswith('.py') or '.so' in s:
   p=d.locate_file(entry)
   if p.is_file():
    with p.open('rb') as f: code[s]=hashlib.file_digest(f,'sha256').hexdigest()
print(json.dumps({'python':sys.version,'machine':platform.machine(),'packages':versions,'code_sha256':code}))
"""
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:
        raise Error('missing_dependency','FFmpeg is needed to check saved speech.','Install FFmpeg and repeat the command.')
    try:
        environment=json.loads(subprocess.check_output([str(python),'-c',probe],stderr=subprocess.PIPE,timeout=60))
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        raise Error('asr_environment',f'Could not run the speech checker with Python at {python}.',
                    'Use a Python environment with faster-whisper installed, or run hamr setup --checks-only to repair the default checker.') from e
    try:
        ffmpeg_version=subprocess.check_output([ffmpeg,'-version'],stderr=subprocess.PIPE,timeout=10).decode()
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        raise Error('asr_environment',f'Could not run FFmpeg at {ffmpeg}.','Check the FFmpeg installation and repeat the command.') from e
    return {'schema_version':1,'worker_sha256':filehash(worker),'model_files':files,
            'environment':environment,'ffmpeg_sha256':filehash(Path(ffmpeg)),'ffmpeg_version':ffmpeg_version,
            'device':'cpu','compute_type':'int8','cpu_threads':2,'prompt':None,
            'language':'en','beam_size':5,'condition_on_previous_text':False,'vad_filter':False,
            'input_format':'s16le mono 24000; FFmpeg f32le mono 16000'}

def raw_observation(row):
    raw={k:row[k] for k in ('text','segments','seconds')}
    if not isinstance(raw['text'],str) or not isinstance(raw['segments'],list): raise ValueError('Invalid ASR text/segments')
    if not isinstance(raw['seconds'],(int,float)) or not math.isfinite(raw['seconds']) or raw['seconds']<0: raise ValueError('Invalid ASR time')
    if row.get('timing_kind')=='unavailable':
        if raw['segments']: raise ValueError('Observer without timing returned segments')
        return {**raw,'timing_kind':'unavailable'}
    for s in raw['segments']:
        if not isinstance(s['text'],str): raise ValueError('Invalid segment text')
        for k in ('start','end','avg_logprob'):
            if not isinstance(s[k],(int,float)) or not math.isfinite(s[k]): raise ValueError('Invalid segment value')
    if ' '.join(s['text'].strip() for s in raw['segments'])!=raw['text']: raise ValueError('ASR text differs from segments')
    return raw


def model_fingerprint(identity):
    return identity.get('model_sha256') or identity.get('model_files',{}).get('model.bin')


def audible_text(row, seconds):
    if row.get('timing_kind')=='unavailable':
        return row['text']
    return ' '.join(s['text'].strip() for s in row['segments'] if s['start']<seconds+.1)

def observation_path(store,k,pcm_sha256,identity):
    return store.root/'clips'/k/('asr-'+key({'identity':identity,'pcm_sha256':pcm_sha256})+'.json')

def read_observation(store,k,receipt,identity):
    p=observation_path(store,k,receipt['sha256'],identity)
    if p.is_symlink(): raise Error('unsafe_path','ASR observation cannot be a symlink.')
    try:
        if p.stat().st_size>1024*1024: return None
        r=loadjson(p)
        if r['schema_version']!=1 or r['observer_sha256']!=key(identity) or r['pcm_sha256']!=receipt['sha256']: return None
        raw=raw_observation(r['observation'])
        if key(raw)!=r['observation_sha256']: return None
        return raw
    except (OSError,ValueError,KeyError,TypeError): return None

def write_observation(store,k,receipt,identity,row,provenance='fresh unprompted ASR'):
    p=observation_path(store,k,receipt['sha256'],identity)
    if p.is_symlink(): raise Error('unsafe_path','ASR observation cannot be a symlink.')
    raw=raw_observation(row)
    writejson(p,{'schema_version':1,'observer_sha256':key(identity),'pcm_sha256':receipt['sha256'],
                 'observation':raw,'observation_sha256':key(raw),'provenance':provenance,'recorded':time.time()})

def recognize(python, model, clips):
    """Decode supplied PCM without sending the worker the expected words."""
    if isinstance(model,Recipe):
        requests=[]
        for clip in clips:
            path=Path(clip['path'])
            receipt=clip.get('receipt') or {'samples':path.stat().st_size//2,'sha256':filehash(path)}
            requests.append({'pcm':str(path),'samples':receipt['samples'],'pcm_sha256':receipt['sha256']})
        return [raw_observation({**row,'segments':[]}) for row in observe_many(model,requests)]
    worker=Path(__file__).with_name('qa_worker.py')
    request={'model':str(Path(model).absolute()),'clips':clips}
    try:
        result=subprocess.run([str(python),str(worker)],input=json.dumps(request).encode(),
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=max(120,len(clips)*30))
        observed=json.loads(result.stdout)
    except (OSError,ValueError,subprocess.SubprocessError) as e:
        raise Error('asr_failed','Isolated CPU ASR failed; completed observation checkpoints are retained.',
                    'Repeat with the same speech checker. No ASR download occurs.') from e
    rows=observed.get('rows',[]) if isinstance(observed,dict) else []
    if not isinstance(rows,list) or len(rows)!=len(clips):
        raise Error('asr_contract','ASR result count differs from requested clips.')
    checked=[]
    for clip,row in zip(clips,rows):
        if not isinstance(row,dict) or row.get('utterance')!=clip['utterance']:
            raise Error('asr_contract','ASR returned an unexpected utterance identity.')
        try: checked.append(raw_observation(row))
        except (ValueError,KeyError,TypeError) as e:
            raise Error('asr_contract','ASR returned an invalid observation.') from e
    return checked

def observe(store,j,utterances,python,model,*,saved_observer=None):
    worker=Path(__file__).with_name('qa_worker.py')
    identity=saved_observer if saved_observer is not None else observer_identity(python,model,worker)
    # A unique verified take is recognized once, even if it occurs repeatedly in the plan.
    takes={}; mapping={}; raw={}; reused=0; recognized=0; started=time.monotonic()
    progress_path=store.path(j)/'qa-progress.json'
    for u in utterances:
        k=clip_key(u,j); mapping[u['id']]=k
        if k in takes: continue
        receipt=valid_clip(store,k)
        if not receipt: raise Error('qa_missing_clip','Speech QA requires verified PCM.','Render the job first, then run qa.')
        takes[k]={'receipt':receipt,'utterance':u['id'],'path':str(store.root/'clips'/k/'audio.pcm')}
        old=read_observation(store,k,receipt,identity)
        if old is not None: raw[k]=old;reused+=1
    todo=[k for k in takes if k not in raw]
    if saved_observer is not None and todo:
        from .installation import command
        raise Error('qa_observations_missing',
                    f'{len(todo)} current take(s) lack an exact saved transcript from the previous verifier.',
                    'Check the existing recordings to fill in the missing transcripts. This does not render speech.\n'
                    + command(store.root,'qa',j['id'],'--all','--require-review'))
    def progress(phase):
        elapsed=time.monotonic()-started
        done=sum(k in raw for k in mapping.values())
        writejson(progress_path,{'schema_version':1,'job':j['id'],'revision':j['revision'],
            'plan_sha256':j['plan']['plan_sha256'],'observer_sha256':key(identity),'observer_profile':identity.get('profile','whisper'),'phase':phase,
            'completed_utterances':done,'total_utterances':len(utterances),'completed_unique_takes':len(raw),
            'total_unique_takes':len(takes),'reused_observations':reused,'recognized_this_run':recognized,
            'maximum_uncheckpointed_takes':BATCH_SIZE,'elapsed_seconds':elapsed,
            'eta_seconds':elapsed/recognized*(len(todo)-recognized) if recognized else (0 if not todo else None),
            'updated':time.time(),'resume':f'hamr resume {j["id"]}',
            'meaning':'Last durable QA checkpoint; this file alone does not establish a live worker or quality acceptance.'})
    progress('checking')
    try:
        for offset in range(0,len(todo),BATCH_SIZE):
            batch=todo[offset:offset+BATCH_SIZE]
            clips=[{'utterance':takes[k]['utterance'],'path':takes[k]['path']} for k in batch]
            if isinstance(model,Recipe):
                for clip,k in zip(clips,batch):clip['receipt']=takes[k]['receipt']
            for k,row in zip(batch,recognize(python,model,clips)):
                # Revalidate the actual PCM before attaching an observation to it.
                receipt=valid_clip(store,k)
                if not receipt or receipt['sha256']!=takes[k]['receipt']['sha256']: raise Error('qa_clip_changed','Speech changed during ASR; run QA on the current verified take.')
                write_observation(store,k,receipt,identity,row)
                raw[k]=row;recognized+=1
            progress('checking')
            fault('after_asr_batch')
            print(f'Speech QA: {sum(k in raw for k in mapping.values())}/{len(utterances)} passages; {reused} saved observations reused.',file=sys.stderr,flush=True)
    except BaseException:
        with contextlib.suppress(OSError): progress('interrupted_or_failed')
        raise
    progress('observations_complete')
    rows=[{'utterance':u['id'],'audio_seconds':takes[mapping[u['id']]]['receipt']['seconds'],'audio_samples':takes[mapping[u['id']]]['receipt']['samples'],'pcm_sha256':takes[mapping[u['id']]]['receipt']['sha256'],**raw[mapping[u['id']]]} for u in utterances]
    return rows,identity,{'reused_unique_observations':reused,'recognized_unique_takes':recognized,
                          'maximum_uncheckpointed_takes':BATCH_SIZE,'progress_file':str(progress_path)}
