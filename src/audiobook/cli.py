from __future__ import annotations
import argparse,json,os,signal,subprocess,sys,time,wave
from pathlib import Path
from . import __version__
from .core import Error,Store,RATE,atomic,dumps,loadjson,writejson
from .importers import inspect,cached_inspect
from .planner import plan
from .backend import Engine,doctor,save_voice,setup
from .jobs import cleanup,create,render,repair,summary,excerpt


def emit(value,json_mode=False):
    if json_mode: print(dumps(value),end=''); return
    if isinstance(value,dict) and 'job' in value:
        print(f'{value["job"]} · {value["status"]} · {value.get("title","")}')
        if value.get('output'): print(value['output'])
        if value.get('needs_review'): print(f'{len(value["needs_review"])} unresolved locations; inspect with hamr show {value["job"]} --content')
        if value.get('error'): print(value['error']['message']); print(value['error']['action'])
    else: print(dumps(value),end='')


def print_profiles(rows):
    print('Built-in model choices — downloaded when selected')
    print(f'{"Profile":31} {"Download":>9}  {"VRAM estimate":>15}  Status')
    for row in rows:
        if not row['implemented']: continue
        low,high=row['estimated_peak_vram_gib']
        memory='CPU only' if high==0 else f'{low:g}–{high:g} GiB'
        label='tested on RX 6800 XT' if row['testing_status']=='locally_tested' else 'implemented; experimental'
        print(f'{row["id"]:31} {row["download_bytes"]/1024**3:6.2f} GiB  {memory:>15}  {label}')


def print_doctor(result):
    host=result['hardware']
    print(f'hamr doctor · {host["system"]} {host["arch"]}')
    for gpu in host['gpus']:
        memory=gpu.get('available_vram_bytes')
        detail=f'{memory/1024**3:.1f} GiB available budget/free memory' if memory is not None else 'shared or unknown memory'
        print(f'GPU: {gpu["name"]} · {detail}')
    print(f'Vulkan: {host["vulkan"]["status"]} · {host["vulkan"]["reason"]}')
    if host['ram_available_bytes'] is not None:print(f'System RAM available: {host["ram_available_bytes"]/1024**3:.1f} GiB')
    choice=result['recommended_profile']
    if choice:
        print(f'\nSuggested starting choice: {choice["profile"]} ({choice["confidence"]})')
        row=next(r for r in result['profiles'] if r['id']==choice['profile'])
        print('  '+(row['testing_evidence'] or 'Implemented, experimental: real local rendering qualification is pending.'))
        for reason in choice['reasons']:print('  '+reason)
        print('  '+choice['uncertainty'])
        print('\n'+choice['command'])
    else:
        print('\nNo starting choice could be established:')
        fallback=next((r for r in result['profiles'] if r['id']=='cpu-q4'),None)
        for reason in fallback.get('reasons',[]) if fallback else []:print('  '+reason)
    for note in result['uncertainties']:print('Note: '+note)
    for issue in result['issues']:print('Issue: '+issue['action'])
    print('\nRun hamr setup for a guided choice, or hamr profiles for all built-in variants.')


def choose_backend(store, json_mode):
    if json_mode or not sys.stdin.isatty():
        raise Error('profile_required','Choose a backend profile before downloading.', 'Run hamr doctor, then hamr setup --profile PROFILE. Interactive hamr setup offers a guided choice.')
    result=doctor(store);print_doctor(result)
    rows=[r for r in result['profiles'] if r['implemented']]
    print()
    for i,row in enumerate(rows,1):
        status=row['compatibility'].replace('_',' ')
        testing='locally tested' if row['testing_status']=='locally_tested' else 'experimental'
        print(f'{i:2d}. {row["id"]} · {row["download_bytes"]/1024**3:.2f} GiB download · {status} · {testing}')
        print('    '+row['tradeoff'])
    recommended=result['recommended_profile']
    default=recommended['profile'] if recommended else None
    while True:
        try:answer=input(f'\nChoose a profile number or ID{f" [{default}]" if default else ""}, or q to cancel: ').strip()
        except EOFError:answer='q'
        if answer.lower() in ('q','quit'):
            raise Error('setup_cancelled','Setup cancelled before acquiring any assets.','Run hamr setup when ready to choose a model.')
        if not answer and default:return default
        if answer.isdigit() and 1<=int(answer)<=len(rows):return rows[int(answer)-1]['id']
        if any(r['id']==answer for r in rows):return answer
        print('Please enter a listed number or profile ID, or q to cancel.')

