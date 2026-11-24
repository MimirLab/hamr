from __future__ import annotations
import ctypes,errno,html,json,os,re,shutil,signal,subprocess,time,uuid
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from contextlib import closing,nullcontext
from dataclasses import asdict
from itertools import groupby
from pathlib import Path
from .core import Block,Document,Error,RATE,Store,atomic,digest,dumps,fault,filehash,key,loadjson,syncdir,syncfile,writejson
from .importers import inspect,cached_inspect,IMPORT_REVISION
from .planner import plan,RULES
from .source_bundle import capture, copy_snapshot, original_name
from .backend import Engine,validate_pcm,validate_request
from .cast import resolve_voice
from . import mastering,mastering_continuity,performance,quote_timing


def rename_new(src,dst):
    # Linux atomic no-replace protects against a destination race, including empty directories.
    if os.name == 'nt':
        from .platform_support import windows_move
        try: windows_move(src,dst,replace=False)
        except FileExistsError as exc:
            raise Error('output_collision','Destination already exists.','Choose another output parent; existing publication is preserved.') from exc
        return
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.renameat2(-100,os.fsencode(src),-100,os.fsencode(dst),1)!=0:
        e=ctypes.get_errno()
        if e==errno.EEXIST: raise Error('output_collision','Destination already exists.','Choose another output parent; existing publication is preserved.')
        raise OSError(e,os.strerror(e),str(dst))
    syncdir(Path(dst).parent)

def document(j):
    d=j['document']; return Document(d['title'],d['format'],d['source_sha256'],[Block(**b) for b in d['blocks']],d['language'],d['inventory'],d['warnings'],d.get('metadata',{}))

def create(store,source,voice=None,policy=None,output=None,aliases=None,label=None,parallel=1,*,
           context_reviewer=None,mastering_mode=mastering.DEFAULT,source_options=None,announce=False,
           preview_sha=None):
    from .intake import inspect_saved

    if mastering_mode not in mastering.MODES:
        raise Error('mastering_mode', 'Choose natural or off for mastering.')
    source = Path(source).absolute()
    saved_voice = resolve_voice(store,voice) if voice else None
    jid = uuid.uuid4().hex[:16]
    path = store.root/'jobs'/jid
    path.mkdir(mode=0o700)
    committed = False
    try:
        snapshot, selection = capture(source,path,**(source_options or {}))
        sha = filehash(snapshot)
        if preview_sha and sha!=preview_sha:
            raise Error('source_changed', 'The source changed after the plan preview.',
                        'Repeat the open command to review the new source.')
        backend = loadjson(store.root/'settings.json') if (store.root/'settings.json').exists() else None
        if parallel!=1:
            if type(parallel) is not int or not 1<=parallel<=4:
                raise Error('parallel_limit','Choose one to four concurrent streams.')
            if backend is None:
                raise Error('setup_required','Configure a backend before selecting concurrent streams.')
            if backend['settings'].get('text_encoder')=='cpu':
                raise Error('parallel_limit','Concurrent CPU text encoding is not qualified.')
            backend['settings'] = {**backend['settings'],'parallel':parallel}
        j = {'schema_version':1,'id':jid,'status':'queued','revision':1,'snapshot':str(snapshot),
             'source_name':source.name,'publication_label':label,'voice':saved_voice,'backend':backend,
             'output_parent':str(Path(output or Path.home()/'Audiobooks').absolute()),
             'clips':{},'repairs':[],'error':None,'created':time.time(),'speech_contract':2,
             'audio_gate':True,'mastering':mastering_mode,'mastering_contract':2}
        expanded = selection.kind!='file' or selection.route not in ('epub','pdf','markdown','text') or selection.route_override
        if expanded:
            pending = {'issues':[],'utterances':[],'aliases':aliases or {},'policy':policy or {},
                       'rule_revision':RULES,'chapters':[],'estimated_minutes':0,
                       'plan_sha256':key({'pending_import':sha})}
            j.update(source_selection=selection.json(),
                     intake={'state':'pending','policy':policy,'aliases':aliases,'context_reviewer':context_reviewer},
                     document=Document(source.stem,selection.route,sha,[]).json(),plan=pending)
            with store.lock():
                store.save(j)
            committed = True
            if announce:
                import sys
                from .installation import command
                print(f'{jid}: original source saved. If interrupted, run:\n'+command(store.root,'resume',jid),
                      file=sys.stderr,flush=True)
            fault('after_source_snapshot')
            return finish_intake(store,jid)
        doc = (inspect_saved(snapshot,selection,store.root/'extractions') if selection.offline
               else cached_inspect(snapshot,store.root/'extractions'))
        finalize_intake(store,j,doc,policy,aliases,context_reviewer)
        committed = True
        return summary(store,j)
    except BaseException:
        if not committed:
            store.safe_remove(path)
        raise


def finalize_intake(store,job,doc,policy,aliases,context_reviewer):
    p = plan(doc,policy,aliases,context_reviewer=context_reviewer)
    if p.get('document_title'):
        doc.title = p['document_title']
    performance.bind(p,job['backend'],job['voice'])
    job.update(document=doc.json(),plan=p)
    quote_timing.bind(store,job)
    writejson(store.path(job)/'plan.json',p)
    store.save(job)


def finish_intake(store,jid):
    from .intake import inspect_saved
    from .installation import command

    with store.lock('intake-'+jid), store.lock():
        job = store.get(jid)
        intake = job.get('intake',{})
        if intake.get('state')!='pending' or job['status']=='cancelled':
            return summary(store,job)
        snapshot = Path(job['snapshot'])
        try:
            if filehash(snapshot)!=job['document']['source_sha256']:
                raise Error('snapshot_changed','The saved source snapshot has changed.')
            doc = inspect_saved(snapshot,job['source_selection'],store.root/'extractions',
                                title=Path(job['source_name']).stem)
            missing = next((item for item in doc.inventory if
                            item.get('error_code') in ('missing_ocr','missing_ocr_data')),None)
            if missing:
                raise Error(missing['error_code'], 'Raster OCR is unavailable for the saved scan. '+missing['reason'])
            if not any(b.source.strip() and b.role not in ('cover','furniture') for b in doc.blocks) and not any(b.issue for b in doc.blocks):
                raise Error('empty_document','The selected book has no narratable text.')
            job['intake'] = {'state':'complete','route':job['source_selection']['route']}
            job.update(status='queued',error=None)
            finalize_intake(store,job,doc,intake['policy'],intake['aliases'],intake['context_reviewer'])
        except Error as exc:
            job.update(intake=intake,status='needs_review',error={
                'code':exc.code,'message':exc.message,
                'action':' '.join(filter(None,(exc.action,command(store.root,'resume',jid))))})
            store.save(job)
        return summary(store,job)


def bind_speech(store,job):
    if job['backend'] is None:
        if not (store.root/'settings.json').is_file():
            raise Error('setup_required','No Breeze backend configured.','Run hamr setup.')
        job['backend']=loadjson(store.root/'settings.json')
    quote_timing.bind(store,job)
    return performance.bind(job['plan'],job['backend'],job['voice'],
                            fallbacks=job.get('performance_fallbacks',()))


def clip_key(u,j):
    text,options=performance.request(u)
    identity={'text':text,'language':u['language'],'voice':j['voice'],'backend':j['backend'],
              'seed':options['seed'],'split_chars':0,'max_new_tokens':1500,
              'cfg_scale':options.get('cfg_scale',1),'codec':'hoppou-s16le-24000',
              'receipt_revision':j.get('speech_contract',1)}
    if active:=performance.request_identity(u):identity['performance']=active
    if quote_timing.active(j,u):
        identity['quote_timing']=[u['quote_timing'],j['quote_timing_models']]
    return key(identity)


