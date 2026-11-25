from __future__ import annotations
import argparse,json,os,signal,subprocess,sys,time,wave
from pathlib import Path
from . import __version__
from .core import Error,Store,RATE,atomic,dumps,loadjson,writejson
from .importers import inspect,cached_inspect
from .planner import plan
from .backend import Engine,doctor,save_voice,setup
from .cast import preference
from .jobs import bind_speech,cleanup,create,render,repair,speak,summary,excerpt
from .installation import command, shell_command


def emit(value,json_mode=False):
    if json_mode: print(dumps(value),end=''); return
    if isinstance(value,dict) and 'job' in value:
        error=value.get('error') or {}
        status=value['status']
        if status=='needs_review':
            status={'speech_qa_pending':'Speech check pending', 'speech_qa_review':'Listening review needed',
                    'listening_review':'Listening review needed', 'source_review':'Source review needed'}.get(error.get('code'),'Needs review')
        print(f'{value["job"]} · {status} · {value.get("title","")}')
        if value.get('output'): print(Path(value['output']) / 'book.m4b')
        if (delivery:=value.get('performance')) and delivery['fallbacks']:
            print(f'Ordinary reading used after a speech check on {delivery["fallbacks"]} directed passage(s).')
        if error.get('message'): print(error['message'])
        if value['status']=='needs_review' and value.get('planned_output'):
            print(f'Current audiobook publication is on hold. Planned file: {value["planned_output"]}')
        if error.get('action'): print(error['action'])
        if detail:=value.get('speech_review'):
            names=', '.join(detail['first_passages'])
            if names:
                suffix=f' and {detail["count"]-len(detail["first_passages"])} more' if detail['count']>len(detail['first_passages']) else ''
                print(f'Flagged passages: {names}{suffix}')
                print(f'Export a flagged passage as a WAV:\n{detail["export"]}')
            print(f'For grouped counts, report and WAV export commands:\n{detail["command"]}')
    elif isinstance(value,dict) and 'jobs' in value:
        if not value['jobs']: print('No audiobook jobs yet. Run hamr open DOCUMENT.')
        for job in value['jobs']: emit(job)
    else: print(dumps(value),end='')


def print_profiles(rows):
    print('Built-in model choices — downloaded when selected')
    print(f'{"Profile":31} {"Download":>9}  {"VRAM estimate":>15}  Status')
    labels = {'locally_tested': 'tested on RX 6800 XT', 'short_control_tested': 'short control tested',
              'implemented_experimental': 'implemented; experimental'}
    for row in rows:
        if not row['implemented']: continue
        low,high=row['estimated_peak_vram_gib']
        memory='CPU only' if high==0 else f'{low:g}–{high:g} GiB'
        label=labels[row['testing_status']]
        print(f'{row["id"]:31} {row["download_bytes"]/1024**3:6.2f} GiB  {memory:>15}  {label}')


def print_doctor(result):
    from .checks_setup import setup_argv

    root=result['state']
    host=result['hardware']
    print(f'hamr doctor · {host["system"]} {host["arch"]}')
    if installation:=result.get('installation'):
        print(f'Running hamr {installation["version"]} · {installation["invoked_as"]}')
        print(f'Command on PATH: {installation["path_command"] or "unavailable"}')
        if host['system'] != 'Windows':
            print(f'Manual: {installation["manual"] or "unavailable on the manual path"}')
        print(f'Speech checking: {"ready" if result["speech_checks_ready"] else "setup needed"}')
    if diagnostic:=result.get('speech_diagnostic'):
        label='installed' if diagnostic['installed'] else diagnostic['recommended']
        if 'speech_verifier' not in result:print(f'Speech diagnostic: {label}. {diagnostic["reason"]}')
    if verifier:=result.get('speech_verifier'):
        print(f'Speech verifier: {verifier["active"] or "setup needed"}. Recommended: {verifier["recommended"]}.')
        print(f'  {verifier["reason"]}')
        if verifier['active']!=verifier['recommended'] and verifier['requested']=='auto':
            print('  To install the recommended verifier, run:')
            print('  '+shell_command(setup_argv(root)))
    if ocr:=result.get('ocr'):
        print(f'Scanned PDFs: {ocr["active_profile"] or "setup available" if ocr["local_engine"] else "Tesseract required"}')
        print(f'  {ocr["reason"]}')
        if not ocr['ready']: print(f'  {ocr["action"]}')
    if washing:=result.get('voice_preparation'):
        names=', '.join(row['profile'] for row in washing['profiles'] if row['installed'])
        print(f'Voice preparation: {names or "setup available"}.')
        print(f'  {washing["reason"]}')
        if not names and washing['recommended_profile']:
            print(f'  {washing["setup_command"]}')
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
    print('\nFor guided setup, run:')
    print(command(root,'setup'))