def add_source(p):
    p.add_argument('--document-policy',type=Path,help='Source-hash-bound verified companion decisions as JSON'); p.add_argument('source',type=Path); p.add_argument('--mode',choices=['listen','study','procedure'],default=None); p.add_argument('--profile',dest='listening_profile',help='Saved listening defaults created with hamr profile'); p.add_argument('--section',action='append',default=[],help='Exact chapter=listen|study|procedure'); p.add_argument('--tables',choices=['speak','companion'],default=None); p.add_argument('--code',choices=['speak','companion'],default=None); p.add_argument('--references',choices=['speak','companion'],default=None); p.add_argument('--notes',choices=['after_paragraph','inline','companion'],default=None)
    p.add_argument('--chapters',help='Source chapter occurrences, e.g.5,2-3,2; original order is retained. Use all to reset.')

def policy(a):
    selected={**getattr(a,'saved_policy',{}),**{k:getattr(a,k) for k in ('mode','tables','code','references','notes') if getattr(a,k,None) is not None}}
    sections=dict(selected.get('sections',{}))
    for item in getattr(a,'section',[]):
        name,sep,mode=item.rpartition('=')
        if not sep or not name or mode not in ('listen','study','procedure'): raise Error('section_policy','Use --section EXACT_CHAPTER=listen|study|procedure.')
        sections[name]={'mode':mode}
    if sections: selected['sections']=sections
    if getattr(a,'document_policy',None): selected['document']=loadjson(a.document_policy)
    if getattr(a,'chapters',None) is not None:
        from .selection import parse
        selected['chapters']=parse(a.chapters)
    return selected