def speak(engine,unit,voice):
    text,options=performance.request(unit)
    return engine.speech(text,voice,**options)

def valid_clip(store,k):
    p=store.root/'clips'/k
    if p.is_symlink(): return None
    try:
        r=loadjson(p/'receipt.json'); pcm=p/'audio.pcm'
        if not isinstance(r,dict):return None
        import math
        if type(r.get('samples')) is not int or not 0<r['samples']<=RATE*125 or type(r.get('sample_rate')) is not int or r['sample_rate']!=RATE:return None
        if type(r.get('seconds')) not in (float,int) or not math.isfinite(r['seconds']) or abs(r['seconds']-r['samples']/RATE)>.001:return None
        if pcm.is_symlink() or pcm.stat().st_size!=r['samples']*2 or filehash(pcm)!=r['sha256'] or r['key']!=k: return None
        return r
    except (OSError,ValueError,KeyError): return None

def take_signature(store,j):
    rows=[]
    for k in sorted({clip_key(u,j) for u in j['plan']['utterances']}):
        receipt=valid_clip(store,k)
        if not receipt:return None
        rows.append((k,receipt['sha256'],receipt['samples']))
    return key(rows)

def publication_manifest(path,j):
    if path.is_symlink(): return None
    try:
        m=loadjson(path/'manifest.json')
        required={'book.m4b','companion.json','companion.vtt','source.html','report.json',original_name(j['snapshot'],j.get('source_selection'))}
        if not isinstance(m,dict) or not isinstance(m.get('files'),dict) or not required<=set(m['files']):return None
        if m['job']==j['id'] and m['revision']==j['revision'] and m['plan_sha256']==j['plan']['plan_sha256']:
            return m
    except (OSError,ValueError,KeyError):
        return None
    return None


def verify_publication(path,j):
    manifest = publication_manifest(path,j)
    if manifest is None:
        return False
    try:
        for name,sha in manifest['files'].items():
            if Path(name).name!=name or (path/name).is_symlink() or filehash(path/name)!=sha: return False
        return True
    except (OSError,ValueError,KeyError): return False


def publication_report(path,j):
    """Read the current report using its manifest digest without rehashing the book."""
    manifest = publication_manifest(path,j)
    report = path/'report.json'
    if manifest is None or report.is_symlink():
        return None
    try:
        data = report.read_bytes()
        if digest(data) == manifest['files']['report.json']:
            result = json.loads(data)
            if isinstance(result,dict):
                return result
    except (OSError,ValueError):
        return None
    return None

def final_path(j): return Path(j['output_parent'])/(j['id']+f'-r{j["revision"]}')


def speech_check_pending(jid):
    return {'code':'speech_qa_pending',
            'message':'The narration is saved. Hamr will check it automatically before writing the audiobook. You only need to listen if it flags a passage.',
            'action':f'Run hamr resume {jid} to check the saved audio and finish the book.'}


def speech_check_status(job):
    if not job.get('audio_gate'):
        return 'not_required'
    if job.get('audio_gate_approved_plan')==job['plan']['plan_sha256'] and job.get('audio_gate_approved_takes'):
        return 'passed'
    return 'needs_review' if job.get('audio_review') else 'pending'


def review_error(store,j):
    error=j['error']
    if j.get('intake',{}).get('state')=='pending' and error:
        return error
    code=(error or {}).get('code')
    jid=j['id']
    if code in ('speech_qa_review','listening_review') or (not code and j.get('audio_review')):
        return {'code':code or 'speech_qa_review',
                'message':'Flagged audio needs a listening check before publication. The saved narration is retained.',
                'action':f'Run hamr status {jid} --json for the passage IDs, reasons and report paths.'}
    if code=='source_review' or (code in (None,'speech_qa_pending') and j['plan']['issues']):
        return {'code':'source_review',
                'message':'Some source passages need to be resolved before Hamr can finish the audiobook.',
                'action':f'Run hamr show {jid} --content to find those passages.'}
    if code in (None,'speech_qa_pending'):
        from .checks_setup import configured, recovery
        from .installation import command
        error=speech_check_pending(jid)
        if not configured(store):
            error['action']=recovery(store, job=jid)
        else:
            error['action']='Run this command to check the saved audio and finish the book.\n' + command(store.root, 'resume', jid)
    return error


def summary(store,j):
    if j['status'] in ('rendering','assembling'):
        try:
            # Kernel locks disappear after a crash/reboot; stale PID-file contents do not matter.
            # Recheck under the actual writer lock so a starting worker cannot race this transition.
            with store.lock():
                current=store.get(j['id'])
                if current['status'] in ('rendering','assembling'):
                    stamp=current['updated'];current['status']='paused'
                    current['error']={'code':'worker_interrupted','message':'The rendering worker stopped; committed takes are retained.','action':f'hamr resume {j["id"]}'}
                    store.save_if_current(current,stamp)
                j=store.get(j['id'])
        except Error as e:
            if e.code!='busy':raise
    final=final_path(j)
    if j['status']=='ready' and not verify_publication(final,j):
        j['status']='failed'; j['error']={'code':'publication_corrupt','message':f'The published audiobook is missing or damaged at {final}.','action':'Choose another existing output directory and pass it with --output when resuming.'}
        if not store.save_if_current(j,j['updated']): return summary(store,store.get(j['id']))
    reviews=list(j['plan']['issues'])
    for r in j.get('audio_review',[]):
        locations=[{k:v for k,v in b['locator'].items() if k not in ('exact','rows')} for b in j['document']['blocks'] if b['id'] in r['sources']]
        reviews.append({'utterance':r['utterance'],'location':locations,'reason':' '.join(r['flags']),'report':r['report'],'repair':f'hamr repair {j["id"]} --retake {r["utterance"]}'})
    qa_progress=None
    try:
        recorded=loadjson(store.path(j)/'qa-progress.json')
        if recorded['plan_sha256']==j['plan']['plan_sha256']: qa_progress=recorded
    except (OSError,ValueError,KeyError): pass
    pipeline_progress=None
    try:
        recorded=loadjson(store.path(j)/'pipeline.json')
        if recorded['plan_sha256']==j['plan']['plan_sha256']:
            pipeline_progress={k:v for k,v in recorded.items() if k in ('phase','observer_profile','updated','elapsed_seconds_this_run','new_takes_this_run','total_unique_takes','remaining_unique_takes','observed_unique_takes','source_locations','speech_locations','error')}
            current_keys={clip_key(u,j) for u in j['plan']['utterances']}
            observations=recorded.get('observed_pcm',{})
            pipeline_progress['observed_unique_takes']=len(current_keys&observations.keys())
            pipeline_progress['historical_observed_unique_takes']=len(observations)
            if j['status']=='paused' and (j.get('error') or {}).get('code') in ('worker_interrupted','pipeline_interrupted') and recorded['phase']!='complete':
                pipeline_progress['last_recorded_phase']=recorded['phase'];pipeline_progress['phase']='interrupted'
            elif j['status'] in ('failed','cancelled'):
                # A crash may persist the job error before the next progress
                # checkpoint. Report its terminal state without altering that
                # older checkpoint or inferring whether OS cleanup has finished.
                phase='stopped' if j['status']=='failed' else 'cancelled'
                if recorded['phase']!=phase:pipeline_progress['last_recorded_phase']=recorded['phase']
                pipeline_progress['phase']=phase
                if (j.get('error') or {}).get('message'):pipeline_progress['error']=j['error']['message']
            pipeline_progress['automatic_retakes_used']=len(recorded.get('retakes',[]))
    except (OSError,ValueError,KeyError):pass
    error=review_error(store,j) if j['status']=='needs_review' else j['error']
    if error and error['code'] in ('speech_checks_required', 'asr_profile_unavailable'):
        from .checks_setup import configured, recovery
        from .installation import command
        if error['code']=='speech_checks_required' and configured(store):
            error={**error,'message':'The speech checker is now installed. Resume the saved book.',
                   'action':command(store.root, 'resume', j['id'])}
        else:
            profile='whisper' if error['code']=='asr_profile_unavailable' else None
            action=recovery(store, job=j['id'], profile=profile)
            if profile:
                action='Free memory and resume, or switch to the lower-memory checker.\n' + action
            error={**error,'action':action}
    metrics = {**j.get('metrics', {}), 'semantic_qa':speech_check_status(j)}
    checked = {k:valid_clip(store,k) for k in set(j['clips'].values())}
    result={'schema_version':1,'job':j['id'],'status':j['status'],'revision':j['revision'],'title':j['document']['title'],'source_sha256':j['document']['source_sha256'],'plan_sha256':j['plan']['plan_sha256'],'utterances':len(j['plan']['utterances']),'verified_clips':sum(r is not None for r in checked.values()),'needs_review':reviews,'listening_findings':j.get('listening_findings',[]),'error':error,'output':str(final) if j['status']=='ready' else None,'planned_output':str(final/'book.m4b'),'resume':f'hamr resume {j["id"]}','metrics':metrics,'progress':j.get('progress',{}),'qa_progress':qa_progress,'pipeline_progress':pipeline_progress,'failed_take':j.get('failed_take')}
    result['mastering']=j.get('mastering','off')
    if intake:=j.get('intake'):
        result['intake'] = {'state':intake['state'],'route':j['source_selection']['route']}
    if delivery:=performance.status(j):
        def selection(utterance):
            receipt = checked.get(j['clips'].get(utterance)) or {}
            return receipt.get('quote_timing',{}).get('selected')
        if timed := quote_timing.summarize(j,selection):
            delivery['timed_quotations'] = timed
        result['performance']=delivery
    return result

