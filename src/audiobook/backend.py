from __future__ import annotations
import contextlib,hashlib,http.client,json,os,platform,shutil,socket,subprocess,sys,tarfile,threading,time,urllib.request,wave
from pathlib import Path
import httpx,numpy as np
from .core import Error,RATE,atomic,digest,filehash,key,loadjson,run,writejson
from .deadlines import wall_budget
from .setup_progress import size_text

CATALOG=Path(__file__).with_name('catalog.json')
DOWNLOAD_STALL_SECONDS=90
DOWNLOAD_TOTAL_SECONDS=7200
DOWNLOAD_POLL_SECONDS=.1


def transfer_asset(url,part,offset,size,*,progress=None,label=None):
    process=subprocess.Popen([sys.executable,'-m','audiobook.asset_download',url,str(part),str(offset),str(size)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,start_new_session=True)
    started=last_activity=time.monotonic();observed=offset
    try:
        if progress: progress.transfer(label,offset,size)
        while process.poll() is None:
            current=part.stat().st_size if part.exists() else 0;now=time.monotonic()
            if current!=observed:observed=current;last_activity=now
            if progress: progress.transfer(label,current,size)
            if now-started>DOWNLOAD_TOTAL_SECONDS:raise Error('download_timeout','Asset transfer exceeded its total wall-clock budget.','Repeat setup to resume the bound partial.')
            if now-last_activity>DOWNLOAD_STALL_SECONDS:raise Error('download_stalled','Asset transfer stopped making byte progress.','Repeat setup to resume the bound partial.')
            time.sleep(DOWNLOAD_POLL_SECONDS)
        output=process.communicate()[0]
        if process.returncode:
            try:failure=json.loads(output)
            except (ValueError,TypeError):failure={'code':'download_failed','message':'Owned asset-transfer worker exited before completion.'}
            raise Error(failure['code'],failure['message'],'Check connectivity and disk space; repeat setup to resume.')
        if progress: progress.transfer(label,part.stat().st_size,size,complete=True)
    finally:
        if progress: progress.finish()
        if process.poll() is None:
            process.terminate()
            try:process.wait(2)
            except subprocess.TimeoutExpired:process.kill();process.wait(2)
        if process.stdout:process.stdout.close()

def doctor(store):
    from .hardware import inspect_host,advise
    from .checks_setup import configured
    from .installation import inspect as inspect_installation
    host=inspect_host();advice=advise(host,loadjson(CATALOG))
    dependencies={x:shutil.which(x) for x in ['ffmpeg','ffprobe','mpv','tesseract']}
    issues=[{'code':'missing_dependency','executable':x,'action':f'Install {x} in user space before rendering.'} for x in ('ffmpeg','ffprobe') if not dependencies[x]]
    installation,installation_issues=inspect_installation()
    if not dependencies['mpv']:
        issues.append({'code':'missing_player','executable':'mpv','action':'Install mpv to use hamr playback commands.'})
    return {'schema_version':1,'issues':issues+installation_issues,'os':platform.platform(),'arch':host['arch'],'gpus':host['gpus'],'ram_available_kib':(host['ram_available_bytes'] or 0)//1024,'disk_free':shutil.disk_usage(store.root).free,'dependencies':dependencies,'private_ocr':str(Path.home()/'.local/share/audiobook-tools/ocr/usr/bin/tesseract') if (Path.home()/'.local/share/audiobook-tools/ocr/usr/bin/tesseract').exists() else None,'setup':(store.root/'settings.json').exists(),'speech_checks_ready':configured(store) is not None,'installation':installation,'state':str(store.root),'automatic_profile':None,'qualification':'Recommendations are advisory; setup validates model startup before activation.','hardware':host,**advice}

def acquire(url,dst,expected,size,*,progress=None,label=None):
    label=label or dst.name
    if dst.exists():
        if progress: progress.stage(f'Checking cached {label} ({size_text(size)})')
        if dst.stat().st_size==size and filehash(dst)==expected: return
        raise Error('asset_corrupt','Installed candidate asset failed checksum.','Remove only this failed candidate and run setup again.')
    part=dst.with_suffix(dst.suffix+'.part'); identity=part.with_suffix(part.suffix+'.json')
    if part.is_symlink() or part.exists() and (not part.is_file() or part.stat().st_nlink!=1):raise Error('unsafe_path','Asset partial must be an owned regular file with one link.')
    wanted={'url':url,'sha256':expected,'bytes':size}
    if part.exists() and (not identity.exists() or loadjson(identity)!=wanted): part.unlink()
    if part.exists() and part.stat().st_size>=size: part.unlink()
    writejson(identity,wanted); offset=part.stat().st_size if part.exists() else 0
    try:
        options={'progress':progress,'label':label} if progress else {}
        transfer_asset(url,part,offset,size,**options)
        if progress: progress.stage(f'Verifying {label}')
        if part.stat().st_size!=size or filehash(part)!=expected: raise Error('download_integrity','Downloaded asset does not match pinned size/digest.','Run setup again; the partial will be revalidated.')
        os.replace(part,dst); identity.unlink(missing_ok=True)
    except (OSError,http.client.HTTPException) as e: raise Error('download_failed',f'Asset acquisition failed ({type(e).__name__}).','Check connectivity and disk space; repeat setup to resume.') from e

def setup(store,profile,cache=None,*,progress=None,before_activate=None):
    c=loadjson(CATALOG); profiles={p['id']:p for p in c['profiles']}; p=profiles.get(profile)
    if not p or p['runtime']!='hoppou': raise Error('unsupported_profile',f'Backend profile {profile!r} is unknown or catalog-only in this build.','Run hamr profiles for the built-in CPU/Vulkan model choices; selecting one downloads its pinned runtime and weights.')
    if platform.system()!='Linux' or platform.machine().lower() not in ('x86_64','amd64'): raise Error('unsupported_platform','This release installs the Linux x86_64 runtime.','Use a supported Linux x86_64 environment; native Windows, ARM and Apple MLX adapters are not implemented.')
    with store.lock():
        r=c['runtimes']['hoppou']['linux_x86_64']; package=c['packages'][p['packages'][0]]; root=store.root/'assets'/profile; root.mkdir(exist_ok=True)
        files=[dict(r,path=r['filename']),*package['files']]
        required=sum(f['bytes'] for f in files if not (root/f['path']).exists())
        if shutil.disk_usage(root).free<required+1024**3: raise Error('disk_space','Insufficient asset acquisition headroom.','Free space on the state filesystem.')
        for f in files:
            dst=root/f['path']
            if cache and (Path(cache)/f['path']).exists() and not dst.exists():
                src=Path(cache)/f['path']
                if src.stat().st_size!=f['bytes'] or filehash(src)!=f['sha256']: raise Error('asset_integrity','Cached candidate does not match catalog.')
                shutil.copyfile(src,dst)
            acquire(f['url'],dst,f['sha256'],f['bytes'],**({'progress':progress} if progress else {}))
        runtime=root/'runtime'
        if not runtime.exists():
            if progress: progress.stage('Extracting the Breeze runtime')
            stage=root/'runtime.partial'
            if stage.exists(): store.safe_remove(stage)
            stage.mkdir()
            with tarfile.open(root/r['filename']) as t: t.extractall(stage,filter='data')
            stage.rename(runtime)
        candidates=list(runtime.rglob('breeze-server'))
        if len(candidates)!=1: raise Error('runtime_layout','Expected one breeze-server executable.')
        binary=candidates[0]; binary.chmod(0o755)
        # Preserve executable/shared-library identities, and model terms locally.
        terms=root/'MODEL-LICENSE'
        license_file=next(f for f in c['packages']['official']['files'] if f['path']=='LICENSE')
        acquire(license_file['url'],terms,license_file['sha256'],license_file['bytes'],**({'progress':progress} if progress else {}))
        identity={'runtime_root':str(runtime),'profile':profile,'runtime_revision':c['runtimes']['hoppou']['revision'],'binary':str(binary),'binary_sha256':filehash(binary),'model':str(root/package['files'][0]['path']),'model_sha256':package['files'][0]['sha256'],'settings':p['settings'],'sample_rate':RATE,'qualification':'experimental','adapter_capabilities':{'languages':['en'],'voice_modes':['design','clone'],'direction':'not qualified'},'library_sha256':{str(x.relative_to(runtime)):filehash(x) for x in runtime.rglob('*.so*') if x.is_file()}}
        # A real startup proves activation before replacing known-working settings.
        try:
            if progress: progress.stage(f'Starting {profile} to check that it loads')
            with Engine(store,identity) as e: health=e.health
        except Error as exc:
            text=(store.root/'runtime/backend.log').read_text(errors='replace')[-12000:] if (store.root/'runtime/backend.log').exists() else ''
            import re
            if re.search(r'out.of.(?:device.|host.)?memory|failed to allocate|allocation failed|VK_ERROR_OUT_OF',text,re.I):
                raise Error('insufficient_memory',f'{profile} could not allocate enough memory for startup.','Close memory-heavy applications, run hamr doctor, then try a smaller model (vulkan-q8, vulkan-q6, vulkan-q4), vulkan-q8-cpu-text-encoder, or cpu-q4. Previously active settings are preserved.') from exc
            if re.search(r'VK_ERROR_INCOMPATIBLE_DRIVER|no (?:vulkan|physical).*device|failed.*vulkan|libvulkan.*cannot open',text,re.I):
                raise Error('vulkan_unavailable',f'{profile} could not start with the installed Vulkan driver.',f'Run hamr doctor and vulkaninfo --summary; check the Vulkan loader and vendor ICD. CPU execution (cpu-q4) avoids the GPU but still needs libvulkan.so.1. Startup details: {store.root / "runtime/backend.log"}') from exc
            raise Error(exc.code,exc.message,f'{exc.action} Profile: {profile}. Startup log: {store.root / "runtime/backend.log"}. Run hamr doctor for another starting choice.') from exc
        result={'schema_version':1,'profile':profile,'health':health,'identity':identity,'model_terms':str(terms)}
        if before_activate:
            result['speech_checks']=before_activate()
        writejson(store.root/'settings.json',identity)
        return result

def validate_request(text,voice=None,instruction=None):
    if not any(c.isalnum() for c in text):raise Error('unspeakable_text','Speech input contains only punctuation.','Upgrade the narration rules or repair this passage into a pause before rendering.')
    if len(text.encode('utf-8'))>1024:
        from .boundaries import sentence_spans
        from .continuous import fits_extended_sentence
        if not fits_extended_sentence(text) or len(list(sentence_spans(text)))!=1:
            raise Error('speech_input_limit','Speech exceeds the bounded one-pass input guard.','Use a source-supported boundary and retain each complete sentence.')
    fields={'reference transcript':voice.get('transcript','') if voice else '', 'instruction':instruction or ''}
    for field,value in fields.items():
        if len(value.encode('utf-8'))>1024:raise Error('speech_input_limit',f'{field.capitalize()} exceeds the conservative1024-byte one-pass input guard.','Use a scoped shorter spoken reading or source-supported boundaries; no input is truncated.')


class ResidentEngineSession:
    """Lazily retain one owned engine across render batches and CPU review."""
    def __init__(self,store,factory=None):
        self.store=store;self.factory=factory or Engine;self.engine=None;self.identity=None;self.starts=0;self.closed=False
    def __enter__(self):return self
    def __exit__(self,*exc):
        engine=self.engine;self.engine=None;self.closed=True
        if engine is not None:engine.__exit__(*exc)
    def speech(self,*args,**kwargs):
        if self.engine is None:raise Error('resident_closed','The resident engine is closed; explicitly restart the pipeline.')
        return self.engine.speech(*args,**kwargs)
    @contextlib.contextmanager
    def use(self,identity):
        if self.closed:raise Error('resident_closed','The resident engine is closed; explicitly restart the pipeline.')
        identity_key=key(identity)
        if self.identity is not None and self.identity!=identity_key:
            raise Error('resident_identity_changed','The resident backend configuration changed; stop and explicitly restart the pipeline.')
        if self.engine is None:
            engine=self.factory(self.store,identity)
            engine.__enter__();self.engine=engine;self.identity=identity_key;self.starts+=1
        # Batch exit deliberately retains model, client and process. The outer
        # pipeline owns cleanup on completion, cancellation or failure.
        # generate_takes aborts its engine explicitly on a failed request. Route
        # that through this owner too, so exception cleanup happens once.
        yield self


class Engine:
    STARTUP_SECONDS=180
    REQUEST_SECONDS=600
    TERMINATE_SECONDS=10
    def __init__(self,store,identity=None):
        self.store=store; self.identity=identity or loadjson(store.root/'settings.json'); self.process=None;self._stop_lock=threading.Lock()
    def __enter__(self):
        cfg=self.identity
        parallel=cfg.get('settings',{}).get('parallel',1)
        if type(parallel) is not int or not 1<=parallel<=4: raise Error('parallel_limit','This adapter supports one to four generation streams.')
        if parallel>1 and cfg['settings'].get('text_encoder')=='cpu': raise Error('parallel_limit','Concurrent streams with CPU text encoding are not qualified.')
        if filehash(Path(cfg['binary']))!=cfg['binary_sha256'] or filehash(Path(cfg['model']))!=cfg['model_sha256']: raise Error('backend_changed','Pinned runtime or model has changed.','Restore the exact job assets, or create a new job.')
        runtime=Path(cfg['binary']).parent
        for rel,sha in cfg.get('library_sha256',{}).items():
            candidates=[Path(cfg['runtime_root'])/rel]
            if not candidates or filehash(candidates[0])!=sha: raise Error('backend_changed','Pinned runtime library has changed.')
        with socket.socket() as s: s.bind(('127.0.0.1',0)); self.port=s.getsockname()[1]
        args=[cfg['binary'],cfg['model'],'--host','127.0.0.1','--port',str(self.port),'--ws-port','-1','--parallel',str(parallel),'--split-chars','0','--no-warmup','--voices-dir',str(self.store.root/'runtime'/'compiled-voices')]
        if cfg['settings'].get('device')=='cpu': args.append('--cpu')
        if cfg['settings'].get('text_encoder')=='cpu': args.append('--te-cpu')
        self.log=(self.store.root/'runtime'/'backend.log').open('wb')
        # No arbitrary transcript logs; upstream emits progress/length/settings by default.
        env=os.environ.copy(); env['LD_LIBRARY_PATH']=str(runtime)+':'+str(runtime/'lib')
        def child_setup():
            import ctypes,signal
            parent=os.getppid(); ctypes.CDLL(None).prctl(1,signal.SIGTERM)
            if os.getppid()!=parent: os._exit(1)
        self.process=subprocess.Popen(args,preexec_fn=child_setup,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,env=env,start_new_session=True)
        self.client=httpx.Client(base_url=f'http://127.0.0.1:{self.port}',timeout=httpx.Timeout(300,connect=2),trust_env=False)
        try:
            start=time.monotonic()
            with wall_budget(self.STARTUP_SECONDS,self._stop_backend,'backend_timeout','Breeze exceeded its startup wall-clock budget.'):
                while time.monotonic()-start<self.STARTUP_SECONDS:
                    if self.process.poll() is not None: raise Error('backend_start_failed','Breeze worker exited during startup.','Check device memory and pinned runtime compatibility; run doctor.')
                    try:
                        r=self.client.get('/health',timeout=min(2,self.STARTUP_SECONDS)); r.raise_for_status(); self.health=r.json()
                        if self.health.get('sample_rate')!=RATE: raise Error('pcm_format','Unexpected backend sample rate.')
                        return self
                    except httpx.TransportError: time.sleep(.25)
                raise Error('backend_timeout','Breeze did not become ready within its startup budget.')
        except BaseException:
            self.__exit__(None,None,None); raise
    def _stop_backend(self):
        import signal
        if not hasattr(self,'_stop_lock'):self._stop_lock=threading.Lock()
        with self._stop_lock:
            process=getattr(self,'process',None)
            if process and process.poll() is None:
                # This session was created by this Engine, never discovered by name.
                with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(self.TERMINATE_SECONDS)
                except subprocess.TimeoutExpired:
                    with contextlib.suppress(ProcessLookupError):os.killpg(process.pid,signal.SIGKILL)
                    process.wait(5)
    def __exit__(self,*_):
        self._stop_backend()
        if hasattr(self,'client'): self.client.close()
        if hasattr(self,'log'): self.log.close()
    def speech(self,text,voice=None,instruction=None,seed=42):
        validate_request(text,voice,instruction)
        logpath=self.store.root/'runtime'/'backend.log' if getattr(self,'store',None) is not None else None
        log_offset=logpath.stat().st_size if logpath and logpath.exists() else 0
        fields={'text':text,'split_chars':'0','max_new_tokens':'1500','seed':str(seed),'cfg_scale':'1'}
        files=None; fh=None
        if voice:
            ref=Path(voice['reference'])
            if filehash(ref)!=voice['reference_sha256']: raise Error('voice_changed','Saved voice reference changed.')
            fh=ref.open('rb'); files={'ref_audio':('reference.wav',fh,'audio/wav')}; fields['ref_text']=voice['transcript']
        else: fields['instruction']=instruction or 'A calm, clear English audiobook narrator.'
        try:
            with wall_budget(self.REQUEST_SECONDS,self._stop_backend,'speech_deadline','Breeze exceeded its speech request wall-clock budget.'):
                with self.client.stream('POST','/v1/audio/speech',data=fields,files=files) as r:
                    if r.status_code!=200: raise Error('speech_http',f'Breeze speech failed with HTTP {r.status_code}.','Resume; the job retains verified clips.')
                    if r.headers.get('x-sample-rate')!=str(RATE) or r.headers.get('x-sample-format')!='s16le': raise Error('pcm_format','Unexpected streamed PCM format.')
                    data=bytearray()
                    for chunk in r.iter_bytes():
                        data.extend(chunk)
                        if len(data)>RATE*2*125: raise Error('audio_limit','Breeze exceeded the bounded take duration.')
                import re
                logtext=(self.store.root/'runtime'/'backend.log').read_text(errors='replace')
                # Pinned Hoppou catches generation exceptions and still sends a
                # normal chunked EOF. Such PCM must never become a good take.
                # Parallel requests share this log: fail closed if attribution
                # is uncertain, rather than accepting a concurrent partial.
                if logpath:
                    with logpath.open('rb') as log:
                        log.seek(log_offset);new_log=log.read().decode(errors='replace')
                    if re.search(r'(?im)^\s*generation error:',new_log):
                        self._stop_backend()
                        raise Error('generation_failed','Breeze reported a generation failure despite a normally completed HTTP stream.','Inspect the owned backend log; committed prior takes are retained. Resume explicitly after resolving the failure.')
                actual=re.search(r'^backend:\s*(\S+)',logtext,re.M)
                expected_cpu=self.identity['settings'].get('device')=='cpu'
                if not actual or (expected_cpu and not actual[1].lower().startswith('cpu')) or (not expected_cpu and not actual[1].startswith('Vulkan')):
                    raise Error('device_unverified','Breeze did not prove the requested execution backend.','Check backend/device availability; unexpected CPU fallback is rejected.')
                data,receipt=validate_pcm(bytes(data),text); receipt['actual_device']=actual[1]
                request={'transport':'hoppou-http','endpoint':'/v1/audio/speech','fields':dict(fields),
                         'reference_audio_sha256':voice['reference_sha256'] if voice else None,
                         'scope':'Exact logical request fields; encoding is checked independently, not a raw wire capture.'}
                receipt['request']=request;receipt['request_sha256']=key(request)
                receipt['completion']={'HTTP_stream_finished':True,'backend_generation_error_reported':False,'backend_log_from_byte':log_offset,'policy':'Reject newly logged pinned-server generation exceptions, including a clean HTTP EOF; content still requires unprompted verification.'}
                return data,receipt
        except httpx.TransportError as e: raise Error('truncated_stream','Breeze stream did not finish successfully.','Resume to retry this take.') from e
        finally:
            if fh: fh.close()

def validate_pcm(data,text=''):
    if not data or len(data)%2: raise Error('invalid_pcm','PCM must have positive even byte length.')
    a=np.frombuffer(data,dtype='<i2').astype(np.float32)/32768; seconds=len(a)/RATE
    if seconds<.2 or seconds>=119.9: raise Error('take_duration','Take is empty or reached frame ceiling.')
    if float(np.sqrt(np.mean(a*a)))<.001: raise Error('silent_take','Take is mostly silent.')
    if float(np.mean(np.abs(a)>.999))>.01: raise Error('clipped_take','Take is excessively clipped.')
    words=len(text.split())
    if words>5 and not words/8<seconds<words*2.5: raise Error('take_pacing','Take duration is implausible for the approved script.')
    qa={'samples':len(a),'sample_rate':RATE,'sha256':digest(data),'seconds':seconds,'rms':float(np.sqrt(np.mean(a*a))),'clipping_fraction':float(np.mean(np.abs(a)>.999)),'semantic_qa':'pending ASR / listening; signal checks do not prove transcript fidelity'}
    return data,qa

def save_voice(store,name,reference,transcript,description='',origin='clone'):
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name): raise Error('voice_name','Voice name must contain 1–64 letters, digits, dash or underscore.')
    if not transcript.strip(): raise Error('transcript_required','Exact corrected reference transcript is required.')
    reference=Path(reference)
    if reference.suffix.lower() not in ('.wav','.flac','.mp3','.m4a','.ogg','.opus'): raise Error('reference_format','Reference must be a local WAV, FLAC, MP3, M4A, OGG or Opus file.')
    if reference.stat().st_size>200*1024*1024: raise Error('reference_size','Reference exceeds 200 MiB.')
    root=store.root/'voices'/name
    if root.exists(): raise Error('voice_exists','A saved voice with this name exists.','Choose a new name.')
    root.mkdir(); target=root/'reference.wav'
    try:
        run(['ffmpeg','-v','error','-protocol_whitelist','file,pipe','-i',reference,'-t','31','-ar',RATE,'-ac','1','-c:a','pcm_s16le',target])
        with wave.open(str(target)) as w: seconds=w.getnframes()/w.getframerate()
        if not 2<=seconds<=30: raise Error('reference_duration','Reference must be 2–30 seconds of clean speech.')
        original=root/('original'+reference.suffix.lower()); shutil.copyfile(reference,original)
        v={'original_reference':str(original),'original_reference_sha256':filehash(original),'name':name,'reference':str(target),'reference_sha256':filehash(target),'transcript':transcript.strip(),'description':description,'origin':origin,'language':'en','seconds':seconds}
        writejson(root/'voice.json',v); return v
    except BaseException:
        store.safe_remove(root); raise