def parser():
    p=argparse.ArgumentParser(prog='hamr',description='hamr — local audiobooks with character voices, source review and recoverable rendering. Models acquired only by setup.'); p.add_argument('--version',action='version',version=__version__)
    p.add_argument('--home',type=Path,help='Private app state (or HAMR_HOME; AUDIOBOOK_HOME is also supported)'); p.add_argument('--json',action='store_true',help='Versioned JSON, no prompts')
    subs=p.add_subparsers(dest='command',required=True)
    def sub(name,help):
        q=subs.add_parser(name,help=help); q.add_argument('--json',action='store_true',default=argparse.SUPPRESS); return q
    sub('doctor','Inspect host and dependencies without downloading')
    q=sub('profile','Save explicit reusable listening defaults'); q.add_argument('name'); q.add_argument('--mode',choices=['listen','study','procedure'],default='listen'); q.add_argument('--tables',choices=['speak','companion'],default='speak'); q.add_argument('--code',choices=['speak','companion']); q.add_argument('--references',choices=['speak','companion']); q.add_argument('--notes',choices=['after_paragraph','inline','companion']); q.add_argument('--pronounce',action='append',default=[]); q.add_argument('--section',action='append',default=[])
    q=sub('setup','Choose, download and health-check a model'); q.add_argument('--profile',dest='backend_profile',help='Speech backend ID from hamr profiles; omit for guided selection'); q.add_argument('--cache',type=Path)
    sub('profiles','Show pinned candidate catalog and implemented profiles')
    for name in ['plan','queue','open']:
        q=sub(name,{'plan':'Inspect and plan without model setup','queue':'Persist a headless job','open':'Review and start a document'}[name]); add_source(q)
        if name!='plan': q.add_argument('--voice',help='Saved voice name or cast:CHARACTER'); q.add_argument('--output',type=Path); q.add_argument('--parallel',type=int,choices=range(1,5),default=1,help='Concurrent generation streams; default 1'); q.add_argument('--label',help='Publication label appended to title, for example AI-generated comparison')
        if name=='open': q.add_argument('--yes',action='store_true')
    for name in ['render','resume']:
        q=sub(name,'Run or recover a saved job'); q.add_argument('job'); q.add_argument('--output',type=Path);q.add_argument('--no-synthesis',action='store_true',help='Use verified cached takes only; never start the speech backend');q.add_argument('--max-new-takes',type=int,help='Stop at a durable checkpoint after this many new unique takes')
    q=sub('run','Run the durable speech/CPU-QA/review/publication pipeline');q.add_argument('job');q.add_argument('--asr-python',type=Path,required=True);q.add_argument('--asr-model',type=Path,required=True);q.add_argument('--second-model',type=Path);q.add_argument('--batch-takes',type=int,default=1);q.add_argument('--max-retakes',type=int,default=8);q.add_argument('--max-new-takes',type=int);q.add_argument('--prepare-clear',action='store_true',help='Prepare accounted speech while source issues still hold publication');q.add_argument('--resident',action='store_true',help='Retain the owned model across render batches, CPU review and batch rests');q.add_argument('--batch-rest-seconds',type=float,default=0,help='Pause new generation between resident batches while retaining the model')
    q.add_argument('--overlap-batch-rest',action='store_true',help='Count CPU review after a committed render batch toward its minimum rest interval')
    q=sub('status','List jobs or reconcile ready output'); q.add_argument('job',nargs='?')
    q=sub('show','Inspect saved plan and exact unresolved locations'); q.add_argument('job'); q.add_argument('--content',action='store_true',help='Explicitly export source/script content')
    q=sub('repair','Change only a scoped pronunciation, verified passage or saved voice'); q.add_argument('job'); q.add_argument('--pronounce'); q.add_argument('--repackage',action='store_true'); q.add_argument('--reimport',action='store_true'); q.add_argument('--upgrade-rules',action='store_true'); q.add_argument('--retake'); q.add_argument('--block'); q.add_argument('--text-file',type=Path); q.add_argument('--voice'); q.add_argument('--document-policy',type=Path);q.add_argument('--metadata',type=Path,help='Source-preserving publication metadata overrides as JSON; null clears, inherit resets')
    q.add_argument('--chapters',help='Replace source chapter occurrence selection; all restores complete source order.')
    for state in ['pause','cancel']:
        q=sub(state,'Mark a non-running job '+state+'d'); q.add_argument('job')
    q=sub('cleanup','Report or remove unreferenced owned scratch/cache'); q.add_argument('--apply',action='store_true'); q.add_argument('--purge-job')
    q=sub('worker','Process the durable queue; failed/review jobs do not block others'); q.add_argument('--once',action='store_true'); q.add_argument('--detach',action='store_true')
    q=sub('review','Record or resolve an exact-take listening finding'); q.add_argument('job');action=q.add_mutually_exclusive_group(required=True);action.add_argument('--accept',dest='accept_utterance');action.add_argument('--flag',dest='flag_utterance');q.add_argument('--reason',required=True)
    q=sub('qa','Run unprompted offline CPU ASR against approved speech'); q.add_argument('job'); q.add_argument('--asr-python',type=Path,required=True); q.add_argument('--asr-model',type=Path,required=True); q.add_argument('--all',action='store_true'); q.add_argument('--require-review',action='store_true')
    q=sub('excerpt','Export consecutive committed takes while a render continues'); q.add_argument('job'); q.add_argument('--start',default='u00001'); q.add_argument('--count',type=int,default=4); q.add_argument('--output',type=Path,required=True)
    q=sub('preview','Audition an actual planned passage outside ready output'); q.add_argument('job'); q.add_argument('--utterance',default='u00001'); q.add_argument('--play',action='store_true')
    q=sub('play','Open ready M4B in the real target player'); q.add_argument('job'); q.add_argument('--start',type=float,default=0)
    q=sub('voice','Browse the built-in cast and manage reusable narrators'); v=q.add_subparsers(dest='voice_command',required=True)
    z=v.add_parser('list',help='List saved custom narrators'); z.add_argument('--json',action='store_true',default=argparse.SUPPRESS)
    for command, help_text in (
        ('cast', 'Browse the built-in character cast'),
        ('sample', 'Find or play a built-in listening sample'),
        ('select', 'Select a built-in character for new books'),
    ):
        z = v.add_parser(command, help=help_text)
        z.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
        if command != 'cast':
            z.add_argument('character', help='Character ID from hamr voice cast')
        if command == 'sample':
            z.add_argument('--play', action='store_true')
    for mode in ['design','clone']:
        z=v.add_parser(mode); z.add_argument('--json',action='store_true',default=argparse.SUPPRESS); z.add_argument('name'); z.add_argument('--no-default',action='store_true',help='Keep the current default narrator'); z.add_argument('--transcript-file',type=Path,required=True); z.add_argument('--description',default='A calm, clear English audiobook narrator with a natural, steady delivery.')
        if mode=='clone': z.add_argument('--reference',type=Path,required=True)
    return p