def choose_backend(store, json_mode, *, automatic=False, advanced=False, include_checks=True):
    if not automatic and (json_mode or not sys.stdin.isatty()):
        raise Error('profile_required','Select a speech model before downloading.',
                    'Run hamr setup in a terminal, or use --auto or --profile PROFILE in a script.')
    result = doctor(store)
    choice = result['recommended_profile']
    rows = [r for r in result['profiles'] if r['implemented']
            and r['compatibility'] in ('likely', 'tentative')]
    if automatic:
        if choice and choice['automatic_eligible']:
            return choice['profile']
        raise Error('no_automatic_profile','There is no well-established automatic choice for this computer.',
                    'Run hamr setup to choose interactively, or hamr doctor for the hardware details.')
    if not rows:
        print_doctor(result)
        raise Error('no_compatible_profile','No packaged speech model fits the current setup.',
                    'See the hardware and installation notes from hamr doctor.')
    default = choice['profile'] if choice else None
    if default and not advanced:
        row = next(r for r in rows if r['id'] == default)
        if row['settings'].get('device') == 'cpu':
            label = 'Compact model, using your processor'
            reason = 'This choice can make books without a supported graphics device. Rendering may take much longer than playback.'
        elif row['precision'] == 'F16':
            label = 'Full-precision model, using your graphics device'
            reason = 'This computer appears to have room for the full model.'
        else:
            label = 'Compact model, using your graphics device'
            reason = 'The smaller model leaves more room for other programs. Listen to a short passage if voice detail matters.'
        print(f'Suggested starting choice: {label}')
        print(reason)
        if row['testing_status'] == 'implemented_experimental':
            print('This choice is experimental on your platform. Setup will check that it starts.')
        elif row['testing_status'] == 'short_control_tested':
            print('This choice passed a short book test. Long-book speed varies by processor.')
        checking = ' Speech checking adds about 0.60 GiB of models plus runtime dependencies.' if include_checks else ''
        print(f'First download: about {row["download_bytes"]/1024**3:.2f} GiB for speech.{checking}')
        while True:
            try: answer = input('Enter to install, a for model choices, or q to cancel: ').strip().lower()
            except EOFError: answer = 'q'
            if not answer:
                return default
            if answer in ('q', 'quit'):
                raise Error('setup_cancelled','Setup cancelled before acquiring any assets.')
            if answer == 'a':
                break
            print('Press Enter to install, a for choices, or q to cancel.')
    else:
        print_doctor(result)
    for i, row in enumerate(rows, 1):
        print(f'{i:2d}. {row["id"]} · {row["download_bytes"]/1024**3:.2f} GiB · {row["compatibility"]}')
        print('    ' + row['tradeoff'])
    while True:
        try: answer = input(f'Choose a number or model ID{f" [{default}]" if default else ""}, or q to cancel: ').strip()
        except EOFError: answer = 'q'
        if answer.lower() in ('q','quit'):
            raise Error('setup_cancelled','Setup cancelled before acquiring any assets.')
        if not answer and default:
            return default
        if answer.isdecimal() and len(answer) < 4 and 1 <= int(answer) <= len(rows):
            return rows[int(answer)-1]['id']
        if any(row['id'] == answer for row in rows):
            return answer
        print('Please enter a listed number or model ID, or q to cancel.')

def add_source(p):
    p.add_argument('--entry',help='Publication ID when a directory or archive contains more than one book')
    p.add_argument('--format',dest='source_format',help='Reader to use for an ambiguous file type')
    p.add_argument('--encoding',help='Known encoding of plain text, AsciiDoc, Org, reStructuredText, TextBundle or compiled HTML Help')
    p.add_argument('--offline',action='store_true',help='Use only installed source readers and OCR assets')
    p.add_argument('--document-policy',type=Path,help='Source-hash-bound verified companion decisions as JSON'); p.add_argument('source',type=Path); p.add_argument('--mode',choices=['listen','study','procedure'],default=None); p.add_argument('--profile',dest='listening_profile',help='Saved listening defaults created with hamr profile'); p.add_argument('--section',action='append',default=[],help='Exact chapter=listen|study|procedure'); p.add_argument('--tables',choices=['speak','companion'],default=None); p.add_argument('--code',choices=['speak','companion'],default=None); p.add_argument('--references',choices=['speak','companion'],default=None); p.add_argument('--notes',choices=['after_paragraph','inline','companion'],default=None)
    p.add_argument('--chapters',help='Source chapter occurrences, e.g.5,2-3,2; original order is retained. Use all to reset.')
    p.add_argument('--context',type=Path,help='Context cue weights, conventions and review mode as JSON')
    add_performance(p)
    add_context_review(p)


def add_performance(p):
    from .performance import MODES
    p.add_argument('--performance',choices=MODES,metavar='{auto,off}',
                   help='Follow expressive cues (the default) or read plainly (off)')


def add_context_review(p):
    group=p.add_mutually_exclusive_group()
    group.add_argument('--context-review',type=Path,help='Local JSON array of replies to source-bound context review requests')
    group.add_argument('--context-review-endpoint',help='Explicit loopback HTTP candidate reviewer for this command')

def policy(a):
    selected={**getattr(a,'reading_defaults',{}),**getattr(a,'saved_policy',{}),**{k:getattr(a,k) for k in ('mode','tables','code','references','notes','performance') if getattr(a,k,None) is not None}}
    sections=dict(selected.get('sections',{}))
    for item in getattr(a,'section',[]):
        name,sep,mode=item.rpartition('=')
        if not sep or not name or mode not in ('listen','study','procedure'): raise Error('section_policy','Use --section EXACT_CHAPTER=listen|study|procedure.')
        sections[name]={'mode':mode}
    if sections: selected['sections']=sections
    if getattr(a,'document_policy',None): selected['document']=loadjson(a.document_policy)
    if getattr(a,'context',None):
        from .context_config import effective,read
        selected['context']=effective(read(a.context))
    if getattr(a,'context_review',None):
        from .context_config import read
        selected['context_review']=read(a.context_review)
    if getattr(a,'chapters',None) is not None:
        from .selection import parse
        selected['chapters']=parse(a.chapters)
    return selected

