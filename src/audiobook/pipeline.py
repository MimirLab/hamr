"""Standalone bounded synthesis, CPU observation, review and publication loop."""
from pathlib import Path
from contextlib import contextmanager,ExitStack
import math,time
from .core import Error,key,writejson,loadjson,filehash
from . import jobs,qa,qa_cache
from .speech_compare import comparison_tokens,quantities,negations,mathematical_markers,notation_observation


def binding(job):
    speech=[{k:v for k,v in u.items() if k!='id'} for u in job['plan']['utterances']]
    return key({'document':job['document'],'speech':speech,'policy':job['plan']['policy'],
                'aliases':job['plan']['aliases'],'voice':job['voice'],'backend':job['backend']})


def critical(expected,observed,protected=None,reference=None):
    observed=notation_observation(expected,observed)
    ref=comparison_tokens(expected);hyp=comparison_tokens(observed);result=set()
    if quantities(ref)!=quantities(hyp):result.add('quantity')
    if negations(ref)!=negations(hyp):result.add('negation')
    if qa.misplaced_negation(expected,observed):result.add('negation_scope')
    if qa.content_gaps(expected,observed):result.add('internal_gap')
    if qa.missing_measurement_instruction(expected,observed):result.add('measurement_instruction')
    if qa.reference_leakage(expected,observed,reference):result.add('reference_leakage')
    if mathematical_markers(expected)!=mathematical_markers(observed):result.add('math_or_unit')
    if qa.missing_parenthetical_counts(expected,observed,protected):result.add('count')
    if qa.missing_final_clause(expected,observed) or qa.missing_final_words(expected,observed):result.add('tail')
    return result


def corroborated(expected,first,second,protected=None,reference=None):
    """Both observers must dispute the same expected quantity/marker site."""
    from difflib import SequenceMatcher
    first,second=(notation_observation(expected,observed) for observed in (first,second))
    shared=critical(expected,first,protected,reference)&critical(expected,second,protected,reference)
    if 'quantity' in shared:
        expected_values=quantities(comparison_tokens(expected))
        def sites(observed):
            values=quantities(comparison_tokens(observed));result=set()
            for action,a,b,c,d in SequenceMatcher(None,expected_values,values,autojunk=False).get_opcodes():
                if action=='equal':continue
                result.add((a,b,tuple(expected_values[a:b]) if a!=b else ('inserted',*values[c:d])))
            return result
        if not sites(first)&sites(second):shared.remove('quantity')
    if 'math_or_unit' in shared:
        expected_markers=mathematical_markers(expected)
        def changes(observed):
            markers=mathematical_markers(observed)
            return {(name,expected_markers.get(name,0),markers.get(name,0)) for name in expected_markers.keys()|markers.keys() if expected_markers.get(name,0)!=markers.get(name,0)}
        if not changes(first)&changes(second):shared.remove('math_or_unit')
    return shared