def cast_command(store, args):
    from . import cast

    if args.voice_command == 'cast':
        result = cast.library(store)
        if args.json:
            emit(result, True)
            return
        print('Character cast')
        for entry in result['characters']:
            status = ' · default for new books' if entry['voice'] == result['default_voice'] else ''
            if entry['sample_kind'] == 'reference' and entry['sample_available']:
                status += ' · plays source reference'
            elif not entry['available']:
                status += ' · audio unavailable in this build'
            print(f"{entry['name']} · {entry['id']}{status}")
            print(f"  {entry['description']}")
        print('Listen with hamr voice sample CHARACTER --play')
        print('Select with hamr voice select CHARACTER')
    elif args.voice_command == 'sample':
        result = cast.sample(args.character)
        if args.json:
            emit(result, True)
        else:
            print(result['sample'])
        if args.play:
            from .core import run
            run(['mpv', '--no-video', result['sample']])
    else:
        result = cast.select(store, args.character)
        if args.json:
            emit(result, True)
        else:
            print(f"{result['name']} selected for new books.")


def discard_closed_output():
    # The controlling terminal is gone; avoid a second failure during interpreter flush.
    for stream in (sys.stdout,sys.stderr):
        try:
            target=stream.fileno(); replacement=os.open(os.devnull,os.O_WRONLY)
            if replacement!=target: os.dup2(replacement,target); os.close(replacement)
        except (OSError,ValueError,AttributeError): pass


