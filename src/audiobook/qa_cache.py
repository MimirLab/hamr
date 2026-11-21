"""Raw ASR observations belong to exact PCM and an explicit observer identity.

Store them beside the pinned clip so normal clip GC also removes its observations.
Never cache expected text, review decisions or a speech-quality pass.
"""
from __future__ import annotations
import contextlib,json,math,shutil,subprocess,sys,time
from pathlib import Path
from .core import Error,filehash,key,loadjson,writejson,fault
from .jobs import clip_key,valid_clip

BATCH_SIZE=32

def observer_identity(python,model,worker):
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
    for s in raw['segments']:
        if not isinstance(s['text'],str): raise ValueError('Invalid segment text')
        for k in ('start','end','avg_logprob'):
            if not isinstance(s[k],(int,float)) or not math.isfinite(s[k]): raise ValueError('Invalid segment value')
    if ' '.join(s['text'].strip() for s in raw['segments'])!=raw['text']: raise ValueError('ASR text differs from segments')
    return raw

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

def observe(store,j,utterances,python,model):
    worker=Path(__file__).with_name('qa_worker.py'); identity=observer_identity(python,model,worker)
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
    def progress(phase):
        elapsed=time.monotonic()-started
        done=sum(k in raw for k in mapping.values())
        writejson(progress_path,{'schema_version':1,'job':j['id'],'revision':j['revision'],
            'plan_sha256':j['plan']['plan_sha256'],'observer_sha256':key(identity),'phase':phase,
            'completed_utterances':done,'total_utterances':len(utterances),'completed_unique_takes':len(raw),
            'total_unique_takes':len(takes),'reused_observations':reused,'recognized_this_run':recognized,
            'maximum_uncheckpointed_takes':BATCH_SIZE,'elapsed_seconds':elapsed,
            'eta_seconds':elapsed/recognized*(len(todo)-recognized) if recognized else (0 if not todo else None),
            'updated':time.time(),'resume':f'hamr qa {j["id"]} with the same ASR Python/model options',
            'meaning':'Last durable QA checkpoint; this file alone does not establish a live worker or quality acceptance.'})
    progress('checking')
    try:
        for offset in range(0,len(todo),BATCH_SIZE):
            batch=todo[offset:offset+BATCH_SIZE]
            request={'model':str(Path(model).absolute()),'clips':[{'utterance':takes[k]['utterance'],'path':takes[k]['path']} for k in batch]}
            try:
                result=subprocess.run([str(python),str(worker)],input=json.dumps(request).encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True,timeout=max(120,len(batch)*30))
                observed=json.loads(result.stdout)
            except (OSError,ValueError,subprocess.SubprocessError) as e:
                raise Error('asr_failed','Isolated CPU ASR failed; completed observation checkpoints are retained.','Repeat qa with the same Python/model to resume. No ASR download occurs.') from e
            if len(observed.get('rows',[]))!=len(batch): raise Error('asr_contract','ASR result count differs from requested clips.')
            for k,row in zip(batch,observed['rows']):
                if row.get('utterance')!=takes[k]['utterance']: raise Error('asr_contract','ASR returned an unexpected utterance identity.')
                # Revalidate the actual PCM before attaching an observation to it.
                receipt=valid_clip(store,k)
                if not receipt or receipt['sha256']!=takes[k]['receipt']['sha256']: raise Error('qa_clip_changed','Speech changed during ASR; run QA on the current verified take.')
                try: write_observation(store,k,receipt,identity,row)
                except (ValueError,KeyError,TypeError) as e: raise Error('asr_contract','ASR returned an invalid observation.') from e
                raw[k]=raw_observation(row);recognized+=1
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