def parser():
    from .mastering import MODES as mastering_modes, DEFAULT as mastering_default
    p=argparse.ArgumentParser(prog='hamr',description='hamr — local audiobooks with character voices, source review and recoverable rendering. Models acquired only by setup.'); p.add_argument('--version',action='version',version=__version__)
    p.add_argument('--home',type=Path,help='Private app state (or HAMR_HOME; AUDIOBOOK_HOME is also supported)'); p.add_argument('--json',action='store_true',help='Versioned JSON, no prompts')
    subs=p.add_subparsers(dest='command',required=True,metavar='COMMAND')
    def sub(name,help):
        q=subs.add_parser(name,help=help); q.add_argument('--json',action='store_true',default=argparse.SUPPRESS); return q
    sub('doctor','Inspect host and dependencies without downloading')
    q=sub('settings','Show or change the default reading style for new books')
    q.add_argument('--performance',choices=('auto','off'),help='Set expressive (auto) or plain (off) delivery')
    q.add_argument('--mode',choices=('listen','study','procedure'),help='Set the default document reading mode')
    q=sub('profile','Save explicit reusable listening defaults'); q.add_argument('name'); q.add_argument('--mode',choices=['listen','study','procedure'],default='listen'); q.add_argument('--tables',choices=['speak','companion'],default='speak'); q.add_argument('--code',choices=['speak','companion']); q.add_argument('--references',choices=['speak','companion']); q.add_argument('--notes',choices=['after_paragraph','inline','companion']); q.add_argument('--pronounce',action='append',default=[]); q.add_argument('--section',action='append',default=[])
    q.add_argument('--context',type=Path,help='Save reusable context settings from JSON')
    add_performance(q)
    q=sub('setup','Download and check one speech model')
    selection=q.add_mutually_exclusive_group()
    selection.add_argument('--profile',dest='backend_profile',help='Choose a specific model from hamr profiles')
    selection.add_argument('--auto',action='store_true',help='Install the recommendation if hardware fit and evidence support it')
    selection.add_argument('--choose',action='store_true',help='Show advanced model choices')
    q.add_argument('--cache',type=Path)
    scope=q.add_mutually_exclusive_group()
    scope.add_argument('--engine-only',action='store_true',help='Set up just the speech engine')
    scope.add_argument('--checks-only',action='store_true',help='Set up offline speech checking without an engine profile')
    scope.add_argument('--diagnostic-asr',choices=('auto','fluid-int8'),help='Install only the optional local speech diagnostic')
    scope.add_argument('--ocr-only',action='store_true',help='Set up offline OCR for scanned PDFs')
    scope.add_argument('--wash-only',action='store_true',help='Set up local preparation of custom voice references')
    q.add_argument('--wash-profile',help='Voice preparation profile from hamr doctor (default: auto)')
    from .checks_setup import PROFILES as asr_profiles
    q.add_argument('--asr-profile',choices=asr_profiles,help='Choose offline speech verification (default: auto)')
    q.add_argument('--ocr-profile',choices=('auto','tesseract-fast'),help='Also set up scanned PDF support using this profile')
    sub('profiles','Show pinned candidate catalog and implemented profiles')
    for name in ['plan','queue','open']:
        q=sub(name,{'plan':'Inspect and plan without model setup','queue':'Persist a headless job','open':'Review and start a document'}[name]); add_source(q)
        if name!='plan': q.add_argument('--voice',help='Saved voice name or cast:CHARACTER'); q.add_argument('--output',type=Path); q.add_argument('--parallel',type=int,choices=range(1,5),default=1,help='Concurrent generation streams; default 1'); q.add_argument('--label',help='Publication label appended to title, for example AI-generated comparison')
        if name!='plan':
            q.add_argument('--mastering',choices=mastering_modes,default=mastering_default,
                           help='Balance finished book volume (natural, default) or leave the original playback level (off)')
        if name=='open':
            q.add_argument('--yes',action='store_true')
            q.add_argument('--play',action='store_true',help='Play the M4B after publication')
    q=sub('source','Recover an exact original file or archive member from a finished book')
    q.add_argument('book',type=Path); q.add_argument('location',help='Source location or passage ID from source.html'); q.add_argument('--output',type=Path,required=True)
    for name in ['render','resume']:
        q=sub(name,'Render, check and publish a saved book'); q.add_argument('job'); q.add_argument('--output',type=Path);q.add_argument('--no-synthesis',action='store_true',help='Assemble only verified, already checked takes; never start the speech backend');q.add_argument('--max-new-takes',type=int,help='Use a manual render batch with this limit; resume normally to check and publish')
    q=sub('run','Run the durable speech/CPU-QA/review/publication pipeline');q.add_argument('job');q.add_argument('--asr-python',type=Path);q.add_argument('--asr-model',type=Path);q.add_argument('--second-model',type=Path);q.add_argument('--batch-takes',type=int,default=1);q.add_argument('--max-retakes',type=int,default=8);q.add_argument('--max-new-takes',type=int);q.add_argument('--prepare-clear',action='store_true',help='Prepare accounted speech while source issues still hold publication');q.add_argument('--resident',action='store_true',help='Retain the owned model across render batches, CPU review and batch rests');q.add_argument('--batch-rest-seconds',type=float,default=0,help='Pause new generation between resident batches while retaining the model')
    q.add_argument('--overlap-batch-rest',action='store_true',help='Count CPU review after a committed render batch toward its minimum rest interval')
    q=sub('status','List jobs or reconcile ready output'); q.add_argument('job',nargs='?')
    q=sub('show','Inspect saved plan and exact unresolved locations'); q.add_argument('job'); q.add_argument('--content',action='store_true',help='Explicitly export source/script content')
    q=sub('repair','Change only a scoped pronunciation, verified passage or saved voice'); q.add_argument('job'); q.add_argument('--pronounce'); q.add_argument('--repackage',action='store_true'); q.add_argument('--reimport',action='store_true'); q.add_argument('--upgrade-rules',action='store_true'); q.add_argument('--retake'); q.add_argument('--block'); q.add_argument('--text-file',type=Path); q.add_argument('--voice'); q.add_argument('--document-policy',type=Path);q.add_argument('--metadata',type=Path,help='Source-preserving publication metadata overrides as JSON; null clears, inherit resets')
    q.add_argument('--context',type=Path,help='Replace contextual reading settings and replan'); add_context_review(q)
    add_performance(q)
    q.add_argument('--mastering',choices=mastering_modes,help='Reassemble using the accepted narration with this mastering mode')
    q.add_argument('--chapters',help='Replace source chapter occurrence selection; all restores complete source order.')
    q.add_argument('--expect-revision',type=int,help='Apply only to the specified job revision')
    for state in ['pause','cancel']:
        q=sub(state,'Mark a non-running job '+state+'d'); q.add_argument('job')
    q=sub('cleanup','Report or remove unreferenced owned scratch/cache'); q.add_argument('--apply',action='store_true'); q.add_argument('--purge-job')
    q=sub('worker','Process the durable queue; failed/review jobs do not block others'); q.add_argument('--once',action='store_true'); q.add_argument('--detach',action='store_true')
    q=sub('review','Summarize, listen to or resolve speech-review flags'); q.add_argument('job')
    action=q.add_mutually_exclusive_group()
    action.add_argument('--serve',action='store_true',help='Open a local browser review with playable recordings')
    action.add_argument('--play',dest='play_utterance',metavar='PASSAGE',help='Play one committed recording with mpv')
    action.add_argument('--report',type=Path,help='Write the grouped report as Markdown, or JSON with a .json extension')
    action.add_argument('--accept',dest='accept_utterance');action.add_argument('--flag',dest='flag_utterance')
    q.add_argument('--reason',help='Record what you checked or heard')
    q.add_argument('--kind',choices=('recording','planned_reading'),help='Type of finding with --flag')
    q.add_argument('--expect-pcm',metavar='SHA256',help='Save a decision only for the specified PCM checksum')
    q.add_argument('--expect-revision',type=int,help='Apply only to the specified job revision')
    q=sub('qa','Check saved narration locally; resume normally to check and publish'); q.add_argument('job'); q.add_argument('--asr-python',type=Path,help='Optional Python executable for a custom checker'); q.add_argument('--asr-model',type=Path,help='Optional local model directory for a custom checker'); q.add_argument('--all',action='store_true'); q.add_argument('--require-review',action='store_true',help='Save the check as evidence for publication and hold flagged passages')
    q.add_argument('--recheck',action='store_true',help='Recompare existing exact-audio transcripts without speech generation or recognition')
    q=sub('transcribe','Compare unprompted local transcripts without changing audio or job state')
    q.add_argument('input',help='Local audio file, or a job ID with --utterance')
    q.add_argument('--utterance',help='Read this exact committed passage from the given job')
    q.add_argument('--asr-python',type=Path)
    q.add_argument('--asr-model',type=Path)
    q.add_argument('--second-model',type=Path)
    q=sub('excerpt','Export consecutive committed takes while a render continues'); q.add_argument('job'); q.add_argument('--start',default='u00001'); q.add_argument('--count',type=int,default=4); q.add_argument('--output',type=Path,required=True)
    q=sub('preview','Audition an actual planned passage outside ready output'); q.add_argument('job'); q.add_argument('--utterance',default='u00001'); q.add_argument('--play',action='store_true')
    q.add_argument('--compare',type=Path,help='Audition named overrides from a JSON file without changing the job')
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
        if mode=='clone':
            z.add_argument('--reference',type=Path,required=True)
            z.add_argument('--wash',default='auto',help='Use an installed qualified treatment when needed, off, or an explicit profile')
            z.add_argument('--channel',type=int,help='Use one source channel, numbered from 1')
    sub('completion','Print shell completion').add_argument('shell',choices=('bash','zsh','fish'))
    from .operator_cli import add_commands
    add_commands(subs)
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
            if entry['sample_kind'] == 'introduction' and entry['sample_available']:
                status += ' · introduction available'
            elif entry['sample_kind'] == 'reference' and entry['sample_available']:
                status += ' · plays source reference'
            elif not entry['sample_available']:
                status += ' · preview unavailable'
            if not entry['available']:
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
    if os.name == 'nt':
        for stream in (sys.stdout,sys.stderr):
            if hasattr(stream,'reconfigure'):
                stream.reconfigure(encoding='utf-8')
    try:
        return dispatch(argv)
    except BrokenPipeError:
        discard_closed_output()
        return 0