def main(argv=None):
    a=parser().parse_args(argv); jm=a.json
    def interrupted(signum,frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,interrupted); signal.signal(signal.SIGHUP,interrupted)
    try:
        s=Store(a.home)
        if getattr(a,'listening_profile',None):
            import re
            if not re.fullmatch('[A-Za-z0-9_-]{1,64}',a.listening_profile): raise Error('profile_name','Invalid listening profile name.')
            path=s.root/'profiles'/(a.listening_profile+'.json')
            if not path.is_file(): raise Error('listening_profile_missing',f'Saved listening profile not found: {path}',f'Create it with hamr profile {a.listening_profile}, or omit --profile. Backend profiles belong to hamr setup --profile.')
            saved=loadjson(path); a.saved_policy=saved['policy']; a.aliases=saved['aliases']
        else: a.aliases={}
        if a.command=='doctor':
            result=doctor(s)
            emit(result,True) if jm else print_doctor(result)
        elif a.command=='profiles':
            from .backend import CATALOG
            from .hardware import profile_catalog
            c=loadjson(CATALOG);rows=profile_catalog(c)
            if jm:emit({'schema_version':1,'profiles':rows,'implemented_runtime':'hoppou','automatic_selection':False},True)
            else:
                print_profiles(rows)
                print('\nResearch catalog only (no setup adapter in this build):')
                print(', '.join(r['id'] for r in rows if not r['implemented']))
        elif a.command=='profile':
            import re
            if not re.fullmatch('[A-Za-z0-9_-]{1,64}',a.name): raise Error('profile_name','Invalid profile name.')
            aliases={}
            for pair in a.pronounce:
                src,sep,dst=pair.partition('=')
                if not sep or not src or not dst: raise Error('alias_format','Pronunciation format is SOURCE=SPOKEN.')
                aliases[src]=dst
            value={'schema_version':1,'name':a.name,'policy':policy(a),'aliases':aliases}
            writejson(s.root/'profiles'/(a.name+'.json'),value); emit(value,True)
        elif a.command=='setup':
            selected=a.backend_profile or choose_backend(s,jm)
            if not jm:print(f'Preparing {selected}; downloading only its pinned runtime/model assets as needed.',file=sys.stderr)
            emit(setup(s,selected,a.cache),jm)
        elif a.command=='plan': emit(plan(cached_inspect(a.source,s.root/'extractions'),policy(a),a.aliases),True)
        elif a.command in ('queue','open'):
            pref=s.root/'preferences.json'; voice=a.voice or (loadjson(pref).get('voice') if pref.exists() else None)
            if a.command=='open' and not a.yes:
                if jm or not sys.stdin.isatty(): raise Error('confirmation_required','Headless open requires --yes with explicit/saved settings.','Use plan to inspect, then open --yes or queue.')
                doc=cached_inspect(a.source,s.root/'extractions')
                while True:
                    p=plan(doc,policy(a),a.aliases); counts={role:sum(b.role==role for b in doc.blocks) for role in ['table','code','note','figure']}
                    print(f'{doc.title} · {len(p["chapters"])} chapters · about {p["estimated_minutes"]} minutes\nRecommended: {p["policy"]["mode"]}. Narrator: {voice or "not selected"}.',file=sys.stderr)
                    if counts['table']:
                        example=next(b.source for b in doc.blocks if b.role=='table'); print(f'{counts["table"]} tables: {p["policy"]["tables"]}. Example: {example[:220]}',file=sys.stderr)
                        alternative=plan(doc,{**policy(a),'tables':'companion'},a.aliases)
                        print(f'Keep exact tables in companion: estimated speech delta {alternative["estimated_minutes"]-p["estimated_minutes"]:+.1f} minutes. Enter t to change.',file=sys.stderr)
                    if counts['code']: print(f'{counts["code"]} literal blocks: {p["policy"]["code"]}. Exact commands remain in the timed companion. Enter c to change.',file=sys.stderr)
                    print(f'{len(p["issues"])} locations need review.',file=sys.stderr)
                    answer=input('Start recommended? [y/N, t tables, c literals] ').strip().lower()
                    if answer=='t' and counts['table']: a.tables='companion' if p['policy']['tables']=='speak' else 'speak'; continue
                    if answer=='c' and counts['code']: a.code='companion' if p['policy']['code']=='speak' else 'speak'; continue
                    if answer not in ('y','yes'): return 0
                    break
            result=create(s,a.source,voice,policy(a),a.output,a.aliases,label=a.label,parallel=a.parallel)
            if a.command=='open': result=render(s,result['job'])
            emit(result,jm)
            if result['status']=='needs_review': return 3
        elif a.command in ('render','resume'):
            result=render(s,a.job,a.output,allow_synthesis=not a.no_synthesis,max_new_takes=a.max_new_takes); emit(result,jm)
            if result['status']=='needs_review': return 3
        elif a.command=='run':
            from .pipeline import run as run_pipeline
            result=run_pipeline(s,a.job,a.asr_python,a.asr_model,a.second_model,a.batch_takes,a.max_retakes,a.prepare_clear,a.max_new_takes,resident=a.resident,batch_rest_seconds=a.batch_rest_seconds,overlap_batch_rest=a.overlap_batch_rest);emit(result,jm)
            if result['status']=='needs_review':return 3
        elif a.command=='status':
            result=summary(s,s.get(a.job)) if a.job else {'schema_version':1,'jobs':[summary(s,j) for j in s.jobs()]}; emit(result,jm)
        elif a.command=='show':
            j=s.get(a.job); emit({'schema_version':1,'job':j['id'],'plan':j['plan']} if a.content else summary(s,j),True)
        elif a.command=='repair':
            if a.block and not a.text_file: raise Error('repair_text','--block requires --text-file containing a verified reading.')
            if not any([a.pronounce,a.block,a.voice,a.retake,a.repackage,a.reimport,a.upgrade_rules,a.document_policy,a.metadata,a.chapters]): raise Error('repair_required','Specify a scoped repair, --document-policy, --metadata or --chapters.')
            emit(repair(s,a.job,a.pronounce,a.block,a.text_file.read_text() if a.text_file else None,a.voice,a.retake,a.reimport,a.upgrade_rules,loadjson(a.document_policy) if a.document_policy else None,loadjson(a.metadata) if a.metadata else None,chapters=a.chapters),jm)
        elif a.command in ('pause','cancel'):
            with s.lock():
                j=s.get(a.job); j['status']='paused' if a.command=='pause' else 'cancelled'
                if a.command=='pause':j['error']={'code':'user_paused','message':'Paused by request.','action':f'hamr resume {a.job}, or hamr run with the saved observer options.'}
                s.save(j); emit(summary(s,j),jm)
        elif a.command=='cleanup': emit(cleanup(s,a.purge_job,a.apply),jm)
        elif a.command=='voice':
            if a.voice_command=='list': emit({'schema_version':1,'voices':[{'name':loadjson(p)['name'],'origin':loadjson(p)['origin'],'language':loadjson(p)['language']} for p in (s.root/'voices').glob('*/voice.json')]},True)
            elif a.voice_command in ('cast','sample','select'): cast_command(s,a)
            else:
                transcript=a.transcript_file.read_text()
                with s.lock():
                    if a.voice_command=='design':
                        with Engine(s) as e: data,receipt=e.speech(transcript,instruction=a.description)
                        temp=s.root/'runtime'/'design-preview.wav'
                        with wave.open(str(temp),'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(data)
                        reference=temp
                    else: reference=a.reference
                    v=save_voice(s,a.name,reference,transcript,a.description,a.voice_command)
                    if not a.no_default: writejson(s.root/'preferences.json',{'voice':a.name})
                    if a.voice_command=='design': temp.unlink(missing_ok=True)
                    emit({'schema_version':1,'voice':v['name'],'origin':v['origin'],'seconds':v['seconds'],'reference':v['reference'],'next':'Listen to the reference and verify its transcript before a long render.'},True)
        elif a.command=='worker':
            if a.detach:
                args=[sys.executable,'-m','hamr','--home',str(s.root),'worker']+(['--once'] if a.once else [])
                log=(s.root/'runtime'/'worker.log').open('ab'); child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True); log.close(); emit({'schema_version':1,'pid':child.pid,'log':str(s.root/'runtime'/'worker.log'),'restart':f'hamr --home {s.root} worker --detach'},True)
            else:
                with s.lock('queue-worker'):
                    while True:
                        queued=[j for j in s.jobs() if j['status']=='queued']
                        for j in queued:
                            try: render(s,j['id'])
                            except Error as e: print(dumps({'job':j['id'],'error':e.code}),file=sys.stderr)
                        if a.once: break
                        time.sleep(2)
        elif a.command=='review':
            from .qa import accept,flag
            emit(flag(s,a.job,a.flag_utterance,a.reason) if a.flag_utterance else accept(s,a.job,a.accept_utterance,a.reason),True)
        elif a.command=='qa':
            from .qa import check
            result=check(s,a.job,a.asr_python,a.asr_model,a.all,a.require_review); emit(result,True)
            if result['status']=='needs_review': return 3
        elif a.command=='excerpt': emit(excerpt(s,a.job,a.start,a.count,a.output),True)
        elif a.command=='preview':
            with s.lock():
                j=s.get(a.job); u=next((u for u in j['plan']['utterances'] if u['id']==a.utterance),None)
                if not u: raise Error('unknown_utterance','Unknown utterance ID.')
                if not j['voice']: raise Error('voice_required','Preview needs a saved narrator.')
                with Engine(s,j['backend']) as e: data,receipt=e.speech(u['text'],j['voice'],seed=u.get('seed',42))
                dest=s.path(j)/'preview.wav'
                with wave.open(str(dest),'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(data)
                emit({'schema_version':1,'preview':str(dest),'ready':False,'sources':[b['locator'] for b in j['document']['blocks'] if b['id'] in u['sources']],'receipt':receipt},True)
                if a.play: subprocess.run(['mpv','--no-video',str(dest)],check=True)
        elif a.command=='play':
            j=s.get(a.job); result=summary(s,j)
            if result['status']!='ready': raise Error('not_ready','Only a verified ready output can be played.','Resume or repair the job first.')
            from .core import run
            subprocess.run(['mpv','--no-video',f'--start={a.start}',str(Path(result['output'])/'book.m4b')],check=True)
        return 0
    except Error as e:
        emit({'schema_version':1,'error':{'code':e.code,'message':e.message,'action':e.action}},True); return 2
    except KeyboardInterrupt:
        try:
            emit({'schema_version':1,'status':'paused','action':'Run hamr status, then resume JOB.'},True); sys.stdout.flush()
        except OSError: discard_closed_output()
        return 130
    except FileNotFoundError as e:
        emit({'schema_version':1,'error':{'code':'local_io','message':f'File not found: {e.filename or str(e)}','action':'Check the named path and repeat the command.'}},True); return 2
    except (OSError,ValueError,KeyError) as e:
        emit({'schema_version':1,'error':{'code':'local_io','message':f'Local operation failed ({type(e).__name__}).','action':'Check app/output paths, permissions and free space. Run doctor and resume.'}},True); return 2

if __name__=='__main__': sys.exit(main())