def generate_takes(engine,j,todo,parallel):
    """Keep at most N requests in flight; the caller alone commits durable state."""
    def take(u,k):
        started=time.monotonic()
        for attempt in range(2):
            try:
                data,receipt=speak(engine,u,j['voice'])
                receipt['request_wall_seconds']=time.monotonic()-started
                return data,receipt,attempt
            except Error as exc:
                if attempt or exc.code in ('speech_deadline','speech_input_limit','backend_timeout','generation_failed','device_unverified'): raise
    pool=ThreadPoolExecutor(max_workers=parallel); pending={}; remaining=iter(todo)
    def refill():
        while len(pending)<parallel:
            item=next(remaining,None)
            if item is None: break
            u,k=item; pending[pool.submit(take,u,k)]=item
    try:
        refill()
        while pending:
            completed,_=wait(pending,return_when=FIRST_COMPLETED)
            for future in completed:
                u,k=pending.pop(future)
                try: data,receipt,attempt=future.result()
                except Error:
                    j['failed_take']={'utterance':u['id'],'key':k,'seed':u.get('seed',42)}
                    raise
                yield u,k,data,receipt,attempt
            refill()
    except BaseException:
        # Stop owned streams before joining threads, including on terminal interruption.
        if hasattr(engine,'__exit__'): engine.__exit__(None,None,None)
        raise
    finally: pool.shutdown(wait=True,cancel_futures=True)


def generate_with_timing(store, job, selected, parallel, engine_session):
    if not any(quote_timing.active(job,unit) for unit,_ in selected):
        context = engine_session.use(job['backend']) if engine_session else Engine(store,job['backend'])
        with context as engine:
            yield from generate_takes(engine,job,selected,parallel)
        return

    from .backend import ResidentEngineSession

    context = nullcontext(engine_session) if engine_session else ResidentEngineSession(store,factory=Engine)
    with context as session:
        def synthesize(text,options):
            try:
                with session.use(job['backend']) as engine:
                    return engine.speech(text,job['voice'],**options)
            finally:
                # Speech recognition runs after the model releases memory.
                session.release()

        for timed, units in groupby(selected,lambda item:bool(quote_timing.active(job,item[0]))):
            if not timed:
                with session.use(job['backend']) as engine:
                    yield from generate_takes(engine,job,units,parallel)
                continue
            session.release()
            for unit,clip in units:
                started = time.monotonic()
                try:
                    data,receipt = quote_timing.render(store,job,unit,clip,synthesize)
                except Error:
                    job['failed_take']={'utterance':unit['id'],'key':clip,'seed':unit.get('seed',42)}
                    raise
                receipt['request_wall_seconds']=time.monotonic()-started
                yield unit,clip,data,receipt,0


