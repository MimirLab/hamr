"""Install and select the offline speech checker independently of narration."""
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from .backend import acquire
from .core import Error, filehash, loadjson, writejson
from .installation import command, command_argv, shell_command


LOCK = Path(__file__).with_name('asr.lock')
WINDOWS_LOCK = LOCK.with_name('asr-windows.lock')
ASSETS = Path(__file__).with_name('asr_assets.json')
PROBE = """import sys
from faster_whisper import WhisperModel
for model in sys.argv[1:]:
    WhisperModel(model, device='cpu', compute_type='int8', cpu_threads=2, local_files_only=True)
"""
PROFILES = ('auto','fluid-int8','whisper')


def setup_argv(root, profile=None):
    args = ['setup', '--checks-only']
    if profile:
        args.extend(('--asr-profile', profile))
    return command_argv(root, *args)


def recovery(store, *, job=None, profile=None):
    argv = setup_argv(store.root, profile)
    lines = ['Run this command to install or repair the speech checker. No speech engine profile is needed.',
             shell_command(argv)]
    if argv[:2] == ['uv', 'run']:
        lines.append('Use the checkout shown above. A bare hamr command uses your shell’s PATH and may start a different installation.')
    if job:
        lines.extend(('Then resume the saved book.', command(store.root, 'resume', job)))
    else:
        lines.append('Then repeat your original command.')
    return '\n'.join(lines)


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


def configured_fluid(saved):
    from . import asr_fluid

    recipe = (saved or {}).get('fluid')
    if (isinstance(recipe,dict) and recipe.get('profile')==asr_fluid.PROFILE
            and recipe.get('lock_sha256')==filehash(asr_fluid.LOCK)
            and isinstance(recipe.get('python'),str) and Path(recipe['python']).is_file()
            and isinstance(recipe.get('model'),str) and Path(recipe['model']).is_dir()):
        return recipe
    return None


def speech_advice(store, host=None):
    from . import asr_fluid

    if host is None:
        host = {'system':platform.system(),'arch':platform.machine(),'ram_available_bytes':asr_fluid.memory_available()}
    fluid = asr_fluid.advice(store,host,book=True)
    saved = configured(store)
    request = (saved or {}).get('requested_profile','auto')
    installed = configured_fluid(saved) is not None
    active = None
    if saved:
        if request!='whisper' and fluid['fits'] and installed:active = 'fluid-int8'
        elif request!='fluid-int8':active = 'whisper'
    return {'requested':request,'active':active,'recommended':fluid['recommended'],'reason':fluid['reason'],
            'profiles':[
                {'profile':'fluid-int8','status':'qualified','linux_arch':'x86_64','fits':fluid['fits'],
                 'device':'cpu','verified_peak_ram_bytes':2175276*1024,'required_available_ram_bytes':fluid['ram_headroom_bytes'],
                 'download_bytes':fluid['download_bytes'],'additional_disk_budget_bytes':fluid['additional_disk_budget_bytes'],
                 'installed':installed,'reason':fluid['reason']},
                {'profile':'whisper','status':'baseline','device':'cpu','installed':saved is not None,
                 'download_bytes':sum(item['bytes'] for model in loadjson(ASSETS).values() for item in model['files']),
                 'reason':'Uses the lower-memory faster-whisper pair.'}]}


def observer(store, python=None, model=None, second=None, *, use_second=True):
    if (python == Path('ASR_PYTHON') or model == Path('LOCAL_MODEL')) and not (
            python and python.is_file() and model and model.is_dir()):
        raise Error('asr_placeholder', 'ASR_PYTHON and LOCAL_MODEL in older instructions stand for local paths.',
                    'Omit --asr-python and --asr-model to use the installed speech checker.\n'
                    + (recovery(store) if not configured(store) else 'Then repeat the command.'))
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
                    recovery(store))
    from . import asr_fluid

    requested = saved.get('requested_profile','auto')
    primary = Path(saved['model'])
    secondary = Path(saved['second_model']) if use_second else None
    if requested!='whisper':
        recipe = configured_fluid(saved)
        available = asr_fluid.memory_available() if recipe else None
        if (recipe and platform.system()=='Linux' and platform.machine()=='x86_64'
                and available is not None and available>=asr_fluid.BOOK_RAM_HEADROOM):
            secondary = primary if use_second else None
            primary = asr_fluid.Recipe(recipe['python'],recipe['model'])
        elif requested=='fluid-int8':
            raise Error('asr_profile_unavailable','The selected Fluid verifier needs a complete install and 2.75 GiB of available system RAM.',
                        'Free memory and retry, or switch to the lower-memory checker.\n' + recovery(store, profile='whisper'))
    return Path(saved['python']),primary,local_model(second, '--second-model') if second else secondary


def preflight():
    if not shutil.which('uv'):
        raise Error('missing_dependency', 'uv is needed to install speech checking.',
                    'Install uv and repeat your original setup command.')
    for executable in ('ffmpeg', 'ffprobe'):
        if not shutil.which(executable):
            raise Error('missing_dependency', f'Missing executable: {executable}',
                        'Install FFmpeg (including ffprobe) and repeat your original setup command.')


def provision(store, progress, cache=None, *, activate=True, asr_profile='auto'):
    """Called under the setup writer lock, with the engine process stopped."""
    preflight()
    prior_fluid=configured_fluid(configured(store))
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
                    f'Check the setup log at {log}, then repeat your original setup command.') from exc
    writejson(ready, {'lock_sha256': lock_hash})
    result = {'schema_version': 1, 'python': str(python), 'model': str(models[0]),
              'second_model': str(models[1]), 'lock_sha256': lock_hash,'requested_profile':asr_profile}
    if prior_fluid:result['fluid']=prior_fluid
    if asr_profile!='whisper':
        from . import asr_fluid

        fit = speech_advice(store)
        if fit['recommended']=='fluid-int8':
            try:
                result['fluid']=asr_fluid.provision(store,progress,cache,activate=False)
            except Error as exc:
                if asr_profile!='auto':raise
                result['selection_reason']=f'Fluid setup could not finish. Whisper is ready. {exc.message}'
        elif asr_profile!='auto':
            raise Error('asr_profile_fit',fit['reason'],'Choose whisper or make more memory and disk available.')
        else:
            result['selection_reason']=fit['reason']
    if activate:
        writejson(store.root / 'speech-checks.json', result)
    return result