def run(store,jid,python,model,second_model=None,batch_takes=1,max_retakes=8,prepare_clear=False,max_new_takes=None,resident=False,batch_rest_seconds=0,overlap_batch_rest=False,report_progress=False):
    if type(batch_takes) is not int or not 1<=batch_takes<=4096 or not 0<=max_retakes<=32:raise Error('pipeline_budget','Invalid batch or retake budget.')
    if max_new_takes is not None and (type(max_new_takes) is not int or max_new_takes<1):raise Error('pipeline_budget','The run take limit must be positive.')
    if type(resident) is not bool or type(batch_rest_seconds) not in (int,float) or not math.isfinite(batch_rest_seconds) or not 0<=batch_rest_seconds<=600:
        raise Error('pipeline_budget','Resident mode and batch rest must be explicit, finite and bounded to600seconds.')
    if batch_rest_seconds and not resident:raise Error('pipeline_budget','Batch rest requires a resident engine.')
    if type(overlap_batch_rest) is not bool or (overlap_batch_rest and not (resident and batch_rest_seconds)):
        raise Error('pipeline_budget','Overlapping CPU review with rest requires resident mode and a positive batch rest.')
    path=store.path(store.get(jid))/'pipeline.json';started=time.monotonic();generated=0
    if path.is_symlink():raise Error('unsafe_path','Pipeline checkpoint cannot be a symlink.')
    code={str(p):filehash(p) for p in Path(__file__).parent.glob('*.py')}
    from .backend import ResidentEngineSession
    with store.lock('pipeline'), ExitStack() as resources:
        engine_session=resources.enter_context(ResidentEngineSession(store,factory=jobs.Engine)) if resident else None
        job=store.get(jid)
        if job['backend'] is None:
            if not (store.root/'settings.json').exists():raise Error('setup_required','Configure the speech backend before running the pipeline.')
            job['backend']=loadjson(store.root/'settings.json')
        owner=binding(job)
        try:state=loadjson(path)
        except FileNotFoundError:state={'retakes':[],'observed_pcm':{},'runs':[]}
        # Explicit invocation after repair starts a new binding, retaining the
        # job's cumulative automatic-retake history rather than resetting it.
        if state.get('binding')!=owner:state['observed_pcm']={}
        observer=qa_cache.observer_identity(python,model,Path(__file__).with_name('qa_worker.py'))
        if state.get('observer_sha256')!=key(observer):state['observed_pcm']={}
        state.update(observer_sha256=key(observer),observer_options={'python':str(python),'model':str(model),'second_model':str(second_model) if second_model else None})
        state.update(binding=owner,job=jid,plan_sha256=job['plan']['plan_sha256'],phase='starting',error=None,resident_model=resident)
        state['runs'].append({'revision':job['revision'],'started':time.time(),'batch_takes':batch_takes,'max_retakes':max_retakes,'prepare_clear':prepare_clear,'resident':resident,'batch_rest_seconds':batch_rest_seconds,'overlap_batch_rest':overlap_batch_rest})
        last_phase = None
        def progress(phase,**fields):
            nonlocal last_phase
            labels = {'observing': 'Checking rendered speech', 'required_QA': 'Checking the full narration',
                      'assembling': 'Assembling the audiobook'}
            if report_progress and phase != last_phase and phase in labels:
                import sys
                print(f'{jid}: {labels[phase]}', file=sys.stderr, flush=True)
            last_phase = phase
            state.update(phase=phase,updated=time.time(),elapsed_seconds_this_run=time.monotonic()-started,
                         new_takes_this_run=generated,**fields)
            if engine_session is not None:state['resident_engine_starts']=engine_session.starts
            progress_job=store.get(jid)
            current_keys={jobs.clip_key(u,progress_job) for u in progress_job['plan']['utterances']}
            state['observed_unique_takes']=len(current_keys&state['observed_pcm'].keys())
            state['historical_observed_unique_takes']=len(state['observed_pcm'])
            writejson(path,state)
        def wait_writer(operation):
            deadline=time.monotonic()+15
            while True:
                try:return operation()
                except Error as exc:
                    if exc.code!='busy' or time.monotonic()>=deadline:raise
                    progress('waiting_for_writer');time.sleep(.2)
        @contextmanager
        def writer():
            # Another short status/cleanup action can win the writer lock
            # between stages. Wait boundedly without rerendering committed PCM.
            with ExitStack() as stack:
                wait_writer(lambda:stack.enter_context(store.lock()))
                yield
        def current_code():
            if any(not Path(p).is_file() or filehash(Path(p))!=sha for p,sha in code.items()):raise Error('pipeline_code_changed','Application code changed; restart with the matching installed build.')
        def current():
            current_code()
            j=store.get(jid)
            if binding(j)!=owner:raise Error('pipeline_plan_changed','The source, voice or narration plan changed outside this run. Restart on the repaired plan; committed recordings are retained.')
            if j['status']=='cancelled':raise Error('cancelled','This job was cancelled.')
            if j['status']=='paused' and (j.get('error') or {}).get('code')=='user_paused':raise KeyboardInterrupt
            return j
        def observe(selected,chosen_model):
            with writer():
                rows,identity,checkpoint=qa_cache.observe(store,current(),selected,python,chosen_model)
            return rows
        def spoken_observation(row):
            return ' '.join(segment['text'].strip() for segment in row['segments'] if segment['start']<row['audio_seconds']+.1)
        def retake_candidates(rows,j):
            byid={u['id']:u for u in j['plan']['utterances']};candidates=[]
            for row in rows:
                u=byid[row['utterance']];identity=key({'text':u['text'],'sources':u['sources']})
                if critical(u['text'],row['comparison_text'],u.get('protected_counts'),(j.get('voice') or {}).get('transcript')) and sum(t['identity']==identity for t in state['retakes'])<2:candidates.append(row)
            confirmed=[]
            if second_model and candidates and len(state['retakes'])<max_retakes:
                progress('second_observer',candidates=len(candidates));others=observe([byid[r['utterance']] for r in candidates],second_model)
                for row,other in zip(candidates,others):
                    u=byid[row['utterance']]
                    shared=corroborated(u['text'],row['comparison_text'],spoken_observation(other),u.get('protected_counts'),(j.get('voice') or {}).get('transcript'))
                    if shared:confirmed.append((u,shared))
            return confirmed
        def queue_retakes(confirmed):
            nonlocal owner
            for u,reasons in confirmed[:max(0,max_retakes-len(state['retakes']))]:
                current();identity=key({'text':u['text'],'sources':u['sources']})
                wait_writer(lambda:jobs.repair(store,jid,retake=u['id']));j=store.get(jid)
                state['retakes'].append({'identity':identity,'utterance':u['id'],'reasons':sorted(reasons),'at':time.time()})
                owner=binding(j);state.update(binding=owner,plan_sha256=j['plan']['plan_sha256']);progress('bounded_retake_queued')
        def source_hold(j):
            with writer():
                j=current();j['status']='needs_review';j['error']={'code':'source_review','message':'Source regions remain unresolved; verified clear speech is retained.','action':'Inspect the source locations and apply a source-supported repair.'};store.save(j)
            progress('source_review',source_locations=len(j['plan']['issues']),publication_attempted=False)
            return jobs.summary(store,j)
        with writer():
            if (job.get('error') or {}).get('code')=='user_paused':job['error']=None;job['status']='queued'
            job['audio_gate']=True;store.save(job)
        try:
            while True:
                batch_completed=None
                j=current()
                if j['plan']['issues'] and not prepare_clear:return source_hold(j)
                required={jobs.clip_key(u,j) for u in j['plan']['utterances']}
                missing=[k for k in required if not jobs.valid_clip(store,k)]
                progress('rendering' if missing else 'observing',total_unique_takes=len(required),remaining_unique_takes=len(missing))
                if missing:
                    budget=batch_takes if max_new_takes is None else min(batch_takes,max_new_takes-generated)
                    if budget<=0:
                        progress('paused_at_run_limit');return jobs.summary(store,current())
                    result=wait_writer(lambda:jobs.render(store,jid,max_new_takes=budget,**({'engine_session':engine_session} if resident else {})))
                    # Rendering has returned only after all this batch's streams
                    # finished and their complete PCM was durably committed.
                    batch_completed=time.monotonic()
                    generated+=result.get('metrics',{}).get('clips_rendered',0)
                    j=current()
                selected=[];seen=set()
                for u in j['plan']['utterances']:
                    k=jobs.clip_key(u,j)
                    if k in seen:continue
                    seen.add(k);receipt=jobs.valid_clip(store,k)
                    if receipt and state['observed_pcm'].get(k)!=receipt['sha256']:selected.append(u)
                if selected:
                    progress('observing',pending_unique_observations=len(selected))
                    rows=observe(selected,model)
                    for u,row in zip(selected,rows):state['observed_pcm'][jobs.clip_key(u,j)]=row['pcm_sha256']
                    progress('observations_checkpointed',observed_unique_takes=len(state['observed_pcm']),pending_unique_observations=0)
                    # Catch critical omissions in clear work now, rather than
                    # waiting for every remaining chapter to finish. This is
                    # still partial evidence and never publication approval.
                    early=[{'utterance':r['utterance'],'comparison_text':spoken_observation(r)} for r in rows]
                    confirmed=retake_candidates(early,current())
                    if confirmed:queue_retakes(confirmed)
                j=current()
                required={jobs.clip_key(u,j) for u in j['plan']['utterances']}
                remaining=[k for k in required if not jobs.valid_clip(store,k)]
                progress(state['phase'],remaining_unique_takes=len(remaining))
                if remaining:
                    if max_new_takes is not None and generated>=max_new_takes:
                        progress('paused_at_run_limit',remaining_unique_takes=len(remaining));return jobs.summary(store,current())
                    if resident and batch_rest_seconds:
                        now=time.monotonic()
                        credit=min(batch_rest_seconds,max(0,now-batch_completed)) if overlap_batch_rest and batch_completed is not None else 0
                        deadline=now+batch_rest_seconds-credit
                        progress('resident_rest',batch_rest_seconds=batch_rest_seconds,overlap_batch_rest=overlap_batch_rest,
                                 post_render_seconds_counted_as_rest=credit,additional_rest_seconds=max(0,deadline-now))
                        checked_token=None
                        while time.monotonic()<deadline:
                            # Keep cancellation/code checks prompt without
                            # repeatedly decoding a large unchanged source graph.
                            token=store.change_token()
                            if token!=checked_token:
                                current();checked_token=token
                            else:current_code()
                            time.sleep(min(.25,max(0,deadline-time.monotonic())))
                        current()  # A fresh full check before the next iteration.
                    continue
                if j['plan']['issues']:return source_hold(j)
                progress('required_QA')
                wait_writer(lambda:qa.check(store,jid,python,model,all_clips=True,require_review=True));j=current()
                if not j.get('audio_review'):
                    progress('assembling');result=wait_writer(lambda:jobs.render(store,jid,allow_synthesis=False))
                    progress('complete',output=result['output'],acceptances_added=0);return jobs.summary(store,current())
                pending={r['utterance'] for r in j['audio_review']};report=loadjson(store.path(j)/f'qa-r{j["revision"]}.json')
                confirmed=retake_candidates([r for r in report['rows'] if r['utterance'] in pending],j)
                if not confirmed:
                    progress('speech_review',speech_locations=len(j['audio_review']),acceptances_added=0);return jobs.summary(store,j)
                queue_retakes(confirmed)
        except BaseException as exc:
            progress('interrupted' if isinstance(exc,KeyboardInterrupt) else 'stopped',error=str(exc),automatic_boot_resume=False)
            if isinstance(exc,KeyboardInterrupt):
                with writer():
                    j=store.get(jid)
                    if j['status'] not in ('ready','cancelled'):
                        j['status']='paused';j['error']={'code':'pipeline_interrupted','message':'The pipeline stopped; committed audio and observations are retained.','action':f'hamr run {jid} with the saved observer options'};store.save(j)
            raise
