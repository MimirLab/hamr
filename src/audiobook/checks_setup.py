"""Install and select the offline speech checker independently of narration."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .backend import acquire
from .core import Error, filehash, loadjson, writejson


LOCK = Path(__file__).with_name('asr.lock')
WINDOWS_LOCK = LOCK.with_name('asr-windows.lock')
ASSETS = Path(__file__).with_name('asr_assets.json')
PROBE = """import sys
from faster_whisper import WhisperModel
for model in sys.argv[1:]:
    WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
"""


def configured(store):
    path = store.root / 'speech-checks.json'
    try:
        result = loadjson(path)
        if result['schema_version'] != 1 or not Path(result['python']).is_file():
            return None
        if any(not (Path(result[key]) / file).is_file()
               for key in ('model', 'second_model') for file in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')):
            return None
        return result
    except (OSError, ValueError, KeyError, TypeError):
        return None


def local_model(path, option):
    path = path.expanduser().absolute()
    if not path.is_dir():
        raise Error('asr_model_missing', f'{option} needs a local speech-check model directory. No directory found at {path}.',
                    f'Give {option} the path to an installed model directory or omit it to use setup defaults.')
    if not (path / 'model.bin').is_file():
        raise Error('asr_model_incomplete', f'Speech-check model file not found: {path / "model.bin"}',
                    f'Give {option} the directory containing the local faster-whisper model.')
    return path


def observer(store, python=None, model=None, second=None, *, use_second=True):
    if (python == Path('ASR_PYTHON') or model == Path('LOCAL_MODEL')) and not (
            python and python.is_file() and model and model.is_dir()):
        raise Error('asr_placeholder', 'ASR_PYTHON and LOCAL_MODEL in older instructions stand for local paths.',
                    'Omit --asr-python and --asr-model to use the installed speech checker. Run hamr setup --checks-only if needed.')
    if python is not None or model is not None:
        if not python or not model:
            raise Error('asr_options', 'Custom speech checking needs both --asr-python and --asr-model.',
                        'Omit both options to use the installed speech checker.')
        python = python.expanduser().absolute()
        if not python.is_file():
            raise Error('asr_python_missing', f'Speech-check Python executable not found: {python}',
                        'Give --asr-python the path to a Python executable with faster-whisper installed, or omit both custom options.')
        return python, local_model(model, '--asr-model'), local_model(second, '--second-model') if second else None
    saved = configured(store)
    if not saved:
        raise Error('speech_checks_required', 'The offline speech checker is missing or its installation is incomplete.',
                    'Run hamr setup --checks-only, then repeat the book command.')
    return (Path(saved['python']), Path(saved['model']),
            local_model(second, '--second-model') if second else Path(saved['second_model']) if use_second else None)


def preflight():
    if not shutil.which('uv'):
        raise Error('missing_dependency', 'uv is needed to install speech checking.',
                    'Install uv and repeat hamr setup.')
    for executable in ('ffmpeg', 'ffprobe'):
        if not shutil.which(executable):
            raise Error('missing_dependency', f'Missing executable: {executable}',
                        'Install FFmpeg (including ffprobe) and repeat hamr setup.')


def provision(store, progress, cache=None):
    """Called under the setup writer lock, with the engine process stopped."""
    preflight()
    root = store.root / 'assets' / 'speech-checks'
    root.mkdir(exist_ok=True)
    lock = WINDOWS_LOCK if os.name == 'nt' else LOCK
    lock_hash = filehash(lock)
    env_dir = root / ('env-' + lock_hash[:16])
    python = env_dir / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    ready = env_dir / '.hamr-ready'
    log = store.root / 'runtime/speech-checks.log'
    try:
        if not ready.is_file() or not python.is_file():
            progress.stage('Installing the offline speech-check runtime')
            with log.open('w') as output:
                version = sys.executable if sys.version_info[:2] == (3, 12) else '3.12'
                subprocess.run(['uv', 'venv', '--allow-existing', '--python', version, env_dir],
                               stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, check=True, timeout=600)
                subprocess.run(['uv', '--no-config', 'pip', 'sync', '--python', python, '--require-hashes',
                                '--only-binary', ':all:', lock], stdin=subprocess.DEVNULL,
                               stdout=output, stderr=subprocess.STDOUT, check=True, timeout=1200)
        models = []
        for name, model in loadjson(ASSETS).items():
            target = root / f'{name}-{model["revision"][:12]}'
            target.mkdir(exist_ok=True)
            for asset in model['files']:
                filename = asset['path']
                path = target / filename
                source = Path(cache) / name / filename if cache else None
                if source and source.is_file() and not path.exists():
                    if source.stat().st_size != asset['bytes'] or filehash(source) != asset['sha256']:
                        raise Error('asset_integrity', f'Cached speech-check asset differs: {source}')
                    # Copy through the same bound partial path as a transfer.
                    partial = path.with_suffix(path.suffix + '.local-copy')
                    try:
                        shutil.copyfile(source, partial)
                        os.replace(partial, path)
                    finally:
                        partial.unlink(missing_ok=True)
                url = f'https://huggingface.co/Systran/faster-whisper-{name}/resolve/{model["revision"]}/{filename}'
                acquire(url, path, asset['sha256'], asset['bytes'], progress=progress, label=f'{name}/{filename}')
            models.append(target)
        progress.stage('Checking that both speech recognition models load on CPU')
        with log.open('a') as output:
            subprocess.run([python, '-I', '-c', PROBE, *models], stdin=subprocess.DEVNULL,
                           stdout=output, stderr=subprocess.STDOUT, check=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Error('speech_checks_setup', 'Speech-check setup could not finish.',
                    f'Check {log}, then repeat hamr setup --checks-only.') from exc
    writejson(ready, {'lock_sha256': lock_hash})
    result = {'schema_version': 1, 'python': str(python), 'model': str(models[0]),
              'second_model': str(models[1]), 'lock_sha256': lock_hash}
    writejson(store.root / 'speech-checks.json', result)
    return result
