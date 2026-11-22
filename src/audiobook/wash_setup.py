"""Pinned, independently installed CPU reference preparation."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from .core import Error, digest, filehash, loadjson, writejson

LOCK = Path(__file__).with_name('wash-runtime.lock')
WORKER = Path(__file__).with_name('wash_worker.py')
PROFILES = {
    'dpdfnet-hr': {
        'worker': 'dpdfnet', 'bytes': 10493337, 'ram': 384 * 1024**2, 'peak_rss_kib': 184680,
        'file': 'dpdfnet2_48khz_hr.onnx',
        'sha256': '7f0575a5cec0ba4ffd8f8bd657e06d007e4ccdd955d76faab922b9d3291dc14b',
        'url': 'https://huggingface.co/Ceva-IP/DPDFNet/resolve/dd6818d00f50c836fed43a6243ebe49116de5964/onnx/dpdfnet2_48khz_hr.onnx',
        'license': 'Apache-2.0',
        'terms': 'https://huggingface.co/Ceva-IP/DPDFNet/blob/dd6818d00f50c836fed43a6243ebe49116de5964/LICENSE',
    },
    'fastenhancer-tiny': {
        'worker': 'fastenhancer', 'bytes': 151147, 'ram': 256 * 1024**2, 'peak_rss_kib': 103812,
        'file': 'fastenhancer_t.onnx',
        'sha256': '1993a3f58ae95d959123b3d28779c2800e0f016d6f4e1177f1213144f301b89c',
        'url': 'https://github.com/aask1357/fastenhancer/releases/download/onnx-48khz-v1/fastenhancer_t.onnx',
        'license': 'MIT',
        'terms': 'https://github.com/aask1357/fastenhancer/blob/85d98a689a85b956dec384f258b075859ca50263/LICENSE',
    },
}


def automatic_profiles():
    return ('dpdfnet-hr',)


def compatible(profile, host):
    return (host['system'] == 'Linux' and host['arch'] == 'x86_64'
            and (host.get('ram_available_bytes') or 0) >= PROFILES[profile]['ram'])


def configured(store, profile):
    try:
        location = store.root / 'assets' / 'voice-wash' / profile
        saved = loadjson(location / 'setup.json')
        expected = PROFILES[profile]
        model = location / expected['file']
        environment = store.root / 'assets' / 'voice-wash' / ('python-' + filehash(LOCK)[:16])
        python = environment / 'bin' / 'python'
        if (saved['schema_version'] == 1 and saved['profile'] == profile
                and saved['model_sha256'] == expected['sha256']
                and saved['lock_sha256'] == filehash(LOCK)
                and model.is_file() and not model.is_symlink()
                and model.stat().st_size == expected['bytes'] and python.is_file()):
            return {**saved, 'model': model, 'python': python}
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def describe(store, host):
    from .installation import command

    profiles = []
    for name, item in PROFILES.items():
        installed = configured(store, name) is not None
        fits = compatible(name, host)
        profiles.append({'profile': name, 'installed': installed, 'compatible': fits,
                         'automatic_for': ['steady_mains_hum'] if name in automatic_profiles() else [],
                         'provider': 'CPUExecutionProvider', 'memory_budget_bytes': item['ram'],
                         'measured_worker_peak_rss_bytes': item['peak_rss_kib'] * 1024,
                         'model_download_bytes': 0 if installed else item['bytes'],
                         'model_license': item['license'], 'model_terms': item['terms']})
    recommended = next((row['profile'] for row in profiles if row['compatible']), None)
    reason = {
        'dpdfnet-hr': 'DPDFNet improved the steady-hum listening trials within the available CPU budget.',
        'fastenhancer-tiny': 'FastEnhancer is a lighter manual choice within the available CPU budget.',
        None: 'Voice import can use the original reference on this host.',
    }
    return {'recommended_profile': recommended, 'profiles': profiles, 'reason': reason[recommended],
            'setup_command': command(store.root, 'setup', '--wash-only')}


def execute_worker(recipe, input_array, output_array):
    import json
    from .platform_support import close_windows_job, spawn_owned

    args = [str(recipe['python']), '-I', str(WORKER), PROFILES[recipe['profile']]['worker'],
            str(recipe['model']), str(input_array), str(output_array)]
    environment = {**os.environ, 'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '2'}
    process, job = spawn_owned(args, parent_death=True, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment)
    try:
        output, _ = process.communicate(timeout=180)
        if process.returncode:
            raise Error('wash_worker', 'Local voice preparation stopped before returning verified audio.')
        report = json.loads(output)
        if report.get('provider') != 'CPUExecutionProvider' or report.get('runtime') != '1.32.0':
            raise Error('wash_provider', 'The selected voice preparation runtime or provider differs.')
        return report
    except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
        raise Error('wash_worker', 'Local voice preparation did not return a valid receipt.') from exc
    finally:
        if job:
            close_windows_job(job)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()
        process.stderr.close()


def provision(store, progress, cache, profile):
    from .backend import acquire, remaining_bytes
    from .hardware import inspect_host

    host = inspect_host()
    if profile == 'auto':
        profile = describe(store, host)['recommended_profile']
    if profile not in PROFILES:
        raise Error('wash_profile', 'Choose a supported voice preparation profile from hamr doctor.')
    if not compatible(profile, host):
        raise Error('wash_hardware', f'{profile} cannot run within the available CPU platform or memory budget.',
                    'Run hamr doctor for compatible profiles.')
    item = PROFILES[profile]
    root = store.root / 'assets' / 'voice-wash'
    location = root / profile
    lock_sha = filehash(LOCK)
    environment = root / ('python-' + lock_sha[:16])
    for directory in (root, location, environment):
        if directory.is_symlink() or directory.is_junction():
            raise Error('unsafe_path', 'Voice preparation requires owned local directories.')
        directory.mkdir(exist_ok=True)
    model = location / item['file']
    python = environment / 'bin' / 'python'
    needed = remaining_bytes(item['url'], model, item['sha256'], item['bytes'])
    if shutil.disk_usage(root).free < needed + 320 * 1024**2:
        raise Error('disk_space', 'Free space for the voice preparation worker and its model.')
    if not python.is_file():
        if not shutil.which('uv'):
            raise Error('missing_dependency', 'Install uv to set up the optional voice preparation worker.')
        progress.stage('Installing the local voice preparation runtime')
        from .core import run
        version = sys.executable if sys.version_info[:2] == (3, 12) else '3.12'
        run(['uv', 'venv', '--allow-existing', '--python', version, environment], timeout=240)
    if not (environment / 'installed.lock').is_file():
        from .core import run
        run(['uv', '--no-config', 'pip', 'sync', '--python', python, '--require-hashes',
             '--only-binary', ':all:', LOCK], timeout=600)
        from .core import atomic
        atomic(environment / 'installed.lock', lock_sha.encode())
    if cache and not model.exists():
        source = next((path for path in (Path(cache)/profile/item['file'], Path(cache)/item['file'])
                       if path.is_file()), None)
        if source:
            from .core import atomic
            contents = source.read_bytes() if source.stat().st_size == item['bytes'] else b''
            if digest(contents) != item['sha256']:
                raise Error('asset_integrity', 'Cached voice preparation file differs from the catalog.')
            atomic(model, contents)
    acquire(item['url'], model, item['sha256'], item['bytes'], progress=progress)
    setup = {'schema_version': 1, 'profile': profile, 'model_sha256': item['sha256'], 'lock_sha256': lock_sha}
    progress.stage(f'Checking the {profile} CPU worker')
    import numpy as np
    with tempfile.TemporaryDirectory(dir=store.root/'runtime') as temporary:
        source, result = Path(temporary)/'source.npy', Path(temporary)/'output.npy'
        time = np.arange(48000, dtype=np.float32) / 48000
        np.save(source, .05*np.sin(2*np.pi*180*time), allow_pickle=False)
        report = execute_worker({**setup, 'python': python, 'model': model}, source, result)
        audio = np.load(result, allow_pickle=False)
        if audio.shape != (48000,) or not np.isfinite(audio).all() or report.get('samples') != 48000:
            raise Error('wash_setup', 'The preparation worker failed its complete-audio startup check.')
    receipt = {**setup, **report}
    writejson(location / 'setup.json', receipt)
    return receipt
