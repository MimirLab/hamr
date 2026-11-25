"""Standalone bounded synthesis, CPU observation, review and publication loop."""
from pathlib import Path
from contextlib import contextmanager,ExitStack
from difflib import SequenceMatcher
import math,time
from .core import Error,key,writejson,loadjson,filehash,fault
from . import jobs,performance,qa,qa_cache
from .asr_fluid import BOOK_RAM_HEADROOM, Recipe, memory_available
from .speech_compare import comparison_tokens,quantities,negations,mathematical_markers,notation_observation,unit_sites,shared_residual_sites


def binding(job):
    speech=[{k:v for k,v in u.items() if k!='id'} for u in job['plan']['utterances']]
    return key({'document':job['document'],'speech':speech,'policy':job['plan']['policy'],
                'aliases':job['plan']['aliases'],'voice':job['voice'],'backend':job['backend'],
                **({'quote_timing_models':job['quote_timing_models']} if 'quote_timing_models' in job else {})})


def critical(expected,observed,protected=None,reference=None):
    observed=notation_observation(expected,observed)
    ref=comparison_tokens(expected);hyp=comparison_tokens(observed);result=set()
    if quantities(ref)!=quantities(hyp):result.add('quantity')
    if negations(ref)!=negations(hyp):result.add('negation')
    if qa.misplaced_negation(expected,observed):result.add('negation_scope')
    if qa.content_gaps(expected,observed):result.add('internal_gap')
    if qa.missing_measurement_instruction(expected,observed):result.add('measurement_instruction')
    if qa.reference_leakage(expected,observed,reference):result.add('reference_leakage')
    if unit_sites(ref,hyp) or mathematical_markers(expected)!=mathematical_markers(observed):result.add('math_or_unit')
    if qa.missing_parenthetical_counts(expected,observed,protected):result.add('count')
    if qa.missing_final_clause(expected,observed) or qa.missing_final_words(expected,observed):result.add('tail')
    return result


def corroborated(expected,first,second,protected=None,reference=None):
    """Both observers must dispute the same expected quantity/marker site."""
    first,second=(notation_observation(expected,observed) for observed in (first,second))
    shared=critical(expected,first,protected,reference)&critical(expected,second,protected,reference)
    if not shared:return shared
    ref,a,b=map(comparison_tokens,(expected,first,second))
    markers=mathematical_markers(first),mathematical_markers(second)
    if a==b and markers[0]==markers[1]:return shared

    # For unequal transcripts, each confirmation needs an independently
    # aligned source site. A broad error category cannot authorize a retake.
    sites={}
    if shared&{'negation','negation_scope'}:
        same=qa.negation_sites(ref,a)&qa.negation_sites(ref,b)
        sites.update(negation=same,negation_scope=same)
    if 'quantity' in shared:
        expected_values=quantities(ref)
        def quantity_sites(tokens):
            values=quantities(tokens)
            return {(i,j,tuple(values[k:l])) for action,i,j,k,l in SequenceMatcher(None,expected_values,values,autojunk=False).get_opcodes()
                    if action!='equal'}
        sites['quantity']=quantity_sites(a)&quantity_sites(b)
    if 'math_or_unit' in shared:
        sites['math_or_unit']=unit_sites(ref,a)&unit_sites(ref,b)
    if 'internal_gap' in shared:
        sites['internal_gap']={tuple(gap['expected_token_range']) for gap in qa.content_gaps(expected,first)} & {
            tuple(gap['expected_token_range']) for gap in qa.content_gaps(expected,second)}
    return {kind for kind in shared if sites.get(kind)}


def substitution_sites(expected, observed):
    """Find replacement words whose source positions have matching neighbors."""
    reference = comparison_tokens(expected)
    heard = comparison_tokens(notation_observation(expected,observed))
    if len(reference)!=len(heard):
        return set()
    edits = SequenceMatcher(None,reference,heard,autojunk=False).get_opcodes()
    if any(action not in ('equal','replace') or i!=k or j!=l for action,i,j,k,l in edits):
        return set()
    return {i for action,i,j,_,_ in edits if action=='replace' and j==i+1
            and ((i and reference[i-1]==heard[i-1]) or (j<len(reference) and reference[j]==heard[j]))}


