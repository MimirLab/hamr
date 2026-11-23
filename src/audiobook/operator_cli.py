"""Local automation discovery and observation, without a service or job writes."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from . import __version__
from .core import Error, SCHEMA, dumps, state_root

GUIDE = Path(__file__).with_name('agent_guide.md')


def add_commands(subparsers):
    agent = subparsers.add_parser('agent', description='Local automation interface')
    commands = agent.add_subparsers(dest='agent_command', required=True)
    for name in ('guide', 'capabilities', 'inspect', 'watch'):
        command = commands.add_parser(name)
        command.add_argument('--json', action='store_true', default=argparse.SUPPRESS)
        if name in ('inspect', 'watch'):
            command.add_argument('job')
            command.add_argument('--content', action='store_true')
        if name == 'inspect':
            command.add_argument('--verify', action='store_true')
        if name == 'watch':
            command.add_argument('--interval', type=float, default=2)
            command.add_argument('--timeout', type=float, default=0)


def children(parser):
    return next((a.choices for a in parser._actions if isinstance(a, argparse._SubParsersAction)), {})


def schema(parser):
    return {
        'description': parser.description,
        'usage': parser.format_usage().removeprefix('usage: ').strip(),
        'arguments': [
            {
                'name': a.dest, 'flags': a.option_strings,
                'required': a.required, 'arity': a.nargs,
                'choices': list(a.choices) if a.choices is not None else None,
                'help': a.help,
                'type': getattr(a.type, '__name__', 'string') if a.nargs != 0 else 'flag',
            }
            for a in parser._actions if not isinstance(a, argparse._SubParsersAction)
        ],
        'commands': {name: schema(child) for name, child in children(parser).items()},
    }


def capabilities(parser):
    from .core import loadjson
    from .mastering import DEFAULT, MODES
    from .performance import RULES_FILE
    from .wash_setup import PROFILES

    formats = ['epub', 'markdown', 'pdf', 'text']
    rules = loadjson(RULES_FILE)
    return {'schema_version': 1, 'hamr_version': __version__,
            'formats': formats, 'languages': ['en'],
            'cli': schema(parser), 'operator_guide': ['hamr', 'agent', 'guide'],
            'expressive_narration': {'default_mode':'auto', 'formats':formats, 'flag':'--performance',
                                     'plain_mode':'off', 'details':'plan.utterances[].performance',
                                     'dialogue_recipes':sorted(set(rules['directions'])-{'quiet_scene'}),
                                     'attribution_cues':'performance.attribution_cues',
                                     'passage_instructions':'performance.passage_instructions',
                                     'frame_guidance':{'field':'frame_guidance','protocol':1,
                                                       'multiple_windows_capability':'frame_guidance_windows',
                                                       'narration_instruction_capability':'narration_conditioning',
                                                       'decoder_guidance_capability':'quote_guidance',
                                                       'scope':'Automatic quoted whispers, passage instructions and previews with a compatible backend',
                                                       'automatic_takes':'report.json quote_timing'},
                                     'policy_flag':'--document-policy'},
            'voice_preparation': {'default_mode':'auto','mode_flag':'--wash','setup_flag':'--wash-only',
                                  'profiles':list(PROFILES),'host_advice':'doctor.voice_preparation',
                                  'receipt':'preparation_receipt'},
            'contextual_readings': {'default_mode':'automatic','modes':['automatic','strict'], 'settings_flag':'--context',
                                    'review_replies_flag':'--context-review','reviewer':'local_file',
                                    'review_endpoint_flag':'--context-review-endpoint','review_endpoint_transport':'loopback_http',
                                    'details':'plan.decisions[].context_events', 'scores':'heuristic'},
            'mastering': {'default_for_new_jobs': DEFAULT, 'modes': list(MODES), 'flag': '--mastering',
                          'report': 'report.json', 'requires_synthesis_for_remaster': False},
            'exit_status': {'success': 0, 'error': 2, 'needs_review': 3, 'interrupted': 130}}


def connect(root):
    path = root / 'state.sqlite'
    if not path.is_file():
        raise Error('state_missing', f'No hamr state at {root}.', 'Use --home for an existing state directory.')
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=5)
    try:
        if db.execute('PRAGMA user_version').fetchone()[0] > SCHEMA:
            raise Error('newer_state', 'This state needs a newer hamr release.')
    except BaseException:
        db.close()
        raise
    return db


def read_job(db, job_id):
    row = db.execute('SELECT payload FROM jobs WHERE id = ?', (job_id,)).fetchone()
    if row is None:
        raise Error('unknown_job', f'No job with ID {job_id}.', 'Run hamr status --json for job IDs.')
    return json.loads(row[0])


def watch_token(db, root, job_id):
    row = db.execute('SELECT updated FROM jobs WHERE id = ?', (job_id,)).fetchone()
    if row is None:
        read_job(db, job_id)
    checkpoints = []
    for name in ('qa-progress.json', 'pipeline.json'):
        try:
            checkpoints.append((root / 'jobs' / job_id / name).stat().st_mtime_ns)
        except FileNotFoundError:
            checkpoints.append(None)
    return row[0], *checkpoints


def snapshot(root, job, *, content=False, verify=False):
    from .performance import status as performance_status
    from .jobs import final_path, verify_publication

    jid = job['id']
    revision = job['revision']
    prefix = ['hamr', '--home', str(root)]
    status = job['status']
    final = final_path(job)
    verified = verify_publication(final, job) if verify and status == 'ready' else None
    reviews = []
    for issue in job['plan'].get('issues', []):
        # A source issue may contain excerpts. Surface only identifiers and locations.
        location = issue.get('location')
        if isinstance(location, dict):
            location = {k: v for k, v in location.items()
                        if k in ('page', 'chapter', 'line', 'href', 'start', 'end', 'ordinal')}
        source = {key: issue[key] for key in ('block', 'reason_code', 'kind') if key in issue}
        if location is not None:
            source['location'] = location if isinstance(location, dict) else None
        reviews.append({'kind': 'source', **source})
    for issue in job.get('audio_review', []):
        uid = issue['utterance']
        reviews.append({'kind': 'speech', 'utterance': uid, 'sources': issue['sources'],
                        'pcm_sha256': issue.get('pcm_sha256'),
                        'retake_argv': [*prefix, 'repair', jid, '--retake', uid,
                                        '--expect-revision', str(revision), '--json']})
    result = {
        'schema_version': 1, 'job': jid, 'revision': revision, 'status': status,
        'mastering': job.get('mastering', 'off'),
        'updated': job['updated'], 'source_sha256': job['document']['source_sha256'],
        'plan_sha256': job['plan']['plan_sha256'],
        'progress': job.get('progress', {}),
        'error_code': (job.get('error') or {}).get('code'),
        'reviews': reviews, 'publication_verified': verified,
        'audio': str(final / 'book.m4b') if status == 'ready' and verified is not False else None,
        'planned_output': str(final / 'book.m4b'),
        'actions': {
            'status': [*prefix, 'status', jid, '--json'],
            'resume': [*prefix, 'resume', jid, '--json'],
            'show_content': [*prefix, 'show', jid, '--content', '--json'],
        },
    }
    for name, file in (('speech_checks', 'qa-progress.json'), ('pipeline', 'pipeline.json')):
        try:
            progress = json.loads((root / 'jobs' / jid / file).read_text())
            if progress['plan_sha256'] == result['plan_sha256']:
                fields = ('phase', 'updated', 'observed_unique_takes', 'remaining_unique_takes',
                          'completed_utterances', 'total_utterances', 'reused_observations', 'eta_seconds')
                result[name] = {k: progress[k] for k in fields if k in progress}
        except (OSError, ValueError, KeyError, TypeError):
            pass
    if content:
        result['plan'] = job['plan']
    if delivery:=performance_status(job):
        result['performance']=delivery
    if context:=job['plan'].get('contextual_resolution'):
        result['contextual_readings']={k:context[k] for k in ('configuration_sha256','score_kind','routes','review_provider') if k in context}
    return result


def completion(parser, shell):
    public = {k: v for k, v in children(parser).items() if k != 'agent'}
    commands = ' '.join(public)
    options = lambda command: ' '.join(flag for a in command._actions for flag in a.option_strings)
    if shell == 'fish':
        lines = [f"complete -c hamr -n '__fish_use_subcommand' -a '{commands}'"]
        for name, command in public.items():
            subcommands = ' '.join(children(command))
            if subcommands:
                lines.append(f"complete -c hamr -n '__fish_seen_subcommand_from {name}' -a '{subcommands}'")
            for flag in options(command).split():
                if flag.startswith('--'):
                    lines.append(f"complete -c hamr -n '__fish_seen_subcommand_from {name}' -l {flag[2:]}")
        return '\n'.join(lines)
    if shell == 'zsh':
        lines = ['#compdef hamr', '_hamr() {', '  case "$words[2]" in']
        for name, command in public.items():
            values = options(command) + ' ' + ' '.join(children(command))
            lines.append(f"    {name}) compadd -- {values}; _files ;;")
        lines += [f'    *) compadd -- {commands} {options(parser)} ;;', '  esac', '}', 'compdef _hamr hamr']
        return '\n'.join(lines)
    lines = ['_hamr_complete() {', '  local words cur', '  cur=${COMP_WORDS[COMP_CWORD]}',
             '  case "${COMP_WORDS[1]}" in']
    for name, command in public.items():
        values = options(command) + ' ' + ' '.join(children(command))
        lines.append(f"    {name}) words='{values}' ;;")
    lines += [f"    *) words='{commands} {options(parser)}' ;;", '  esac',
              '  mapfile -t COMPREPLY < <(compgen -W "$words" -- "$cur")', '}',
              'complete -o default -F _hamr_complete hamr']
    return '\n'.join(lines)


def dispatch(parser, args):
    if args.command == 'completion':
        print(completion(parser, args.shell), flush=True)
        return 0
    if args.agent_command == 'guide':
        print(GUIDE.read_text(), end='', flush=True)
        return 0
    if args.agent_command == 'capabilities':
        print(dumps(capabilities(parser)), end='', flush=True)
        return 0
    root = (args.home or state_root()).absolute()
    with closing(connect(root)) as db:
        if args.agent_command == 'inspect':
            value = snapshot(root, read_job(db, args.job), content=args.content, verify=args.verify)
            print(dumps(value), end='', flush=True)
            return 0
        if not .1 <= args.interval <= 60 or not 0 <= args.timeout <= 86400:
            raise Error('watch_interval', 'Use an interval of 0.1–60 seconds and a timeout of 0–86400 seconds.')
        prior = None
        previous_token = None
        end = time.monotonic() + args.timeout if args.timeout else None
        while True:
            token = watch_token(db, root, args.job)
            if token != previous_token:
                value = snapshot(root, read_job(db, args.job), content=args.content)
                line = json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                if line != prior:
                    print(line, flush=True)
                    prior = line
                previous_token = token
            if value['status'] in ('ready', 'failed', 'cancelled', 'needs_review', 'paused'):
                # Rendering can checkpoint an intermediate hold before QA starts.
                phase = (value.get('pipeline') or {}).get('phase')
                temporary = value['status'] in ('paused', 'needs_review') and phase not in (
                    None, 'interrupted', 'paused_at_run_limit', 'stopped', 'speech_review', 'source_review',
                    'publication_held', 'complete')
                if not temporary:
                    return {'failed': 2, 'needs_review': 3}.get(value['status'], 0)
            if end is not None and time.monotonic() >= end:
                return 0
            time.sleep(min(args.interval, max(0, end - time.monotonic())) if end else args.interval)