def render(store,jid,output=None,allow_synthesis=True,max_new_takes=None,engine_session=None,
           *, record_run_metrics=None):
    if max_new_takes is not None and (type(max_new_takes) is not int or not 1<=max_new_takes<=4096):
        raise Error('render_budget','Choose one to4096 new takes per render batch.')
    if store.get(jid).get('intake',{}).get('state')=='pending':
        result = finish_intake(store,jid)
        if result['status']=='needs_review':
            return result
    with store.lock():
        j=store.get(jid)
        if j['status']=='cancelled': raise Error('cancelled','Job was cancelled.','Repair or create a new job to render it.')
        if output: j['output_parent']=str(Path(output).absolute())
        final=final_path(j)
        if verify_publication(final,j):
            if record_run_metrics:
                record_run_metrics(j)
            if j.get('audio_gate') and j.get('audio_gate_approved_plan')!=j['plan']['plan_sha256']:
                j['status']='needs_review'; store.save(j); return summary(store,j)
            j['status']='ready'; j['error']=None; store.save(j); return summary(store,j)
        if final.exists(): raise Error('output_collision',f'An existing folder at {final} does not match this audiobook job.','Choose another existing output directory and pass it with --output when resuming.')
        if not j['voice']: raise Error('voice_required','Job requires one saved narrator.','Use hamr voice design/clone, then repair JOB --voice NAME.')
        if filehash(Path(j['snapshot']))!=j['document']['source_sha256']: raise Error('snapshot_changed','Immutable source snapshot has changed.')
        if bind_speech(store,j):writejson(store.path(j)/'plan.json',j['plan'])
        started=time.monotonic(); reused=0; rendered=0; retries=0
        j['status']='rendering'; j['error']=None; store.save(j)
        try:
            for dependency in ('ffmpeg','ffprobe'):
                if not shutil.which(dependency): raise Error('missing_dependency',f'Missing executable: {dependency}',f'Install {dependency} in user space and resume.')
            if not Path(j['output_parent']).is_dir(): raise Error('output_unavailable',f'Output directory is unavailable: {j["output_parent"]}','Create or reconnect that directory, then resume.')
            estimated_seconds=j['plan']['estimated_minutes']*60
            expected_clip_bytes=int(estimated_seconds*RATE*2*1.3)
            retained=sum(p.stat().st_size for k in j['clips'].values() if (p:=store.root/'clips'/k/'audio.pcm').is_file())
            if shutil.disk_usage(store.root).free<max(50*1024*1024,expected_clip_bytes-retained): raise Error('disk_space',f'Insufficient clip space at {store.root}.','Free space on the app-state filesystem, then resume.')
            if shutil.disk_usage(j['output_parent']).free<max(50*1024*1024,int(estimated_seconds*10000)): raise Error('disk_space',f'Insufficient output space at {j["output_parent"]}.','Free space on the output filesystem, then resume.')
            # Reap only app-owned attempt debris while no writer can race it.
            scratch=store.path(j)/'scratch'
            if scratch.exists(): store.safe_remove(scratch)
            scratch.mkdir()
            todo={}; occurrences={}; checked={}
            for u in j['plan']['utterances']:
                k=clip_key(u,j); occurrences.setdefault(k,[]).append(u['id'])
                if k not in checked: checked[k]=valid_clip(store,k)
                if checked[k]: j['clips'][u['id']]=k; reused+=1
                else: j['clips'].pop(u['id'],None); todo.setdefault(k,u)
            parallel=j['backend'].get('settings',{}).get('parallel',1)
            j['progress']={'completed_utterances':reused,'total_utterances':len(j['plan']['utterances']),'unique_takes_rendered_this_run':0,'reused_utterances':reused,'elapsed_seconds':time.monotonic()-started,'eta_seconds':None if todo else 0,'parallel':parallel}
            store.save(j)
            if todo:
                if not allow_synthesis:raise Error('synthesis_required','CPU-only resume found missing or invalid speech takes.','Use the selected GPU launcher for missing speech, then repeat resume --no-synthesis. Verified clips are retained.')
                selected=list(todo.items())[:max_new_takes] if max_new_takes is not None else list(todo.items())
                for _,take in selected:
                    request_text,options=performance.request(take)
                    try:validate_request(request_text,j['voice'],options.get('instruction'))
                    except Error:
                        j['failed_take']={'utterance':take['id'],'key':clip_key(take,j),'seed':take.get('seed',42)};raise
                with closing(generate_with_timing(
                        store,j,[(u,k) for k,u in selected],parallel,engine_session)) as takes:
                    for u,k,data,receipt,attempt in takes:
                        retries+=attempt
                        dest=store.root/'clips'/k
                        if dest.exists(): store.safe_remove(dest)
                        temp=scratch/k; temp.mkdir()
                        receipt.update({'key':k,'utterance_sha256':digest(u['text'].encode()),'backend_sha256':key(j['backend']),'voice_sha256':key(j['voice']),'attempts':attempt+1})
                        atomic(temp/'audio.pcm',data); writejson(temp/'receipt.json',receipt); syncdir(temp)
                        fault('before_clip_rename'); rename_new(temp,dest); fault('after_clip_rename')
                        if receipt.get('quote_timing'):
                            store.safe_remove(store.path(j)/'quote-timing'/k)
                        for uid in occurrences[k]: j['clips'][uid]=k
                        j.pop('failed_take',None); rendered+=1
                        elapsed=time.monotonic()-started
                        completed=sum(uid in j['clips'] for uid in (u['id'] for u in j['plan']['utterances']))
                        j['progress']={'completed_utterances':completed,'total_utterances':len(j['plan']['utterances']),'unique_takes_rendered_this_run':rendered,'reused_utterances':reused,'elapsed_seconds':elapsed,'eta_seconds':elapsed/max(1,completed-reused)*(len(j['plan']['utterances'])-completed),'parallel':parallel}
                        store.save(j)
                        print(f'{j["id"]}: verified {completed}/{len(j["plan"]["utterances"])}; ETA {j['progress']['eta_seconds']/60:.1f} min',file=__import__('sys').stderr,flush=True)
            j['metrics']={'wall_seconds_this_run':time.monotonic()-started,'clips_reused':reused,'clips_rendered':rendered,'retries':retries,'accepted_audio_seconds':sum(valid_clip(store,k)['seconds'] for k in j['clips'].values())}
            if max_new_takes is not None and rendered<len(todo):
                j['status']='paused';j['error']={'code':'render_batch_complete','message':'The selected speech batch is complete; verified takes are retained.','action':f'hamr resume {jid} --max-new-takes {max_new_takes}'}
                store.save(j)
                if scratch.exists():store.safe_remove(scratch)
                return summary(store,j)
            from .speech_compare import POLICY as comparison_policy
            current_comparison=j.get('speech_contract',1)<2 or j.get('audio_gate_approved_comparison_policy')==comparison_policy
            if j.get('audio_gate'):
                plan_hash=j['plan']['plan_sha256']
                approved=current_comparison and j.get('audio_gate_approved_plan')==plan_hash
                takes=take_signature(store,j) if approved or j.get('audio_review') else None
                if not approved or j.get('audio_gate_approved_takes')!=takes:
                    checked=(takes is not None and j.get('audio_gate_checked_plan')==plan_hash and j.get('audio_gate_checked_takes')==takes
                             and (j.get('speech_contract',1)<2 or j.get('audio_gate_checked_comparison_policy')==comparison_policy))
                    j['status']='needs_review'
                    j['error']=review_error(store,j) if checked and j.get('audio_review') else speech_check_pending(jid)
                    store.save(j); return summary(store,j)
            if j['plan']['issues']:
                j['status']='needs_review'; store.save(j); return summary(store,j)
            j['status']='assembling'; store.save(j); publish(store,j,record_run_metrics=record_run_metrics)
            fault('before_ready_db'); j['status']='ready'; store.save(j); fault('after_ready_db')
            if scratch.exists(): store.safe_remove(scratch)
            return summary(store,j)
        except KeyboardInterrupt:
            j['status']='paused'; j['error']={'code':'interrupted','message':'Owned work stopped; verified clips are retained.','action':f'hamr resume {jid}'}; store.save(j); raise
        except (Error,OSError) as e:
            j['status']='failed'; j['error']={'code':getattr(e,'code','storage_or_io'),'message':getattr(e,'message',f'{getattr(e,"strerror",None) or "I/O failed"}: {getattr(e,"filename",None) or store.path(j)}'),'action':getattr(e,'action',f'Fix the filesystem problem, then run hamr resume {jid}.')} ; store.save(j)
            raise Error(j['error']['code'],j['error']['message'],j['error']['action']) from e


def timeline(utterances,receipts,edits=None):
    pos=0; rows=[]; chapters=[]
    edits=edits or {}
    for u,r in zip(utterances,receipts,strict=True):
        edit=edits.get(u['id'],{})
        if 'pause_samples' in edit:
            u={**u,'pause_samples':edit['pause_samples'],'planned_pause_samples':u['pause_samples']}
        if not chapters or (chapters[-1]['title'],chapters[-1].get('chapter_id'))!=(u['chapter'],u.get('chapter_id')):
            chapters.append({'title':u['chapter'],'start_sample':pos,**({'chapter_id':u['chapter_id']} if u.get('chapter_id') is not None else {})})
        end=pos+r['samples']; rows.append({**u,'start_sample':pos,'end_sample':end,'start':pos/RATE,'end':end/RATE,'destination':'source.html#'+u['sources'][0]})
        pos=end+u['pause_samples']
    for i,c in enumerate(chapters): c['end_sample']=chapters[i+1]['start_sample'] if i+1<len(chapters) else pos
    return rows,chapters,pos

def escape_meta(s):
    if '\0' in s:raise Error('metadata_control','Metadata contains an unsupported null character.')
    return re.sub(r'([\\=;#\n])',lambda m:'\\'+m[1],s)