def emit_error(error, json_mode):
    if json_mode:
        emit({'schema_version':1,'error':{'code':error.code,'message':error.message,'action':error.action, **({'candidates':error.candidates} if hasattr(error,'candidates') else {})}},True)
    else:
        print(f'hamr: {error.message}', file=sys.stderr)
        if error.action: print(error.action, file=sys.stderr)
    return 2


def dispatch(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    p = parser()
    if arguments == ['--help-json']:
        from .operator_cli import capabilities
        emit(capabilities(p), True)
        return 0
    a=p.parse_args(arguments); jm=a.json
    def interrupted(signum,frame): raise KeyboardInterrupt
    for name in ('SIGTERM','SIGHUP','SIGBREAK'):
        if (handler:=getattr(signal,name,None)) is not None:
            signal.signal(handler,interrupted)
    try:
        if a.command in ('agent','completion'):
            from .operator_cli import dispatch as operator_dispatch
            return operator_dispatch(p,a)
        s=Store(a.home)
        if a.command in ('plan','queue','open'):
            a.reading_defaults=preference(s).get('reading',{})
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
        elif a.command=='settings':
            changes={name:getattr(a,name) for name in ('mode','performance') if getattr(a,name) is not None}
            if changes:
                with s.lock():
                    saved=preference(s)
                    reading={**saved.get('reading',{}),**changes}
                    writejson(s.root/'preferences.json',{**saved,'reading':reading})
            else:
                reading=preference(s).get('reading',{})
            effective={'mode':'listen','performance':'auto',**reading}
            if jm: emit({'schema_version':1,'reading':effective},True)
            else:
                print('Delivery: '+{'auto':'Expressive','off':'Plain'}[effective['performance']])
                print('Reading mode: '+effective['mode'])
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
            from .checks_setup import preflight, provision, setup_argv, speech_advice
            from .ocr_setup import provision as provision_ocr
            from .setup_progress import SetupProgress
            progress=SetupProgress()
            asr_options={'asr_profile':a.asr_profile} if a.asr_profile else {}
            if a.wash_profile and not a.wash_only:
                raise Error('setup_scope','--wash-profile requires --wash-only.')
            if a.wash_only and (a.asr_profile or a.ocr_profile or a.backend_profile or a.auto or a.choose):
                raise Error('setup_scope','--wash-only sets up voice preparation independently.')
            if a.asr_profile and (a.engine_only or a.ocr_only or a.diagnostic_asr):
                raise Error('setup_scope','--asr-profile requires ordinary setup or --checks-only.')
            if a.ocr_profile and (a.checks_only or a.engine_only or a.diagnostic_asr):
                raise Error('setup_scope','--ocr-profile needs ordinary setup or --ocr-only.')
            if a.wash_only:
                from .wash_setup import provision as provision_wash
                with s.lock():
                    result={'schema_version':1,'voice_preparation':provision_wash(s,progress,a.cache,a.wash_profile or 'auto')}
            elif a.ocr_only:
                if a.backend_profile or a.auto or a.choose:
                    raise Error('setup_scope','--ocr-only does not select a speech engine.')
                with s.lock():
                    result={'schema_version':1,'ocr':provision_ocr(s,progress,a.cache,a.ocr_profile or 'auto')}
            elif a.checks_only:
                if a.backend_profile or a.auto or a.choose:
                    raise Error('setup_scope', '--checks-only does not select a speech engine.')
                with s.lock():
                    result={'schema_version':1,'speech_checks':provision(s,progress,a.cache,**asr_options)}
            elif a.diagnostic_asr:
                if a.backend_profile or a.auto or a.choose:
                    raise Error('setup_scope','--diagnostic-asr installs the speech diagnostic on its own.')
                from .asr_fluid import advice as diagnostic_advice, provision as provision_diagnostic
                from .hardware import inspect_host
                fit=diagnostic_advice(s,inspect_host())
                if not fit['fits']:
                    if a.diagnostic_asr!='auto':
                        raise Error('asr_diagnostic_fit',fit['reason'],
                                    'To install the Whisper checker, run:\n'+shell_command(setup_argv(s.root,'whisper')))
                    result={'schema_version':1,'speech_diagnostic_advice':fit}
                else:
                    with s.lock():
                        result={'schema_version':1,'speech_diagnostic':provision_diagnostic(s,progress,a.cache),
                                'speech_diagnostic_advice':fit}
            else:
                selected=a.backend_profile or choose_backend(s,jm,automatic=a.auto,advanced=a.choose,
                                                            include_checks=not a.engine_only)
                if not a.engine_only: preflight()
                stage=None if a.engine_only else lambda: provision(s,progress,a.cache,activate=False,**asr_options)
                ocr_profile=a.ocr_profile
                if not ocr_profile and not (a.engine_only or jm or a.auto) and sys.stdin.isatty():
                    from .ocr_setup import MODEL_BYTES, NOTICE_BYTES
                    from .pdf_ocr import tesseract_binary
                    if tesseract_binary()[0].is_file():
                        megabytes=(MODEL_BYTES+NOTICE_BYTES) / 1_000_000
                        try: answer=input(f'Add offline scanned PDF support using installed Tesseract ({megabytes:.1f} MB)? [Y/n] ').strip().lower()
                        except EOFError: answer='n'
                        if answer in ('','y','yes'): ocr_profile='auto'
                    else:
                        print('Scanned PDFs can be enabled after installing Tesseract. Run hamr setup --ocr-only.')
                ocr_stage=None
                if ocr_profile:
                    from .ocr_setup import preflight as preflight_ocr
                    preflight_ocr(ocr_profile)
                    ocr_stage=lambda: provision_ocr(s,progress,a.cache,ocr_profile,activate=False)
                result=setup(s,selected,a.cache,progress=progress,before_activate=stage,before_ocr_activate=ocr_stage)
            if result.get('speech_checks'):
                result['speech_verifier']=speech_advice(s)
            if jm: emit(result,True)
            else:
                if result.get('profile'): print(f'Speech engine ready: {result["profile"]}')
                if checks:=result.get('speech_checks'):
                    print(f'Offline speech verifier ready: {result["speech_verifier"]["active"]}.')
                    if reason:=checks.get('selection_reason'):print(reason)
                if result.get('ocr'): print('Offline scanned PDF support ready.')
                if result.get('voice_preparation'):
                    print(f'Local voice preparation ready: {result["voice_preparation"]["profile"]}.')
                if result.get('speech_diagnostic'):
                    print('Fluid diagnostic ready. Use hamr transcribe CLIP.wav to compare local recognizers.')
                elif result.get('speech_diagnostic_advice'):
                    print(result['speech_diagnostic_advice']['reason'])
                    print('To install the Whisper checker, run:')
                    print(shell_command(setup_argv(s.root,'whisper')))
                elif a.checks_only:
                    print('Repeat the command that needed speech checking.')
                elif result.get('profile'):
                    print('To make a book, replace the example filename with your document:')
                    print(command(s.root,'open','My book.epub','--voice','cast:nigel-thornberry'))
        elif a.command=='source':
            from .intake import extract_location
            emit(extract_location(a.book,a.location,a.output),jm)
        elif a.command=='plan':
            from .intake import inspect_source
            doc=inspect_source(a.source,s.root/'extractions',entry=a.entry,format=a.source_format,offline=a.offline,
                               encoding=a.encoding)
            result = plan(doc,policy(a),a.aliases,context_reviewer=a.context_review_endpoint)
            emit(result,True)
        elif a.command in ('queue','open'):
            from .book_flow import check_source, finish_book, play_output, preflight
            from .intake import inspect_source
            source_options = {'entry':a.entry,'format':a.source_format,'offline':a.offline,'encoding':a.encoding}
            check_source(a.source,**source_options)
            preview_sha = None
            voice=a.voice or preference(s).get('voice')
            if a.command=='open': preflight(s,voice,a.output,a.play)
            if a.command=='open' and not a.yes:
                if jm or not sys.stdin.isatty(): raise Error('confirmation_required','Headless open requires --yes with explicit/saved settings.','Use plan to inspect, then open --yes or queue.')
                doc = inspect_source(a.source,s.root/'extractions',**source_options)
                preview_sha = doc.source_sha256
                if doc.format=='scan':
                    pages = doc.inventory
                    order = ', '.join(f'{row["page"]}: {row["member"]}' for row in pages[:8])
                    print(f'{len(pages)} source pages · {order}'+(' …' if len(pages)>8 else ''),file=sys.stderr)
                while True:
                    p=plan(doc,policy(a),a.aliases); counts={role:sum(b.role==role for b in doc.blocks) for role in ['table','code','note','figure']}
                    print(f'{doc.title} · {len(p["chapters"])} chapters · about {p["estimated_minutes"]} minutes\nRecommended: {p["policy"]["mode"]}. Narrator: {voice or "not selected"}.',file=sys.stderr)
                    if counts['table']:
                        example=next(b.source for b in doc.blocks if b.role=='table'); print(f'{counts["table"]} tables: {p["policy"]["tables"]}. Example: {example[:220]}',file=sys.stderr)
                        alternative=plan(doc,{**policy(a),'tables':'companion'},a.aliases)
                        print(f'Keep exact tables in companion: estimated speech delta {alternative["estimated_minutes"]-p["estimated_minutes"]:+.1f} minutes. Enter t to change.',file=sys.stderr)
                    if counts['code']: print(f'{counts["code"]} literal blocks: {p["policy"]["code"]}. Exact commands remain in the timed companion. Enter c to change.',file=sys.stderr)
                    if delivery:=p.get('performance'):
                        print(f'Delivery cues identified: {delivery["candidates"]} ({delivery["mode"]}).',file=sys.stderr)
                    print(f'{len(p["issues"])} locations need review.',file=sys.stderr)
                    try: answer=input('Start recommended? [y/N, t tables, c literals] ').strip().lower()
                    except EOFError: return 0
                    if answer=='t' and counts['table']: a.tables='companion' if p['policy']['tables']=='speak' else 'speak'; continue
                    if answer=='c' and counts['code']: a.code='companion' if p['policy']['code']=='speak' else 'speak'; continue
                    if answer not in ('y','yes'): return 0
                    break
            result=create(s,a.source,voice,policy(a),a.output,a.aliases,label=a.label,parallel=a.parallel,
                          context_reviewer=a.context_review_endpoint,mastering_mode=a.mastering,
                          source_options=source_options,announce=not jm,preview_sha=preview_sha)
            if a.command=='open' and result['status']!='needs_review': result=finish_book(s,result['job'],announce=not jm)
            if not jm: emit(result)
            if a.command=='open' and a.play and result['status']=='ready': play_output(result,quiet=jm)
            if jm: emit(result,True)
            if result['status']=='needs_review': return 3
        elif a.command in ('render','resume'):
            if not a.no_synthesis and a.max_new_takes is None:
                from .book_flow import finish_book
                result=finish_book(s,a.job,output=a.output,announce=not jm)
            else: result=render(s,a.job,a.output,allow_synthesis=not a.no_synthesis,max_new_takes=a.max_new_takes)
            emit(result,jm)
            if result['status']=='needs_review': return 3
        elif a.command=='run':
            from .checks_setup import observer
            from .pipeline import run as run_pipeline
            python,model,second=observer(s,a.asr_python,a.asr_model,a.second_model)
            result=run_pipeline(s,a.job,python,model,second,a.batch_takes,a.max_retakes,a.prepare_clear,a.max_new_takes,resident=a.resident,batch_rest_seconds=a.batch_rest_seconds,overlap_batch_rest=a.overlap_batch_rest,report_progress=not jm);emit(result,jm)
            if result['status']=='needs_review':return 3
        elif a.command=='status':
            result=summary(s,s.get(a.job)) if a.job else {'schema_version':1,'jobs':[summary(s,j) for j in s.jobs()]}; emit(result,jm)
        elif a.command=='show':
            j=s.get(a.job); emit({'schema_version':1,'job':j['id'],'plan':j['plan']} if a.content else summary(s,j),True)
        elif a.command=='repair':
            if a.block and not a.text_file: raise Error('repair_text','--block requires --text-file containing a verified reading.')
            if not any([a.pronounce,a.block,a.voice,a.retake,a.repackage,a.reimport,a.upgrade_rules,a.document_policy,a.metadata,a.chapters,a.context,a.context_review,a.context_review_endpoint,a.performance,a.mastering]): raise Error('repair_required','Specify a scoped repair, --document-policy, --context, --metadata, --chapters, --performance or --mastering.')
            from .context_config import read as read_context
            emit(repair(s,a.job,a.pronounce,a.block,a.text_file.read_text() if a.text_file else None,a.voice,a.retake,a.reimport,a.upgrade_rules,loadjson(a.document_policy) if a.document_policy else None,loadjson(a.metadata) if a.metadata else None,chapters=a.chapters,expect_revision=a.expect_revision,
                        context=read_context(a.context) if a.context else None,context_review=read_context(a.context_review) if a.context_review else None,
                        context_reviewer=a.context_review_endpoint,performance_mode=a.performance,mastering_mode=a.mastering),jm)
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
                    options={'wash':a.wash,'channel':a.channel} if a.voice_command=='clone' else {}
                    v=save_voice(s,a.name,reference,transcript,a.description,a.voice_command,**options)
                    if not a.no_default: writejson(s.root/'preferences.json',{**preference(s),'voice':a.name})
                    if a.voice_command=='design': temp.unlink(missing_ok=True)
                    emit({'schema_version':1,'voice':v['name'],'origin':v['origin'],'seconds':v['seconds'],
                          'reference':v['reference'],'preparation':v['preparation'],
                          'preparation_receipt':v['preparation_receipt'],
                          'next':'The reference is saved for reuse in new books.'},True)
        elif a.command=='worker':
            if a.detach:
                args=[sys.executable,'-m','hamr','--home',str(s.root),'worker']+(['--once'] if a.once else [])
                from .platform_support import detached_flags
                with (s.root/'runtime'/'worker.log').open('ab') as log:
                    child=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=log,stderr=log,**detached_flags())
                emit({'schema_version':1,'pid':child.pid,'log':str(s.root/'runtime'/'worker.log'),'restart':f'hamr --home {s.root} worker --detach'},True)
            else:
                with s.lock('queue-worker'):
                    from .book_flow import finish_book, record_worker_failure
                    while True:
                        queued=[j for j in s.jobs() if j['status']=='queued']
                        results=[]
                        for j in queued:
                            try:
                                result=finish_book(s,j['id'],announce=not jm)
                                results.append({'job':j['id'],'status':result['status']})
                            except Error as e:
                                failure={'code':e.code,'message':e.message,'action':e.action}
                                status=record_worker_failure(s,j,failure)
                                results.append({'job':j['id'],'status':status,'error':failure})
                                if not jm or not a.once:
                                    print(f"{j['id']}: {e.message}",file=sys.stderr)
                                    if e.action: print(e.action,file=sys.stderr)
                        if a.once:
                            if jm: emit({'schema_version':1,'processed':results},True)
                            elif results: print(f'Queue pass processed {len(results)} job(s). Run hamr jobs for status.')
                            if any('error' in result or result['status']=='failed' for result in results): return 2
                            if any(result['status']=='needs_review' for result in results): return 3
                            break
                        time.sleep(2)
        elif a.command=='review':
            from . import qa, speech_review
            if a.flag_utterance or a.accept_utterance:
                if not a.reason or a.kind and not a.flag_utterance:
                    raise Error('review_reason','Give a --reason for the decision. Use --kind with --flag.')
                options={'expect_revision':a.expect_revision}
                if a.expect_pcm:
                    import re
                    if not re.fullmatch('[a-f0-9]{64}',a.expect_pcm):
                        raise Error('review_pcm','Use the 64-character PCM checksum from the review or excerpt.')
                    options['expected_take']=(None,a.expect_pcm)
                if a.flag_utterance:
                    result=qa.flag(s,a.job,a.flag_utterance,a.reason,kind=a.kind or 'recording',**options)
                else:
                    result=qa.accept(s,a.job,a.accept_utterance,a.reason,**options)
                emit(result,True)
            elif a.reason is not None or a.kind or a.expect_revision is not None or a.expect_pcm is not None:
                raise Error('review_action','Use decision options with --accept or --flag.')
            elif a.play_utterance:
                result=speech_review.play(s,s.get(a.job),a.play_utterance)
                if jm:
                    emit(result,True)
                else:
                    print(f'Played {a.play_utterance} · PCM SHA-256 {result["pcm_sha256"]}')
            elif a.serve:
                if jm:
                    raise Error('review_serve_json','Open the browser review without --json, or use review JOB --json to inspect the data.')
                from .speech_review_server import serve
                serve(s,a.job)
            else:
                data=speech_review.snapshot(s,s.get(a.job))
                if a.report:
                    path=a.report.absolute()
                    if path.exists() or path.is_symlink():
                        raise Error('output_collision',f'The report destination already exists: {path}',
                                    'Choose a new report path.')
                    content=dumps(data) if path.suffix.lower()=='.json' else speech_review.markdown(data)
                    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
                    with os.fdopen(fd,'w',encoding='utf-8') as stream:
                        stream.write(content)
                    if jm:
                        emit({'schema_version':1,'job':a.job,'report':str(path),'flagged_passages':data['count']},True)
                    else:
                        print(f'Review report written to {path}')
                        speech_review.print_summary(data)
                elif jm:
                    emit(data,True)
                else:
                    speech_review.print_summary(data)
        elif a.command=='qa':
            from .qa import check
            from .checks_setup import observer
            if a.recheck:
                if a.asr_python or a.asr_model:
                    raise Error('qa_recheck_options','Recheck uses the previous verifier’s saved transcripts.',
                                'Run qa without --recheck to use a different verifier.')
                result=check(s,a.job,None,None,recheck=True)
            else:
                python,model,_=observer(s,a.asr_python,a.asr_model,use_second=False)
                result=check(s,a.job,python,model,a.all,a.require_review)
            if jm:
                emit(result,True)
            else:
                print(f'{result["clips_checked"]} passages checked. Verifier report: {result["report"]}')
                if a.require_review or a.recheck:
                    from .speech_review import print_summary,snapshot
                    print_summary(snapshot(s,s.get(a.job)))
                else:
                    print(f'{result["flagged_passages"]} verifier disagreements. Save a publication review with:\n'
                          + command(s.root,'qa',a.job,'--all','--require-review'))
            if result['status']=='needs_review': return 3
        elif a.command=='transcribe':
            from .asr_diagnostic import transcribe
            result=transcribe(s,a.input,utterance=a.utterance,python=a.asr_python,model=a.asr_model,second=a.second_model)
            if jm:
                emit(result,True)
            else:
                for reading in result['observations']:
                    print(f'{reading["role"].capitalize()} ({reading["model"]})\n{reading["raw"]["text"]}')
                if (extra:=result['challenger']) and extra['status']=='observed':
                    print(f'Fluid CPU diagnostic\n{extra["text"]}')
                elif extra and extra['status']=='unavailable':
                    print(extra['message'],file=sys.stderr)
                elif extra:
                    print('Fluid diagnostics cover excerpts up to 30 seconds.',file=sys.stderr)
                print(result['assessment'])
        elif a.command=='excerpt': emit(excerpt(s,a.job,a.start,a.count,a.output),True)
        elif a.command=='preview':
            with s.lock():
                j=s.get(a.job); u=next((u for u in j['plan']['utterances'] if u['id']==a.utterance),None)
                if not u: raise Error('unknown_utterance','Unknown utterance ID.')
                if not j['voice']: raise Error('voice_required','Preview needs a saved narrator.')
                # Audition the same request that would be bound by render.
                # Preview leaves a queued job unbound until actual rendering.
                bind_speech(s,j)
                if a.compare:
                    from .preview_compare import audition, read_controls
                    comparison = audition(s, j, u, read_controls(a.compare))
                else:
                    with Engine(s,j['backend']) as e: data,receipt=speak(e,u,j['voice'])
                    dest=s.path(j)/'preview.wav'
                    with wave.open(str(dest),'wb') as w: w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE); w.writeframes(data)
                    emit({'schema_version':1,'preview':str(dest),'ready':False,'sources':[b['locator'] for b in j['document']['blocks'] if b['id'] in u['sources']],'receipt':receipt},True)
                    if a.play: subprocess.run(['mpv','--no-video',str(dest)],check=True)
            if a.compare:
                if jm:
                    emit(comparison,True)
                else:
                    for take in comparison['takes']:
                        print(f'{take["name"]} · {take["seconds"]:.2f} s · {take["audio"]}')
                    if comparison['reused_takes']:
                        print(f'Reused {comparison["reused_takes"]} verified comparison take(s).')
                    print(comparison['manifest'])
                if a.play:
                    sys.stdout.flush()
                    subprocess.run(['mpv','--no-video','--really-quiet',*[take['audio'] for take in comparison['takes']]],check=True)
        elif a.command=='play':
            j=s.get(a.job); result=summary(s,j)
            if result['status']!='ready': raise Error('not_ready','Only a verified ready output can be played.','Resume or repair the job first.')
            from .book_flow import play_output
            play_output(result,a.start,quiet=jm)
        sys.stdout.flush()
        return 0
    except BrokenPipeError:
        discard_closed_output()
        return 0
    except Error as e:
        return emit_error(e,jm or a.command=='agent')
    except KeyboardInterrupt:
        status, action = ('interrupted',f'Run the same {a.command} command again.') if a.command in ('transcribe','preview') else (
            'paused','Run hamr status, then resume JOB.')
        try:
            emit({'schema_version':1,'status':status,'action':action},True); sys.stdout.flush()
        except OSError: discard_closed_output()
        return 130
    except FileNotFoundError as e:
        return emit_error(Error('local_io',f'File not found: {e.filename or str(e)}','Check the named path and repeat the command.'),jm or a.command=='agent')
    except OSError as e:
        detail=e.strerror or str(e)
        if e.filename: detail=f'{detail}: {e.filename}'
        return emit_error(Error('local_io',detail,'Fix the filesystem problem and repeat the command.'),jm or a.command=='agent')
    except (ValueError,KeyError) as e:
        return emit_error(Error('local_io',f'Local operation failed ({type(e).__name__}).','Check app/output paths, permissions and free space. Run doctor and resume.'),jm or a.command=='agent')

if __name__=='__main__': sys.exit(main())
