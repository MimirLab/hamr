from __future__ import annotations
import contextlib,hashlib,http.client,json,os,platform,shutil,socket,stat,subprocess,sys,tempfile,threading,time,urllib.request,uuid,wave
from pathlib import Path
import httpx,numpy as np
from .core import Error,RATE,atomic,digest,filehash,key,loadjson,run,writejson
from .deadlines import wall_budget
from .setup_progress import size_text
from .platform_support import spawn_owned,close_windows_job
from .runtime_catalog import runtime_key,extract_hoppou

CATALOG=Path(__file__).with_name('catalog.json')
DOWNLOAD_STALL_SECONDS=90
DOWNLOAD_TOTAL_SECONDS=7200
DOWNLOAD_POLL_SECONDS=.1


def transfer_asset(url,part,offset,size,*,progress=None,label=None):
    process,job=spawn_owned([sys.executable,'-m','audiobook.asset_download',url,str(part),str(offset),str(size)],
                            parent_death=True,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
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
        if job: close_windows_job(job)
        if process.poll() is None:
            process.terminate()
            try:process.wait(2)
            except subprocess.TimeoutExpired:process.kill();process.wait(2)
        if process.stdout:process.stdout.close()

def doctor(store):
    from .hardware import inspect_host,advise
    from .checks_setup import configured, speech_advice
    from .installation import command, inspect as inspect_installation
    from .ocr_setup import describe as describe_ocr
    from .asr_fluid import advice as speech_diagnostic
    from .wash_setup import describe as describe_washing
    host=inspect_host();advice=advise(host,loadjson(CATALOG))
    if choice:=advice['recommended_profile']:
        choice['command']=command(store.root,'setup','--profile',choice['profile'])
    dependencies={x:shutil.which(x) for x in ['ffmpeg','ffprobe','mpv','tesseract']}
    issues=[{'code':'missing_dependency','executable':x,'action':f'Install {x} in user space before rendering.'} for x in ('ffmpeg','ffprobe') if not dependencies[x]]
    installation,installation_issues=inspect_installation()
    if not dependencies['mpv']:
        issues.append({'code':'missing_player','executable':'mpv','action':'Install mpv to use hamr playback commands.'})
    return {'schema_version':1,'issues':issues+installation_issues,'os':platform.platform(),'arch':host['arch'],'gpus':host['gpus'],'ram_available_kib':(host['ram_available_bytes'] or 0)//1024,'disk_free':shutil.disk_usage(store.root).free,'dependencies':dependencies,'private_ocr':str(Path.home()/'.local/share/audiobook-tools/ocr/usr/bin/tesseract') if (Path.home()/'.local/share/audiobook-tools/ocr/usr/bin/tesseract').exists() else None,'setup':(store.root/'settings.json').exists(),'speech_checks_ready':configured(store) is not None,'speech_diagnostic':speech_diagnostic(store,host),'speech_verifier':speech_advice(store,host),'installation':installation,'state':str(store.root),'automatic_profile':None,'qualification':'Recommendations are advisory; setup validates model startup before activation.','hardware':host,'ocr':describe_ocr(store,host),'voice_preparation':describe_washing(store,host),**advice}

def bound_partial(dst,wanted):
    part=dst.with_suffix(dst.suffix+'.part'); identity=part.with_suffix(part.suffix+'.json')
    if part.is_symlink() or part.exists() and (not part.is_file() or part.stat().st_nlink!=1):raise Error('unsafe_path','Asset partial must be an owned regular file with one link.')
    if identity.is_symlink() or (identity.exists() and not identity.is_file()):
        raise Error('unsafe_path','The asset partial receipt must be a local regular file.')
    if part.exists() and identity.is_file():
        try: recorded=loadjson(identity)
        except ValueError: recorded=None
        offset=part.stat().st_size
        if recorded==wanted and 0<offset<wanted['bytes']:
            return part,identity,offset
    return part,identity,0


def remaining_bytes(url,dst,expected,size):
    if dst.is_symlink() or dst.exists() and not dst.is_file():
        raise Error('unsafe_path','An asset must be a local regular file.')
    if dst.exists(): return 0
    return size-bound_partial(dst,{'url':url,'sha256':expected,'bytes':size})[2]


def acquire(url,dst,expected,size,*,progress=None,label=None):
    label=label or dst.name
    if dst.is_symlink() or dst.exists() and not dst.is_file():
        raise Error('unsafe_path','An asset must be a local regular file.')
    if dst.exists():
        if progress: progress.stage(f'Checking cached {label} ({size_text(size)})')
        if dst.stat().st_size==size and filehash(dst)==expected: return
        raise Error('asset_corrupt','Installed candidate asset failed checksum.','Remove only this failed candidate and run setup again.')
    wanted={'url':url,'sha256':expected,'bytes':size}
    part,identity,offset=bound_partial(dst,wanted)
    if not offset: part.unlink(missing_ok=True)
    writejson(identity,wanted)
    try:
        options={'progress':progress,'label':label} if progress else {}
        transfer_asset(url,part,offset,size,**options)
        received=part.stat().st_size
        if offset<received<size:
            if progress: progress.stage(f'{label} ended early. Resuming from {size_text(received)}.')
            transfer_asset(url,part,received,size,**options)
            received=part.stat().st_size
        if received<size:
            raise Error('download_incomplete',f'{label} ended after {size_text(received)} of {size_text(size)}.',
                        'Run setup again to resume this download.')
        if progress: progress.stage(f'Verifying {label}')
        if received!=size or filehash(part)!=expected: raise Error('download_integrity','Downloaded asset does not match pinned size/digest.','Run setup again; the partial will be revalidated.')
        os.replace(part,dst); identity.unlink(missing_ok=True)
    except (OSError,http.client.HTTPException) as e: raise Error('download_failed',f'Asset acquisition failed ({type(e).__name__}).','Check connectivity and disk space; repeat setup to resume.') from e

def asset_destination(root,record):
    legacy=root/record['path']
    if legacy.is_symlink() or legacy.exists() and not legacy.is_file():
        raise Error('unsafe_path','An asset must be a local regular file.')
    if not legacy.exists(): return legacy,False
    if legacy.stat().st_size==record['bytes'] and filehash(legacy)==record['sha256']:
        return legacy,True
    # Preserve the previous bytes and their path for jobs pinned to this basename.
    destination=root/'pinned'/record['sha256']/record['path']
    for directory in (destination.parent.parent,destination.parent):
        if directory.is_symlink() or directory.is_junction():
            raise Error('unsafe_path','Pinned assets must use an owned Hamr directory.')
        directory.mkdir(exist_ok=True)
    return destination,False


def runtime_tree(directory):
    files={}
    for path in directory.rglob('*'):
        relative=path.relative_to(directory).as_posix()
        if path.is_symlink():
            files[relative]=('link',os.readlink(path))
        elif path.is_file():
            files[relative]=('file',filehash(path),stat.S_IMODE(path.stat().st_mode))
        elif path.is_dir():
            files[relative]=('directory',)
        else:
            files[relative]=('special',)
    return files


def verified_runtime(root,archive,archive_sha,variant,progress):
    if progress: progress.stage('Checking the Breeze runtime against its pinned archive')
    stage=root/f'.runtime-{archive_sha}.partial'
    if stage.is_symlink() or stage.is_junction() or stage.exists() and not stage.is_dir():
        raise Error('unsafe_path','The runtime staging path must be an owned Hamr directory.')
    if stage.exists():shutil.rmtree(stage)
    stage.mkdir()
    legacy=root/('runtime-windows' if variant=='windows_x86_64' else 'runtime')
    destination=root/f'runtime-{archive_sha}'
    try:
        extract_hoppou(archive,stage,variant)
        windows=variant=='windows_x86_64'
        candidates=list(stage.rglob('breeze-server.exe' if windows else 'breeze-server'))
        if len(candidates)!=1 or not candidates[0].is_file():
            raise Error('runtime_layout','Expected one breeze-server executable.')
        relative=candidates[0].relative_to(stage)
        if not windows: candidates[0].chmod(0o755)
        expected=runtime_tree(stage)
        for path in (destination,*sorted(root.glob(destination.name+'-repair-*')),legacy):
            if path.is_symlink() or path.is_junction() or not path.is_dir(): continue
            if runtime_tree(path)==expected: return path,path/relative
        if destination.exists() or destination.is_symlink():
            destination=root/f'{destination.name}-repair-{uuid.uuid4().hex}'
        stage.rename(destination)
        return destination,destination/relative
    finally:
        if stage.exists():shutil.rmtree(stage)


def setup(store,profile,cache=None,*,progress=None,before_activate=None,before_ocr_activate=None):
    c=loadjson(CATALOG); profiles={p['id']:p for p in c['profiles']}; p=profiles.get(profile)
    if not p or p['runtime']!='hoppou': raise Error('unsupported_profile',f'Backend profile {profile!r} is unknown or catalog-only in this build.','Run hamr profiles for the built-in CPU/Vulkan model choices; selecting one downloads its pinned runtime and weights.')
    variant=runtime_key(platform.system(),platform.machine())
    if variant is None or variant not in c['runtimes']['hoppou']:
        raise Error('unsupported_platform','This speech runtime is available for x86_64 Linux and Windows.','Run hamr profiles to see the supported builds and research candidates.')
    with store.lock():
        r=c['runtimes']['hoppou'][variant]; package=c['packages'][p['packages'][0]]; root=store.root/'assets'/profile
        if root.is_symlink() or root.is_junction(): raise Error('unsafe_path','Backend assets must use an owned Hamr directory.')
        root.mkdir(exist_ok=True)
        license_file=next(f for f in c['packages']['official']['files'] if f['path']=='LICENSE')
        files=[dict(r,path=r['filename']),*package['files'],dict(license_file,path='MODEL-LICENSE')]
        destinations=[asset_destination(root,f) for f in files]
        cache_paths=[Path(cache)/f['path'] if cache else None for f in files[:-1]]+[None]
        required=sum(f['bytes'] if src and src.exists() else remaining_bytes(f['url'],dst,f['sha256'],f['bytes'])
                     for f,(dst,validated),src in zip(files,destinations,cache_paths) if not validated and not dst.exists())
        if shutil.disk_usage(root).free<required+1024**3: raise Error('disk_space','Insufficient asset acquisition headroom.','Free space on the state filesystem.')
        for f,(dst,validated),src in zip(files,destinations,cache_paths):
            if validated: continue
            if src and src.exists() and not dst.exists():
                if src.stat().st_size!=f['bytes'] or filehash(src)!=f['sha256']: raise Error('asset_integrity','Cached candidate does not match catalog.')
                with tempfile.NamedTemporaryFile(dir=dst.parent,delete=False) as staged: temporary=Path(staged.name)
                try:
                    shutil.copyfile(src,temporary)
                    os.replace(temporary,dst)
                finally:
                    temporary.unlink(missing_ok=True)
            acquire(f['url'],dst,f['sha256'],f['bytes'],**({'progress':progress} if progress else {}))
        runtime,binary=verified_runtime(root,destinations[0][0],r['sha256'],variant,progress)
        windows=variant=='windows_x86_64'
        terms=destinations[-1][0]
        identity={'runtime_root':str(runtime),'profile':profile,'runtime_revision':c['runtimes']['hoppou']['revision'],'runtime_archive_sha256':r['sha256'],'binary':str(binary),'binary_sha256':filehash(binary),'model':str(destinations[1][0]),'model_sha256':package['files'][0]['sha256'],'settings':p['settings'],'sample_rate':RATE,'qualification':'experimental','adapter_capabilities':{'languages':['en'],'voice_modes':['design','clone'],'direction':'not qualified'},'library_sha256':{x.relative_to(runtime).as_posix():filehash(x) for x in runtime.rglob('*.dll' if windows else '*.so*') if x.is_file()}}
        if windows: identity['runtime_platform']=variant
        # A real startup proves activation before replacing known-working settings.
        try:
            if progress: progress.stage(f'Starting {profile} to check that it loads')
            with Engine(store,identity) as e:
                health=e.health
                if windows or health.get('actual_device') is None:
                    if progress: progress.stage('Checking speech and the actual processing device')
                    _,proof=e.speech('This is a short speech check.')
                    health['actual_device']=proof['actual_device']
        except Error as exc:
            text=(store.root/'runtime/backend.log').read_text(errors='replace')[-12000:] if (store.root/'runtime/backend.log').exists() else ''
            import re
            if re.search(r'out.of.(?:device.|host.)?memory|failed to allocate|allocation failed|VK_ERROR_OUT_OF',text,re.I):
                raise Error('insufficient_memory',f'{profile} could not allocate enough memory for startup.','Close memory-heavy applications, run hamr doctor, then try a smaller model (vulkan-q8, vulkan-q6, vulkan-q4), vulkan-q8-cpu-text-encoder, or cpu-q4. Previously active settings are preserved.') from exc
            if re.search(r'VK_ERROR_INCOMPATIBLE_DRIVER|no (?:vulkan|physical).*device|failed.*vulkan|libvulkan.*cannot open',text,re.I):
                if windows:
                    hint='Update the graphics driver and run hamr doctor. The processor-only option (cpu-q4) still uses the driver’s Vulkan loader.'
                else:
                    hint='Run hamr doctor and vulkaninfo --summary. The processor-only option (cpu-q4) still needs the Vulkan loader (libvulkan.so.1).'
                raise Error('vulkan_unavailable',f'{profile} could not start with the installed Vulkan driver.',f'{hint} Startup details: {store.root / "runtime/backend.log"}') from exc
            raise Error(exc.code,exc.message,f'{exc.action} Profile: {profile}. Startup log: {store.root / "runtime/backend.log"}. Run hamr doctor for another starting choice.') from exc
        result={'schema_version':1,'profile':profile,'health':health,'identity':identity,'model_terms':str(terms),'runtime_platform':variant}
        if before_activate:
            result['speech_checks']=before_activate()
        if before_ocr_activate:
            result['ocr']=before_ocr_activate()
        if before_activate:
            writejson(store.root/'speech-checks.json',result['speech_checks'])
        if before_ocr_activate:
            writejson(store.root/'ocr-settings.json',result['ocr'])
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
        self.closed=True
        self.release(*exc)
    def release(self,*exc):
        """Release the current process while keeping the pinned session reusable."""
        engine=self.engine;self.engine=None
        if not exc:exc=(None,None,None)
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
        self._windows_job=None
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
        env=os.environ.copy()
        if os.name != 'nt':
            env['LD_LIBRARY_PATH']=os.pathsep.join((str(runtime),str(runtime/'lib')))
        try:
            self.process,self._windows_job=spawn_owned(args,parent_death=True,stdin=subprocess.DEVNULL,
                                                        stdout=self.log,stderr=self.log,env=env)
            self.client=httpx.Client(base_url=f'http://127.0.0.1:{self.port}',timeout=httpx.Timeout(300,connect=2),trust_env=False)
            start=time.monotonic()
            with wall_budget(self.STARTUP_SECONDS,self._stop_backend,'backend_timeout','Breeze exceeded its startup wall-clock budget.'):
                while time.monotonic()-start<self.STARTUP_SECONDS:
                    if self.process.poll() is not None: raise Error('backend_start_failed','Breeze worker exited during startup.','Check device memory and pinned runtime compatibility; run doctor.')
                    try:
                        r=self.client.get('/health',timeout=min(2,self.STARTUP_SECONDS)); r.raise_for_status(); self.health=r.json()
                        if self.health.get('sample_rate')!=RATE: raise Error('pcm_format','Unexpected backend sample rate.')
                        # The pinned server may buffer this line until it writes
                        # speech output. A model load alone cannot prove placement.
                        self.health['actual_device']=self.verify_device(self.read_log(), allow_missing=True)
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
            job=getattr(self,'_windows_job',None)
            if job is not None:
                self._windows_job=None
                close_windows_job(job)
                if process: process.wait(5)
                return
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
    def read_log(self):
        return (self.store.root/'runtime'/'backend.log').read_text(encoding='utf-8',errors='replace')
    def verify_device(self,logtext,*,allow_missing=False):
        import re
        actual=re.search(r'^backend:\s*(\S+)',logtext,re.M)
        if not actual and allow_missing:
            return None
        expected_cpu=self.identity['settings'].get('device')=='cpu'
        if not actual or (expected_cpu and not actual[1].lower().startswith('cpu')) or (not expected_cpu and not actual[1].startswith('Vulkan')):
            raise Error('device_unverified','Breeze did not prove the requested execution backend.','Check backend/device availability; unexpected CPU fallback is rejected.')
        return actual[1].rstrip(',')
    def speech(self,text,voice=None,instruction=None,seed=42,*,cfg_scale=1,sampling=None,frame_guidance=None):
        from .performance import (FRAME_GUIDANCE_CAPABILITY, supports_frame_guidance, validate_guidance,
                                  validated_frame_guidance, validated_sampling)

        validate_request(text,voice,instruction)
        identity = getattr(self, 'identity', {}) or {}
        applied = {}
        if frame_guidance is None:
            validate_guidance(cfg_scale, identity.get('settings', {}).get('parallel', 1))
        else:
            frame_guidance = validated_frame_guidance(frame_guidance)
            if (not voice or not supports_frame_guidance(identity)
                    or getattr(self, 'health', {}).get('frame_guidance') != FRAME_GUIDANCE_CAPABILITY):
                raise Error('frame_guidance_unavailable', 'The running backend did not verify frame-local guidance.')
            cfg_scale = frame_guidance['base_scale']
            window = f"{frame_guidance['start_frame']}:{frame_guidance['end_frame']}:{frame_guidance['scale']:.9g}"
            applied = {'X-Breeze-Guidance-Window':window, 'X-Breeze-Guidance-Base':format(cfg_scale,'.9g'),
                       'X-Breeze-Guidance-Release':str(frame_guidance['release_frames']),
                       'X-Breeze-Guidance-Chunks':str(FRAME_GUIDANCE_CAPABILITY['chunk_frames'])}
        logpath=self.store.root/'runtime'/'backend.log' if getattr(self,'store',None) is not None else None
        log_offset=logpath.stat().st_size if logpath and logpath.exists() else 0
        fields={'text':text,'split_chars':'0','max_new_tokens':'1500','seed':str(seed),'cfg_scale':format(cfg_scale,'g')}
        if applied:
            fields.update(cfg_scale=applied['X-Breeze-Guidance-Base'], guidance_window=window,
                          guidance_release=applied['X-Breeze-Guidance-Release'])
        if sampling:
            fields.update({name:str(value) for name,value in validated_sampling(sampling).items()})
        files=None; fh=None
        if voice:
            ref=Path(voice['reference'])
            if filehash(ref)!=voice['reference_sha256']: raise Error('voice_changed','Saved voice reference changed.')
            fh=ref.open('rb'); files={'ref_audio':('reference.wav',fh,'audio/wav')}; fields['ref_text']=voice['transcript']
            if instruction:fields['instruction']=instruction
        else: fields['instruction']=instruction or 'A calm, clear English audiobook narrator.'
        try:
            with wall_budget(self.REQUEST_SECONDS,self._stop_backend,'speech_deadline','Breeze exceeded its speech request wall-clock budget.'):
                with self.client.stream('POST','/v1/audio/speech',data=fields,files=files) as r:
                    if r.status_code!=200: raise Error('speech_http',f'Breeze speech failed with HTTP {r.status_code}.','Resume; the job retains verified clips.')
                    if r.headers.get('x-sample-rate')!=str(RATE) or r.headers.get('x-sample-format')!='s16le': raise Error('pcm_format','Unexpected streamed PCM format.')
                    if any(r.headers.get(header) != value for header, value in applied.items()):
                        raise Error('frame_guidance_unconfirmed', 'The backend did not confirm the requested frame schedule.')
                    data=bytearray()
                    for chunk in r.iter_bytes():
                        data.extend(chunk)
                        if len(data)>RATE*2*125: raise Error('audio_limit','Breeze exceeded the bounded take duration.')
                import re
                logtext=self.read_log()
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
                actual=self.verify_device(logtext)
                data,receipt=validate_pcm(bytes(data),text); receipt['actual_device']=actual
                if frame_guidance is not None:
                    frames, remainder = divmod(receipt['samples'], FRAME_GUIDANCE_CAPABILITY['frame_samples'])
                    required_frames = frame_guidance['end_frame'] + frame_guidance['release_frames']
                    if remainder or frames < required_frames:
                        raise Error('frame_guidance_unreached',
                                    f'Breeze returned {frames} complete frames. The requested guidance needs {required_frames} complete frames.',
                                    'Compare with the undirected take or use an earlier end frame.')
                    receipt['frame_guidance'] = {**frame_guidance, 'generated_frames':frames,
                                                 'chunk_frames':FRAME_GUIDANCE_CAPABILITY['chunk_frames']}
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

def save_voice(store,name,reference,transcript,description='',origin='clone', *, wash='auto', channel=None):
    import re
    from .core import fault, syncdir, syncfile
    from .jobs import rename_new
    from .reference_preparation import prepare

    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',name): raise Error('voice_name','Voice name must contain 1–64 letters, digits, dash or underscore.')
    transcript=transcript.strip()
    if not transcript: raise Error('transcript_required','Exact corrected reference transcript is required.')
    if len(transcript.encode())>1024: raise Error('reference_transcript_length','The exact reference transcript exceeds the 1024-byte Breeze limit.')
    reference=Path(reference)
    if reference.suffix.lower() not in ('.wav','.flac','.mp3','.m4a','.ogg','.opus'): raise Error('reference_format','Reference must be a local WAV, FLAC, MP3, M4A, OGG or Opus file.')
    if reference.stat().st_size>200*1024*1024: raise Error('reference_size','Reference exceeds 200 MiB.')
    root=store.root/'voices'/name
    if root.exists() or root.is_symlink(): raise Error('voice_exists','A saved voice with this name exists.','Choose a new name.')
    parent=store.root/'runtime'/'voice-staging'
    if parent.is_symlink() or parent.is_junction():
        raise Error('unsafe_path','Voice staging requires an owned directory.')
    parent.mkdir(exist_ok=True)
    stage=parent/name
    owner={'type':'voice_preparation','name':name}
    if stage.exists() or stage.is_symlink():
        if stage.is_symlink() or stage.is_junction() or not stage.is_dir():
            raise Error('unsafe_path','Voice staging is occupied by unrecognized data.')
        if not any(stage.iterdir()):
            stage.rmdir()
        else:
            marker=stage/'owner.json'
            if marker.is_symlink() or not marker.is_file() or loadjson(marker)!=owner:
                raise Error('unsafe_path','Voice staging is occupied by unrecognized data.')
            store.safe_remove(stage)
    stage.mkdir(mode=0o700)
    try:
        writejson(stage/'owner.json',owner)
        target=stage/'reference.wav'
        original=stage/('original'+reference.suffix.lower())
        shutil.copyfile(reference,original)
        if original.stat().st_size>200*1024*1024:
            raise Error('reference_size','Reference exceeds 200 MiB.')
        original_sha=filehash(original)
        preparation=prepare(store,original,target,transcript,original_sha,wash if origin=='clone' else 'off',channel)
        with wave.open(str(target)) as reader:
            seconds=reader.getnframes()/reader.getframerate()
        v={'original_reference':str(root/original.name),'original_reference_sha256':original_sha,
           'name':name,'reference':str(root/target.name),'reference_sha256':filehash(target),
           'transcript':transcript,'description':description,'origin':origin,'language':'en','seconds':seconds,
           'preparation_receipt':str(root/'preparation.json')}
        writejson(stage/'voice.json',v)
        writejson(stage/'preparation.json',{**preparation,'reference_sha256':v['reference_sha256']})
        syncfile(original)
        syncfile(target)
        syncdir(stage)
        fault('before_voice_promote')
        rename_new(stage,root)
        syncdir(root.parent)
        return {**v,'preparation':{'status':preparation['status'],'reason':preparation['reason'],
                                   'profile':preparation.get('profile'),'cache_reused':preparation.get('cache_reused',False)}}
    finally:
        if stage.exists():
            store.safe_remove(stage)