def publish(store,j,*,record_run_metrics=None):
    parent=Path(j['output_parent'])
    # Do not recreate a vanished destination mount/directory: require explicit existing parent.
    if not parent.is_dir(): raise Error('output_unavailable',f'Output directory is unavailable: {parent}','Create or reconnect that directory, then resume.')
    final=final_path(j); stage=parent/('.audiobook-'+j['id']+f'-r{j["revision"]}.partial')
    if stage.is_symlink(): raise Error('unsafe_path','Publication staging path is a symlink.')
    if stage.exists():
        try: marker=loadjson(stage/'owner.json')
        except (OSError,ValueError): raise Error('unowned_stage','Existing output staging directory lacks an ownership receipt.')
        if marker!={'job':j['id'],'revision':j['revision']}: raise Error('unowned_stage','Output staging directory belongs to another job.')
        # Only known leaf artifacts; never recursive deletion of destination-controlled contents.
        allowed={'owner.json','book.m4b','companion.json','companion.vtt','source.html','report.json','chapters.ffmeta','encode.log','manifest.json',original_name(j['snapshot'],j.get('source_selection'))}
        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in stage.iterdir()): raise Error('unsafe_stage','Unexpected content in staging; move it aside before resuming.')
        for p in stage.iterdir(): p.unlink()
        stage.rmdir()
    stage.mkdir(mode=0o700); writejson(stage/'owner.json',{'job':j['id'],'revision':j['revision']})
    receipts=[valid_clip(store,j['clips'][u['id']]) for u in j['plan']['utterances']]
    if not receipts or any(r is None for r in receipts): raise Error('clips_missing','Verified clips are missing.','Resume to rerender missing takes.')
    from .installation import command
    from .media import tag_publication,verify_container,verify_audio_decode,packet_hash
    previous=j.get('metadata_repackage');previous_hash=None
    book=stage/'book.m4b'
    units=j['plan']['utterances']
    takes=[(store.root/'clips'/j['clips'][u['id']]/'audio.pcm',u['pause_samples']) for u in units]
    mode=j.get('mastering','off')
    master_report={'mode':mode}
    edits={}
    input_reading=None
    retry=command(store.root,'resume',j['id'],'--no-synthesis')
    disable=command(store.root,'repair',j['id'],'--mastering','off')
    recover=(f'Resolve the audio or storage error, then retry.\n{retry}\n'
             f'To make an edition with mastering off, run this command, then retry the resume command.\n{disable}')
    if previous:
        source=Path(previous['audio'])
        if filehash(source)!=previous['sha256'] or previous['plan_sha256']!=j['plan']['plan_sha256']:
            raise Error('repackage_source_changed','The previous publication changed; original PCM remains available.')
        if mode=='natural':
            prior_report=source.with_name('report.json')
            if filehash(prior_report)!=previous.get('report_sha256'):
                raise Error('repackage_source_changed','The previous mastering report changed.',
                            'Create a fresh edition with repair --mastering natural, then resume.')
            master_report=loadjson(prior_report)['mastering']
            edits=master_report.get('take_levels',{}).get('continuity',{}).get('edits',{})
    elif mode=='natural':
        try:
            input_reading,diagnostics,reused=mastering.analyze(store.path(j)/'mastering-analysis.json',j,takes,receipts)
        except Error as exc:
            raise Error(exc.code,exc.message+' Checked narration is retained.',recover) from exc
        edits=diagnostics.get('continuity',{}).get('edits',{})
        fault('after_mastering_analysis')
    rows,chapters,total=timeline(units,receipts,edits)
    if shutil.disk_usage(parent).free<max(50*1024*1024,total*2//3+Path(j['snapshot']).stat().st_size):
        raise Error('disk_space','Insufficient space on output filesystem.','Free space at the selected output destination and resume.')
    chapter_metadata=';FFMETADATA1\ntitle='+escape_meta(j['document']['title']+(' — '+j['publication_label'] if j.get('publication_label') else ''))+'\nartist='+escape_meta(j['voice']['name'])+'\n'
    for c in chapters:
        chapter_metadata+=f'[CHAPTER]\nTIMEBASE=1/{RATE}\nSTART={c["start_sample"]}\nEND={c["end_sample"]}\ntitle={escape_meta(c["title"])}\n'
    atomic(stage/'chapters.ffmeta',chapter_metadata.encode()); fault('during_assembly')
    common=['ffmpeg','-v','error','-xerror','-nostdin','-y']
    metadata=['-f','ffmetadata','-i',str(stage/'chapters.ffmeta'),'-map','0:a:0',
              '-map_metadata','1','-map_chapters','1']
    output=['-movflags','+faststart+disable_chpl','-f','mp4',str(book)]
    encode_recovery=f'Check the destination storage and FFmpeg, then retry.\n{retry}'

    def encode(gain):
        volume=['-af',f'volume={gain:.2f}dB:precision=double'] if gain else []
        if edits:
            pcm_input=mastering.FLOAT_INPUT
            audio=mastering_continuity.render(takes,units,edits)
        else:
            pcm_input=mastering.PCM_INPUT
            audio=mastering.rendered_pcm(takes)
        mastering.run_audio([*common,*pcm_input,*metadata,*volume,
                             '-ar',str(RATE),'-c:a','aac','-b:a','64k',*output],
                            audio,error_code='encode_failed',message='AAC assembly failed.',action=encode_recovery)

    if previous:
        previous_hash=packet_hash(source)
        mastering.run_audio([*common,'-i',str(source),*metadata,'-c:a','copy',*output],
                            error_code='encode_failed',message='AAC stream copy failed.',action=encode_recovery)
        master_report['audio_stream_copy']=True
    elif input_reading is not None:
        try:
            master_report.update(mastering.encode_checked(book,input_reading,mastering.BOOK,encode),
                                 analysis_cache_reused=reused,take_levels=diagnostics)
        except Error as exc:
            raise Error(exc.code,exc.message+' Checked narration is retained.',recover) from exc
    else:
        encode(0)
    metadata_receipt=tag_publication(book,j)
    metadata_receipt['audio_stream_copy']=bool(previous)
    if previous_hash and metadata_receipt['audio_packet_sha256']!=previous_hash:raise Error('repackage_audio_changed','Metadata-only remux changed audio packets.')
    try:
        probe=verify_container(book,chapters,total)
        decoded_audio=verify_audio_decode(book,total)
    except Error as exc:
        raise Error(exc.code,exc.message+' Checked narration is retained.',
                    f'Rebuild and check the output from the saved speech.\n{retry}') from exc
    actual=float(probe['format']['duration'])
    original=original_name(j['snapshot'],j.get('source_selection'))
    copy_snapshot(Path(j['snapshot']),stage/original)
    if filehash(stage/original)!=j['document']['source_sha256']: raise Error('source_copy','Publication source copy differs from the immutable input.')
    source_links=[{'source_block':b['id'],**link} for b in j['document']['blocks'] for link in b.get('links',[]) if link.get('destinations')]
    source_kind = (j.get('source_selection') or {}).get('kind','file')
    block_locations = {b['id']:b['locator'].get('location_id') or b['id'] for b in j['document']['blocks']}
    source_locations = {block_locations[b['id']]:{**b['locator'],'location_id':block_locations[b['id']]}
                        for b in j['document']['blocks']}
    resources = j['document'].get('metadata',{}).get('source_resources',())
    source_locations.update((resource['location_id'],resource) for resource in resources)
    aliases = {block_id:location for block_id,location in block_locations.items() if block_id!=location}
    writejson(stage/'companion.json',{'schema_version':1,'original_source':original,'source_sha256':j['document']['source_sha256'],'plan_sha256':j['plan']['plan_sha256'],'sample_rate':RATE,'samples':total,'chapters':chapters,'segments':rows,'decisions':j['plan']['decisions'],'source_inventory':j['document']['inventory'],'source_metadata':j['document'].get('metadata',{}),'source_links':source_links,
        'source_kind':source_kind,'source_locations':source_locations,
        **({'source_block_locations':aliases} if aliases else {})})
    def ts(sec):
        ms=round(sec*1000); return f'{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02}.{ms%1000:03}'
    vtt='WEBVTT\n\n'+'\n\n'.join(f'{r["id"]}\n{ts(r["start"])} --> {ts(r["end"])}\n{html.escape(r["text"],quote=False)}\n{r["destination"]}' for r in rows)+'\n'
    atomic(stage/'companion.vtt',vtt.encode())
    pointers={};context_time=0
    for b in j['document']['blocks']:
        direct=next((r['start'] for r in rows if b['id'] in r['sources']),None)
        if direct is not None:context_time=direct
        pointers[b['id']]=context_time
    decisions_by_id={d['block']:d for d in j['plan']['decisions']}
    inline_event_readings={}
    for row in rows:
        active=row.get('performance',{})
        if active.get('event',{}).get('position')=='within':
            for evidence in active.get('evidence',()):
                if evidence['kind']=='authored_sentence_boundary':
                    inline_event_readings[evidence.get('block',row['sources'][0])]=row['text']
    source='<!doctype html><meta charset="utf-8"><title>Timed source companion</title><h1>'+html.escape(j['document']['title'])+'</h1><p><a href="'+original+'">Open original source</a></p><audio controls src="book.m4b"></audio>'
    if j['document']['format']=='pdf':source+='<p>Scanned text can contain reading errors. Open the original page to check a number, table or diagram.</p>'
    for b in j['document']['blocks']:
        location_id = block_locations[b['id']]
        locator = source_locations[location_id]
        page = locator.get('page')
        member = locator.get('inner_member',{}).get('member') or locator.get('member') or locator.get('path')
        labels = [f'Page {page}'] if type(page) is int and page>0 else []
        if member:
            labels.append(member)
        original_link = original
        if labels and source_kind=='file' and Path(original).suffix.lower()=='.pdf' and type(page) is int and page>0:
            original_link += f'#page={page}'
        description = html.escape(' · '.join(labels) or 'Original source')
        source+=f'<section id="{b["id"]}"><h2>{html.escape(b["chapter"])}</h2>'
        source+=f'<p><a href="{html.escape(original_link,quote=True)}">{description}</a> · Source location <code>{html.escape(location_id)}</code></p>'
        source+=f'<a href="book.m4b#t={pointers[b["id"]]:.3f}">Listen near {ts(pointers[b["id"]])}</a><p>{html.escape(dumps(locator))}</p><pre style="white-space:pre-wrap">{html.escape(locator.get("exact",b["source"]))}</pre>'
        if b.get('issue'):source+='<p>Uncertainty in the scanned text: '+html.escape(b['issue'])+'</p>'
        for link in b.get('links',[]):
            for destination in link.get('destinations',[]):
                source+='<p><a href="'+html.escape(destination,quote=True)+'">'+html.escape(link.get('label') or ('Note' if link.get('kind')=='note' else 'Linked source'))+'</a></p>'
        for link in source_links:
            if 'source.html#'+b['id'] in link['destinations']:
                source+='<p><a href="#'+link['source_block']+'">Back to referring passage</a></p>'
        decision=decisions_by_id[b['id']]
        if b['id'] in inline_event_readings:
            source+='<details><summary>Spoken words in this passage</summary><pre style="white-space:pre-wrap">'+html.escape(inline_event_readings[b['id']])+'</pre></details>'
        elif j['document']['format']=='pdf' and decision['action']=='speak' and ' '.join(decision['spoken'].split())!=' '.join(b['source'].split()):
            source+='<details><summary>Reading used in the audiobook</summary><pre style="white-space:pre-wrap">'+html.escape(decision['spoken'])+'</pre><p>'+html.escape(decision['reason'])+'</p></details>'
        source+='</section>'
    if resources:
        source+='<h2>Original package files</h2><ul>'
        for resource in resources:
            ident = html.escape(resource['location_id'],quote=True)
            member = resource.get('inner_member',{}).get('member') or resource['member']
            source+=f'<li id="resource-{ident}">{html.escape(member)} · Source location <code>{ident}</code></li>'
        source+='</ul>'
    atomic(stage/'source.html',source.encode())
    if record_run_metrics:
        record_run_metrics(j)
    metrics = {**j['metrics'], 'semantic_qa':speech_check_status(j)}
    qa_message = ('Container and required offline speech checks passed.' if metrics['semantic_qa']=='passed'
                  else 'Container checks passed. Offline speech checking was not required for this publication.')
    timing = [{'utterance':unit['id'],**receipt['quote_timing']}
              for unit,receipt in zip(units,receipts,strict=True) if receipt.get('quote_timing')]
    echoes = [{'utterance':unit['id'],**unit['fragment_echo']}
               for unit in units if unit.get('fragment_echo')]
    writejson(stage/'report.json',{'schema_version':1,'job':j['id'],'revision':j['revision'],'metrics':metrics,'warnings':j['plan']['warnings']+metadata_receipt['warnings'],'issues':j['plan']['issues'],'qa':qa_message,'backend':j['backend'],'voice':{k:v for k,v in j['voice'].items() if k!='transcript'},'publication_metadata':metadata_receipt,'mastering':master_report,'container_duration':actual,'decoded_audio':decoded_audio,'pcm_samples':total,'created':time.time(),
                                  **({'quote_timing':timing} if timing else {}),
                                  **({'fragment_echoes':echoes} if echoes else {})})
    (stage/'chapters.ffmeta').unlink()
    files={p.name:filehash(p) for p in stage.iterdir() if p.name!='owner.json'}
    writejson(stage/'manifest.json',{'schema_version':1,'job':j['id'],'revision':j['revision'],'plan_sha256':j['plan']['plan_sha256'],'source_sha256':j['document']['source_sha256'],'files':files})
    for p in stage.iterdir():
        syncfile(p)
    syncdir(stage); fault('before_publish_rename'); rename_new(stage,final); fault('after_publish_rename')
    if not verify_publication(final,j): raise Error('publication_validation','Published directory failed checksum validation.')


def take_selection(u):
    identity={'text':u['text'],'sources':u['sources']}
    if active:=performance.request_identity(u):identity['performance']=active
    if timing:=u.get('quote_timing'):identity['quote_timing']=timing
    if echo:=u.get('fragment_echo'):identity['fragment_echo']=echo
    return key(identity)


def repair(store,jid,alias=None,block_id=None,text=None,voice=None,retake=None,reimport=False,upgrade_rules=False,document_policy=None,metadata=None,chapters=None,*,expect_revision=None,context=None,context_review=None,context_reviewer=None,automatic=None,performance_mode=None,mastering_mode=None):
    with store.lock():
        from .core import check_revision
        j=store.get(jid)
        check_revision(j,expect_revision)
        doc=document(j); aliases=dict(j['plan']['aliases'])
        old_plan=j['plan']['plan_sha256'];old_voice=j['voice'];old_output=final_path(j)
        pinned_directions={u['performance_candidate']['id']:u for u in j['plan']['utterances']
                           if 'performance_state' in u}
        takes=j.get('retakes',{});rebased_takes={}
        target=None
        if retake:
            target=next((u for u in j['plan']['utterances'] if u['id']==retake),None)
            if target is None: raise Error('unknown_utterance','Retake ID does not belong to this approved plan.')
        previous={'audio':str(old_output/'book.m4b'),'sha256':filehash(old_output/'book.m4b'),'plan_sha256':old_plan} if verify_publication(old_output,j) else None
        if mastering_mode is not None:
            if mastering_mode not in mastering.MODES:
                raise Error('mastering_mode', 'Choose natural or off for mastering.')
            j['mastering']=mastering_mode
            j['mastering_contract']=2
        if metadata is not None:
            from .media import merge_overrides
            j['metadata_overrides']=merge_overrides(j.get('metadata_overrides',{}),metadata)
        if reimport:
            previous_doc = doc
            if selection:=j.get('source_selection'):
                from .intake import inspect_saved
                fresh = inspect_saved(Path(j['snapshot']),selection,store.root/'extractions',
                                      title=Path(j['source_name']).stem)
            else:
                fresh = cached_inspect(Path(j['snapshot']),store.root/'extractions')
            if fresh.source_sha256!=previous_doc.source_sha256: raise Error('snapshot_changed','Reimport source identity differs from the saved revision.')
            from .source_rebase import rebase
            selected={take_selection(u):u for u in j['plan']['utterances'] if take_selection(u) in takes}
            retained={bid for unit in selected.values() for bid in unit['sources']}
            retained.update(u['performance_candidate']['source']['block'] for u in pinned_directions.values())
            retained.update(entry['source']['block'] for entry in j.get('performance_fallbacks',()))
            if target:retained.update(target['sources'])
            stored=None if document_policy is not None else j['plan']['policy'].get('document')
            j['repairs'],rebased_policy,mapping=rebase(previous_doc,fresh,j['repairs'],stored,retain_sources=retained)
            for original,unit in selected.items():
                replacement=take_selection({**unit,'sources':[mapping[bid] for bid in unit['sources']]})
                rebased_takes[replacement]=takes[original]
                if replacement!=original:takes.pop(original)
            takes.update(rebased_takes)
            if target:target={**target,'sources':[mapping[bid] for bid in target['sources']]}
            pinned_directions=performance.rebase(pinned_directions,mapping,j.get('performance_fallbacks',()),fresh.source_sha256)
            for record in j.get('automatic_retakes',[]):
                if record.get('sources') and all(bid in mapping for bid in record['sources']):
                    record['sources']=[mapping[bid] for bid in record['sources']]
                    record['identity']=take_selection(record)
            if stored is not None:j['plan']['policy']={**j['plan']['policy'],'document':rebased_policy}
            doc=fresh; j['repairs'].append({'kind':'reimport','import_revision':IMPORT_REVISION,'source_sha256':doc.source_sha256,'previous_plan_sha256':j['plan']['plan_sha256'],'source_block_map':mapping})
        if alias:
            src,sep,dst=alias.partition('=')
            if not sep or not src or not dst: raise Error('alias_format','Pronunciation format is SOURCE=SPOKEN.')
            aliases[src]=dst
        if block_id:
            matches=[b for b in doc.blocks if b.id==block_id]
            if not matches: raise Error('unknown_block','Block ID does not belong to this source revision.')
            b=matches[0]; old=b.source; b.source=text; b.issue=None; b.evidence.append('User-verified replacement for this exact source occurrence')
            j['repairs'].append({'kind':'verified_reading','block':block_id,'original':old,
                                 'replacement':text,'source_sha256':doc.source_sha256})
        if voice: j['voice']=resolve_voice(store,voice)
        if target:
            identity=take_selection(target); takes[identity]=target.get('seed',42)+1
            if reimport:rebased_takes[identity]=takes[identity]
            if automatic is not None:
                history=j.setdefault('automatic_retakes',[])
                if len(history)>=automatic['limit'] or sum(r['identity']==identity for r in history)>=2:
                    raise Error('automatic_retake_budget','The saved automatic retake budget has been used.')
                original = {}
                if 'word_substitution' in automatic['reasons']:
                    previous_key = clip_key(target,j)
                    receipt = valid_clip(store,previous_key)
                    if not receipt:
                        raise Error('qa_clip_changed','The take marked for a word retake is no longer available.')
                    original = {'previous_clip':previous_key,'previous_pcm_sha256':receipt['sha256']}
                history.append({'identity':identity,'utterance':retake,'reasons':automatic['reasons'],'at':time.time(),
                                'text':target['text'],'sources':target['sources'],'revision':j['revision']+1,
                                **original,
                                **({'performance':target['performance']} if target.get('performance') else {})})
            j['repairs'].append({'kind':'retake','utterance':retake,'seed':takes[identity]})
        j['retakes']=takes
        refresh=upgrade_rules or performance_mode is not None or context is not None or (context_review is not None or context_reviewer is not None) and j['plan']['rule_revision']!=RULES
        revision=RULES if refresh else j['plan']['rule_revision']
        if refresh: j['repairs'].append({'kind':'rules_upgrade','from':j['plan']['rule_revision'],'to':revision})
        selected_policy=dict(j['plan']['policy'])
        automatic_prose=(performance_mode is not None or upgrade_rules or
                         j['plan'].get('performance',{}).get('automatic_scope')=='prose')
        delivery_context=(None if performance_mode is not None or upgrade_rules else
                          j['plan'].get('performance',{}).get('context_version',1))
        if performance_mode is not None:selected_policy['performance']=performance_mode
        if refresh:selected_policy.pop('context_implementation',None)
        if context is not None:
            selected_policy['context']=context
            selected_policy.pop('context_review',None)
        if context_review is not None:
            selected_policy.pop('context_review_receipt',None)
            selected_policy['context_review']=context_review
        if chapters is not None:
            from .selection import parse
            selected=parse(chapters)
            if selected is None:selected_policy.pop('chapters',None)
            else:selected_policy['chapters']=selected
        if document_policy is not None:
            selected_policy['document']=document_policy
            j['repairs'].append({'kind':'document_policy','policy_sha256':key(document_policy),'source_sha256':doc.source_sha256})
        replan=(reimport or upgrade_rules or any(value is not None for value in (
            alias,block_id,voice,retake,document_policy,chapters,context,context_review,context_reviewer,performance_mode)))
        if replan:
            bind_verified_readings(selected_policy,j['repairs'],doc.source_sha256,block_id)
            j['document']=doc.json(); j['plan']=plan(doc,selected_policy,aliases,rule_revision=revision,
                                                   context_reviewer=context_reviewer,automatic_prose=automatic_prose,
                                                   delivery_context=delivery_context)
            performance.bind(j['plan'],j['backend'],j['voice'],fallbacks=j.get('performance_fallbacks',()),
                             previous=pinned_directions if performance_mode is None and not (upgrade_rules or voice) else None)
        if rebased_takes:
            from collections import Counter
            from .source_rebase import conflict
            destinations=Counter(take_selection(u) for u in j['plan']['utterances'])
            if any(destinations[identity]!=1 for identity in rebased_takes):conflict()
        if j['plan'].get('document_title'):j['document']['title']=j['plan']['document_title']
        if replan:
            for u in j['plan']['utterances']:
                seed=takes.get(take_selection(u))
                if seed: u['seed']=seed
            j['plan'].pop('plan_sha256',None)
            j['plan']['plan_sha256']=key(j['plan'])
        j['revision']+=1; j['status']='queued'; j['error']=None
        j.pop('metadata_repackage',None)
        if previous and mastering_mode is None and old_plan==j['plan']['plan_sha256'] and old_voice==j['voice']:
            if j.get('mastering')=='natural':
                previous['report_sha256']=loadjson(old_output/'manifest.json')['files']['report.json']
            j['metadata_repackage']=previous
        if j.get('audio_gate') and (voice or j.get('audio_gate_checked_plan')!=j['plan']['plan_sha256']):
            # Position IDs may move after rechunking. Keep historical QA reports,
            # but expose no old-position repair commands or premature acceptances.
            j['audio_review']=[];j['audio_gate_approved_plan']=None;j['audio_gate_approved_takes']=None
        j['clips']={}  # content keys recover all unaffected takes without positional coupling
        j.pop('progress',None)  # A repaired plan must not display the prior run as complete.
        j.pop('failed_take',None)  # Position IDs from the former plan are stale.
        writejson(store.path(j)/'plan.json',j['plan']); store.save(j); return summary(store,j)


def bind_verified_readings(policy,repairs,source_sha256,current):
    """Keep the full replacement reading independent of original inline evidence."""
    edits = {edit['block']:edit for edit in repairs if 'block' in edit}
    document_policy = policy.get('document',{})
    if not isinstance(document_policy,dict) or not isinstance(document_policy.get('blocks',{}),dict):
        return  # The planner will report the original policy validation error.
    blocks = dict(document_policy.get('blocks',{}))
    reason = 'User-verified replacement reading.'
    for block,edit in edits.items():
        spoken = edit['replacement']
        if edit.get('kind')!='verified_reading':
            continue
        if spoken.strip() and (block==current or block not in blocks):
            blocks[block] = {'action':'speak','verified':True,'reason':reason,
                             'expected_source_sha256':digest(spoken.encode()),'spoken':spoken}
        elif block==current and not spoken.strip() and isinstance(blocks.get(block),dict):
            if blocks[block].get('reason')==reason and blocks[block].get('spoken')==edit['original']:
                del blocks[block]
    if blocks!=document_policy.get('blocks',{}):
        policy['document'] = {'source_sha256':source_sha256,**document_policy,'blocks':blocks}


def cleanup(store,purge_job=None,apply=False):
    with store.lock():
        jobs=store.jobs(); pins=set()
        for j in jobs:
            if j['id']!=purge_job:
                pins.update(j['clips'].values()); pins.update(clip_key(u,j) for u in j['plan']['utterances'] if j.get('backend') and j.get('voice'))
                # Listening evidence remains inspectable after a retake. Purging
                # its owning job explicitly releases these historical pins.
                pins.update(finding['clip_key'] for finding in j.get('listening_findings',[]))
                pins.update(entry['clip_key'] for entry in j.get('performance_fallbacks',[]))
                pins.update(entry['previous_clip'] for entry in j.get('automatic_retakes',[]) if 'previous_clip' in entry)
        candidates=[]; skipped=[]
        for p in (store.root/'clips').iterdir():
            if p.is_symlink() or p.is_junction(): skipped.append(str(p)); continue
            receipt_path=p/'receipt.json'
            try:
                receipt=loadjson(receipt_path) if p.is_dir() and re.fullmatch('[0-9a-f]{64}',p.name) and not receipt_path.is_symlink() and receipt_path.is_file() else None
                owned=isinstance(receipt,dict) and receipt.get('key')==p.name
            except (OSError,ValueError): owned=False
            if not owned: skipped.append(str(p)); continue
            if p.name not in pins: candidates.append(p)
        for j in jobs:
            scratch=store.path(j)/'scratch'
            if scratch.exists() and not scratch.is_symlink(): candidates.append(scratch)
            timing=store.path(j)/'quote-timing'
            if timing.is_dir() and not (timing.is_symlink() or timing.is_junction()):
                candidates.extend(p for p in timing.iterdir() if p.is_dir() and not (p.is_symlink() or p.is_junction())
                                  and re.fullmatch('[0-9a-f]{64}',p.name) and valid_clip(store,p.name))
        staging=store.root/'runtime'/'voice-staging'
        if staging.is_dir() and not (staging.is_symlink() or staging.is_junction()):
            for stage in staging.iterdir():
                record=stage/'owner.json'
                if stage.is_symlink() or stage.is_junction() or record.is_symlink():
                    skipped.append(str(stage))
                    continue
                try:
                    owned=loadjson(record)=={'type':'voice_preparation','name':stage.name}
                except (OSError,ValueError):
                    owned=False
                (candidates if owned else skipped).append(stage if owned else str(stage))
        if purge_job:
            j=store.get(purge_job)
            candidates.append(store.path(j))
        paths=[str(p) for p in candidates]
        if apply:
            fault('during_cleanup')
            for p in candidates:
                if p.exists(): store.safe_remove(p)
                fault('after_cleanup_item')
            if purge_job:
                with store.db: store.db.execute('DELETE FROM jobs WHERE id=?',(purge_job,))
        return {'schema_version':1,'applied':apply,'candidates':paths,'skipped_unowned_or_symlink':skipped,'preserved':'Sources/voices/pinned clips and published output are retained except the explicitly purged job data.'}


def excerpt(store,jid,start,count,output):
    """Export consecutive committed takes without occupying the generation writer."""
    import tempfile,wave
    j=store.get(jid);utterances=j['plan']['utterances']
    index=next((i for i,u in enumerate(utterances) if u['id']==start),None)
    if index is None:raise Error('unknown_utterance','Unknown excerpt starting utterance.')
    if type(count) is not int or not 1<=count<=20:raise Error('excerpt_count','Choose one to twenty consecutive utterances.')
    selected=utterances[index:index+count]
    if len(selected)!=count:raise Error('excerpt_range','Excerpt extends beyond the approved plan.')
    receipts={};missing=[]
    for u in selected:
        k=clip_key(u,j)
        if k not in receipts:receipts[k]=valid_clip(store,k)
        if not receipts[k]:missing.append(u['id'])
    if missing:raise Error('excerpt_pending','Excerpt awaits committed takes: '+', '.join(missing),'Choose completed consecutive passages or wait for rendering. No passage is skipped.')
    dest=Path(output).absolute()
    if dest.exists() or dest.is_symlink():raise Error('output_collision','Excerpt destination already exists.','Choose a new excerpt directory.')
    if not dest.parent.is_dir():raise Error('output_unavailable','Excerpt parent directory is unavailable.')
    stage=Path(tempfile.mkdtemp(prefix='.audiobook-excerpt-',dir=dest.parent))
    try:
        writejson(stage/'owner.json',{'job':jid,'revision':j['revision'],'role':'experimental_excerpt'})
        pos=0;rows=[]
        with wave.open(str(stage/'audio.wav'),'wb') as w:
            w.setnchannels(1);w.setsampwidth(2);w.setframerate(RATE)
            for i,u in enumerate(selected):
                k=clip_key(u,j);r=receipts[k];data=(store.root/'clips'/k/'audio.pcm').read_bytes()
                if digest(data)!=r['sha256']:raise Error('excerpt_clip_changed','A cached take changed during excerpt export.','Resume the job to recover that take.')
                w.writeframes(data);end=pos+r['samples'];rows.append({**u,'clip_key':k,'pcm_sha256':r['sha256'],'start_sample':pos,'end_sample':end});pos=end
                if i+1<len(selected):w.writeframes(b'\0\0'*u['pause_samples']);pos+=u['pause_samples']
        ids={b for u in selected for b in u['sources']}
        receipt={'schema_version':1,'kind':'experimental_excerpt','ready_publication':False,'job':jid,'revision':j['revision'],'plan_sha256':j['plan']['plan_sha256'],'source_sha256':j['document']['source_sha256'],'source_snapshot':j['snapshot'],'voice_name':j['voice']['name'],'sample_rate':RATE,'samples':pos,'seconds':pos/RATE,'utterances':rows,'source_blocks':[b for b in j['document']['blocks'] if b['id'] in ids],'audio_sha256':filehash(stage/'audio.wav'),'qualification':'Committed signal-checked takes; human listening and full-book qualification are separate.'}
        writejson(stage/'excerpt.json',receipt)
        for p in stage.iterdir():
            syncfile(p)
        syncdir(stage);rename_new(stage,dest)
        return {'schema_version':1,'job':jid,'kind':'experimental_excerpt','ready_publication':False,'audio':str(dest/'audio.wav'),'receipt':str(dest/'excerpt.json'),'utterances':[u['id'] for u in selected],'seconds':pos/RATE}
    finally:
        if stage.exists():
            for name in ('audio.wav','excerpt.json','owner.json'):(stage/name).unlink(missing_ok=True)
            stage.rmdir()
