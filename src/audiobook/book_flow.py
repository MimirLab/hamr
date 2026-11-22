"""The installed CLI's checked path from a saved job to a playable book."""
from pathlib import Path
import shutil
import subprocess
import sys

from . import jobs
from .checks_setup import observer
from .core import Error, run
from .installation import command


def check_source(source):
    from .importers import input_adapter
    if source.is_dir():
        raise Error('source_is_directory', f'Choose a file inside {source}.',
                    'Example: hamr open "Folder/My book.epub".')
    if not source.is_file():
        raise Error('source_missing', f'Document not found: {source}', 'Check the document path.')
    input_adapter(source)


def preflight(store, voice, output, play):
    if not (store.root / 'settings.json').is_file():
        raise Error('setup_required', 'The speech engine needs setup.', 'Run this command to choose an engine.\n'+command(store.root, 'setup'))
    if not voice:
        raise Error('voice_required', 'Choose a narrator before creating the book.',
                    'Run hamr voice select nigel-thornberry or pass --voice cast:CHARACTER.')
    observer(store)
    for executable in ('ffmpeg', 'ffprobe', *(('mpv',) if play else ())):
        if not shutil.which(executable):
            raise Error('missing_dependency', f'Missing executable: {executable}.',
                        'Install it and repeat the command.')
    if output is not None and not output.is_dir():
        raise Error('output_unavailable', f'Output directory does not exist: {output}',
                    'Create that directory or omit --output to use ~/Audiobooks.')


def finish_book(store, jid, *, output=None, announce=False):
    job = store.get(jid)
    if job['status'] == 'ready' and output is None:
        result = jobs.summary(store, job)
        if result['status'] == 'ready':
            return result
    if job['status'] == 'cancelled':
        raise Error('cancelled', 'This job was cancelled.', 'Repair the job or create a new one.')
    if not job.get('voice'):
        raise Error('voice_required', 'The saved job needs a narrator.',
                    f'Run hamr repair {jid} --voice cast:nigel-thornberry.')
    if not job.get('backend') and not (store.root / 'settings.json').is_file():
        raise Error('setup_required', 'The speech engine needs setup.', 'Run this command to choose an engine.\n'+command(store.root, 'setup'))
    python, model, second_model = observer(store)
    for executable in ('ffmpeg','ffprobe'):
        if not shutil.which(executable):
            raise Error('missing_dependency',f'Missing executable: {executable}.',
                        f'Install it and run hamr resume {jid}.')
    if output is not None:
        if not output.is_dir():
            raise Error('output_unavailable', f'Output directory does not exist: {output}')
        with store.lock('pipeline'), store.lock():
            job = store.get(jid)
            job['output_parent'] = str(output.absolute())
            if job['status'] == 'ready':
                job['status'] = 'queued'
            store.save(job)
    default = Path.home() / 'Audiobooks'
    if Path(job['output_parent']) == default:
        try:default.mkdir(exist_ok=True)
        except OSError as exc:
            raise Error('output_unavailable',f'Could not open the default output directory at {default}.',
                        f'Choose a writable directory with hamr resume {jid} --output DIRECTORY.') from exc
    elif not Path(job['output_parent']).is_dir():
        raise Error('output_unavailable',f'Output directory does not exist: {job["output_parent"]}',
                    f'Create it and run hamr resume {jid}, or choose a directory with --output.')
    if announce:
        print(f'{jid}: rendering and checking speech. If interrupted, run:\n{command(store.root, "resume", jid)}',
              file=sys.stderr, flush=True)
        print(f'Planned audiobook: {jobs.final_path(job) / "book.m4b"}', file=sys.stderr, flush=True)
    from .pipeline import run as run_pipeline
    return run_pipeline(store, jid, python, model, second_model, batch_takes=8, resident=True,
                        report_progress=announce)


def record_worker_failure(store, job, error):
    """Persist a blocked preflight only while the queue snapshot is current."""
    permanent_preflight={'voice_required','setup_required','speech_checks_required','asr_profile_unavailable',
                         'missing_dependency','output_unavailable','asr_environment'}
    if error['code'] not in permanent_preflight:
        return store.get(job['id'])['status']
    failed={**job,'status':'failed','error':error}
    try:
        with store.lock():
            if store.save_if_current(failed,job['updated']):
                return 'failed'
    except Error as exc:
        if exc.code!='busy':raise
    return store.get(job['id'])['status']


def play_output(result, start=0, *, quiet=False):
    path = Path(result['output']) / 'book.m4b'
    command = ['mpv', '--no-video', f'--start={start}', str(path)]
    try:
        if quiet:
            run(command)
        else:
            subprocess.run(command, check=True)
    except (Error, OSError, subprocess.SubprocessError) as exc:
        raise Error('playback_failed', f'mpv could not play the finished book at {path}.',
                    f'Check mpv and your audio device, then retry with hamr play {result["job"]}.') from exc