def run(store,jid,python,model,second_model=None,batch_takes=1,max_retakes=8,prepare_clear=False,max_new_takes=None,resident=False,batch_rest_seconds=0,overlap_batch_rest=False,report_progress=False):
    if type(batch_takes) is not int or not 1<=batch_takes<=4096 or not 0<=max_retakes<=32:raise Error('pipeline_budget','Invalid batch or retake budget.')
    if max_new_takes is not None and (type(max_new_takes) is not int or max_new_takes<1):raise Error('pipeline_budget','The run take limit must be positive.')
    if type(resident) is not bool or type(batch_rest_seconds) not in (int,float) or not math.isfinite(batch_rest_seconds) or not 0<=batch_rest_seconds<=600:
        raise Error('pipeline_budget','Resident mode and batch rest must be explicit, finite and bounded to600seconds.')
    if batch_rest_seconds and not resident:raise Error('pipeline_budget','Batch rest requires a resident engine.')
    if type(overlap_batch_rest) is not bool or (overlap_batch_rest and not (resident and batch_rest_seconds)):
        raise Error('pipeline_budget','Overlapping CPU review with rest requires resident mode and a positive batch rest.')
    job = store.get(jid)
    if job.get('intake',{}).get('state')=='pending':
        result = jobs.finish_intake(store,jid)
        if result['status']=='needs_review':
            return result
    path=store.path(job)/'pipeline.json'
    started = time.monotonic()
    generated = retries = 0
    existing_clips = None
    if path.is_symlink():raise Error('unsafe_path','Pipeline checkpoint cannot be a symlink.')
    code={str(p):filehash(p) for p in Path(__file__).parent.glob('*.py')}
    from .backend import ResidentEngineSession
    with store.lock('pipeline'), ExitStack() as resources:
        engine_session=resources.enter_context(ResidentEngineSession(store,factory=jobs.Engine)) if resident else None
        job=store.get(jid)
        starting_binding=binding(job)
        performance_bound=jobs.bind_speech(store,job)
        owner=binding(job)
        try:state=loadjson(path)
        except FileNotFoundError:state={'retakes':[],'observed_pcm':{},'runs':[]}
        # Explicit invocation after repair starts a new binding, retaining the
        # job's cumulative automatic-retake history rather than resetting it.
        if state.get('binding')!=owner:state['observed_pcm']={}
        observer=qa_cache.observer_identity(python,model,Path(__file__).with_name('qa_worker.py'))
        confirming_observer=None
        if state.get('observer_sha256')!=key(observer):state['observed_pcm']={}
        state.pop('second_observer_sha256',None)
        state.pop('second_observer_status',None)
        state.update(observer_sha256=key(observer),observer_profile=observer.get('profile','whisper'),observer_options={'python':str(python),'model':str(model),'second_model':str(second_model) if second_model else None})
        state.update(binding=owner,job=jid,plan_sha256=job['plan']['plan_sha256'],phase='starting',error=None,resident_model=resident)
        state['runs'].append({'revision':job['revision'],'started':time.time(),'batch_takes':batch_takes,'max_retakes':max_retakes,'prepare_clear':prepare_clear,'resident':resident,'batch_rest_seconds':batch_rest_seconds,'overlap_batch_rest':overlap_batch_rest})
        last_phase = None
        def progress(phase,**fields):
            nonlocal last_phase
            labels = {'observing': 'Checking rendered speech', 'required_QA': 'Checking the full narration',
                      'assembling': 'Assembling the audiobook'}
            if report_progress and phase != last_phase and phase in labels:
                import sys
                profile=f' ({state["observer_profile"]})' if phase!='assembling' else ''
                print(f'{jid}: {labels[phase]}{profile}', file=sys.stderr, flush=True)
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
        def record_run_metrics(j):
            # The assembly pass reuses everything synthesized earlier in
            # this run. Account for reuses only from its starting cache.
            cached = existing_clips or set()
            metrics = j.setdefault('metrics',{})
            metrics.update(
                wall_seconds_this_run=time.monotonic()-started,
                clips_rendered=generated,
                clips_reused=sum(clip in cached for clip in j['clips'].values()),
                retries=retries,
            )
        def finish_without_publication():
            with writer():
                j = current()
                record_run_metrics(j)
                store.save(j)
            return jobs.summary(store,j)
        def observe(selected,chosen_model):
            with writer():
                rows,identity,checkpoint=qa_cache.observe(store,current(),selected,python,chosen_model)
            return rows,identity
        def spoken_observation(row,unit):
            observed=qa_cache.audible_text(row,row['audio_seconds'])
            return performance.lexical_observation(unit,observed)
        def retake_candidates(rows,j):
            nonlocal confirming_observer
            byid={u['id']:u for u in j['plan']['utterances']};candidates=[]
            source=j['document']['source_sha256']
            open_findings=[r for r in j.get('listening_findings',[]) if r['status']=='open']
            listening={r['clip_key'] for r in open_findings}
            planned_readings={r['reading_sha256'] for r in open_findings if 'reading_sha256' in r}
            accepted={(r['clip_key'],r.get('pcm_sha256')) for r in j.get('audio_acceptances',[])
                      if r['source_sha256']==source}
            for row in rows:
                u=byid[row['utterance']];identity=jobs.take_selection(u)
                clip=jobs.clip_key(u,j)
                if (u.get('performance') or clip in listening or (clip,row['pcm_sha256']) in accepted
                        or planned_readings and qa.reading_identity(j,u) in planned_readings):continue
                first=row['comparison_text']
                mismatch=critical(u['text'],first,u.get('protected_counts'),(j.get('voice') or {}).get('transcript'))
                sites = set()
                flags = row.get('flags')
                residual = flags==[qa.RESIDUAL_WORD_REVIEW]
                if residual or flags==[qa.WORD_ERROR_REVIEW]:
                    receipt = jobs.valid_clip(store,clip)
                    timed = (receipt or {}).get('quote_timing',{})
                    if timed.get('selected')=='directed':
                        residual = False
                    elif not residual:
                        sites = substitution_sites(u['text'],first)
                if (mismatch or sites or residual) and sum(t['identity']==identity for t in state['retakes'])<2:
                    candidates.append((row,sites,residual))
            confirmed=[]
            if second_model and candidates and len(state['retakes'])<max_retakes:
                if confirming_observer is None:
                    primary=qa_cache.model_fingerprint(observer)
                    try:
                        secondary=qa_cache.observer_identity(python,second_model,Path(__file__).with_name('qa_worker.py'))
                        other=qa_cache.model_fingerprint(secondary)
                        confirming_observer=secondary if primary and other and primary!=other else False
                        state['second_observer_status']='distinct_model' if confirming_observer else 'duplicate_or_unverified_model'
                        if confirming_observer:
                            state['second_observer_sha256'] = key(secondary)
                    except (Error,OSError):
                        confirming_observer=False
                        state['second_observer_status']='unavailable'
                if not confirming_observer:return confirmed
                progress('second_observer',candidates=len(candidates))
                try:
                    others,observed_identity=observe([byid[row['utterance']] for row,_,_ in candidates],second_model)
                except (FileNotFoundError,Error) as exc:
                    if isinstance(exc,Error) and exc.code not in ('asr_environment','asr_failed','asr_contract'):raise
                    confirming_observer=False
                    state['second_observer_status']='unavailable'
                    return confirmed
                if observed_identity!=confirming_observer:
                    confirming_observer=False
                    state['second_observer_status']='changed_model'
                    return confirmed
                for (row,sites,residual),other in zip(candidates,others):
                    u=byid[row['utterance']]
                    if other['utterance']!=row['utterance'] or other['pcm_sha256']!=row['pcm_sha256']:
                        raise Error('qa_clip_changed','Speech changed between independent observations.',
                                    f'Run hamr resume {jid} to check the current takes.')
                    first,second=row['comparison_text'],spoken_observation(other,u)
                    shared=corroborated(u['text'],first,second,u.get('protected_counts'),(j.get('voice') or {}).get('transcript'))
                    if sites & substitution_sites(u['text'],second):
                        shared.add('word_substitution')
                    witness = None
                    if residual and (aligned:=shared_residual_sites(u['text'],first,second)):
                        shared.add('residual_word_disagreement')
                        witness = {'pcm_sha256':row['pcm_sha256'],
                                   'observer_sha256':[key(observer),key(confirming_observer)],
                                   'sites':[[start,end,list(words)] for start,end,words in sorted(aligned)]}
                    if shared:confirmed.append((u,shared,witness))
            return confirmed
        def queue_retakes(confirmed):
            nonlocal owner
            for u,reasons,witness in confirmed[:max(0,max_retakes-len(state['retakes']))]:
                before=current()
                wait_writer(lambda:jobs.repair(store,jid,retake=u['id'],expect_revision=before['revision'],
                                               automatic={'limit':max_retakes,'reasons':sorted(reasons),
                                                          'word_disagreement':witness}))
                fault('after_automatic_retake_commit')
                j=store.get(jid);state['retakes']=j['automatic_retakes']
                owner=binding(j);state.update(binding=owner,plan_sha256=j['plan']['plan_sha256']);progress('bounded_retake_queued')
        def source_hold():
            with writer():
                j=current();j['status']='needs_review';j['error']={'code':'source_review','message':'Source regions remain unresolved; verified clear speech is retained.','action':'Inspect the source locations and apply a source-supported repair.'}
                record_run_metrics(j)
                store.save(j)
            progress('source_review',source_locations=len(j['plan']['issues']),publication_attempted=False)
            return jobs.summary(store,j)
        with writer():
            latest=store.get(jid)
            if latest['revision']!=job['revision'] or binding(latest)!=starting_binding:
                raise Error('pipeline_plan_changed','The saved job changed before this pipeline started.')
            latest['backend']=job['backend']
            if 'quote_timing_models' in job:
                latest['quote_timing_models']=job['quote_timing_models']
            if performance_bound:
                latest['plan']=job['plan']
                writejson(store.path(latest)/'plan.json',latest['plan'])
            job=latest
            state['retakes']=job.setdefault('automatic_retakes',state.get('retakes',[]))
            if job['status']=='cancelled':raise Error('cancelled','This job was cancelled.')
            if (job.get('error') or {}).get('code')=='user_paused':job['error']=None;job['status']='queued'
            job['audio_gate']=True;store.save(job)
        try:
            while True:
                batch_completed=None
                j=current()
                if j['plan']['issues'] and not prepare_clear:return source_hold()
                required={jobs.clip_key(u,j) for u in j['plan']['utterances']}
                missing={k for k in required if not jobs.valid_clip(store,k)}
                if existing_clips is None:
                    existing_clips = required - missing
                progress('rendering' if missing else 'observing',total_unique_takes=len(required),remaining_unique_takes=len(missing))
                if missing:
                    budget=batch_takes if max_new_takes is None else min(batch_takes,max_new_takes-generated)
                    if budget<=0:
                        progress('paused_at_run_limit');return finish_without_publication()
                    result=wait_writer(lambda:jobs.render(store,jid,max_new_takes=budget,**({'engine_session':engine_session} if resident else {})))
                    # Rendering has returned only after all this batch's streams
                    # finished and their complete PCM was durably committed.
                    batch_completed=time.monotonic()
                    batch_metrics = result.get('metrics',{})
                    generated += batch_metrics.get('clips_rendered',0)
                    retries += batch_metrics.get('retries',0)
                    j=current()
                selected=[];seen=set();unrendered=False
                for u in j['plan']['utterances']:
                    k=jobs.clip_key(u,j)
                    if k in seen:continue
                    seen.add(k);receipt=jobs.valid_clip(store,k)
                    if not receipt:unrendered=True
                    if receipt and state['observed_pcm'].get(k)!=receipt['sha256']:selected.append(u)
                if isinstance(model,Recipe) and engine_session is not None:
                    if not unrendered:
                        engine_session.release()
                    elif selected and (memory_available() or 0)<BOOK_RAM_HEADROOM:
                        progress('speech_check_deferred',reason='Waiting for synthesis to release system memory.')
                        selected=[]
                if selected:
                    progress('observing',pending_unique_observations=len(selected))
                    rows,observed_identity=observe(selected,model)
                    if qa_cache.model_fingerprint(observer) and observed_identity!=observer:
                        raise Error('asr_environment_changed','The primary speech checker changed during the job.',
                                    f'Run hamr resume {jid} with the current checker.')
                    for u,row in zip(selected,rows):state['observed_pcm'][jobs.clip_key(u,j)]=row['pcm_sha256']
                    progress('observations_checkpointed',observed_unique_takes=len(state['observed_pcm']),pending_unique_observations=0)
                    # Catch critical omissions in clear work now, rather than
                    # waiting for every remaining chapter to finish. This is
                    # still partial evidence and never publication approval.
                    early=[{'utterance':r['utterance'],'pcm_sha256':r['pcm_sha256'],
                            'comparison_text':spoken_observation(r,u)} for r,u in zip(rows,selected)]
                    confirmed=retake_candidates(early,current())
                    if confirmed:queue_retakes(confirmed)
                j=current()
                required={jobs.clip_key(u,j) for u in j['plan']['utterances']}
                remaining=[k for k in required if not jobs.valid_clip(store,k)]
                progress(state['phase'],remaining_unique_takes=len(remaining))
                if remaining:
                    if max_new_takes is not None and generated>=max_new_takes:
                        progress('paused_at_run_limit',remaining_unique_takes=len(remaining));return finish_without_publication()
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
                if j['plan']['issues']:return source_hold()
                progress('required_QA')
                wait_writer(lambda:qa.check(store,jid,python,model,all_clips=True,require_review=True));j=current()
                if not j.get('audio_review'):
                    if engine_session is not None:
                        engine_session.release()
                    progress('assembling')
                    result=wait_writer(lambda:jobs.render(store,jid,allow_synthesis=False,record_run_metrics=record_run_metrics))
                    progress('complete' if result['status']=='ready' else 'publication_held',output=result['output'],acceptances_added=0)
                    if result['status']!='ready':
                        return finish_without_publication()
                    return jobs.summary(store,current())
                pending={r['utterance'] for r in j['audio_review']};report=loadjson(store.path(j)/f'qa-r{j["revision"]}.json')
                flagged=[r for r in report['rows'] if r['utterance'] in pending]
                confirmed=retake_candidates(flagged,j)
                if not confirmed:
                    progress('speech_review',speech_locations=len(j['audio_review']),acceptances_added=0);return finish_without_publication()
                queue_retakes(confirmed)
        except BaseException as exc:
            progress('interrupted' if isinstance(exc,KeyboardInterrupt) else 'stopped',error=str(exc),automatic_boot_resume=False)
            if isinstance(exc,KeyboardInterrupt):
                with writer():
                    j=store.get(jid)
                    if j['status'] not in ('ready','cancelled'):
                        j['status']='paused';j['error']={'code':'pipeline_interrupted','message':'The pipeline stopped; committed audio and observations are retained.','action':f'hamr run {jid} with the saved observer options'};store.save(j)
            raise
