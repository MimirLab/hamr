"""Checkpoint raw visual OCR independently of PDF structure and listening policy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .core import Error, digest, filehash, key, writejson
from .platform_support import close_windows_job, spawn_owned

MAX_RESPONSE = 4 * 1024 * 1024


RESIDENT = Path(__file__).with_name('ocr_resident')


LINUX_OCR_SHA256 = '31414b49ddaa2b2cb1d33c888b25cb06e1b6e70d1f0e1ed00d4b7a1f721f326a'
WINDOWS_OCR_SHA256 = 'fc95332f7f8c2e472635e40669ac4767b366225bbff5bd30489c16c008c5c95d'


def resident_binary():
    import platform
    if platform.machine().lower() not in ('x86_64','amd64'):
        return None
    if sys.platform.startswith('linux'):
        libc,version = platform.libc_ver()
        if libc!='glibc' or not version or tuple(map(int,version.split('.')[:2]))<(2,34):
            return None
        target = RESIDENT/'tesseract-linux-x86_64'
        checksum = LINUX_OCR_SHA256
    elif os.name=='nt':
        target = RESIDENT/'tesseract-windows-x86_64.exe'
        checksum = WINDOWS_OCR_SHA256
    else:
        return None
    if target.is_file() and os.access(target,os.X_OK):
        if filehash(target)!=checksum:
            raise Error('asset_integrity','Bundled OCR runtime failed its checksum.')
        return target
    return None


def tesseract_binary():
    root = Path(os.environ.get('AUDIOBOOK_OCR_ROOT', Path.home() / '.local/share/audiobook-tools/ocr'))
    local = root/'usr/bin/tesseract'
    if 'AUDIOBOOK_OCR_ROOT' in os.environ:
        return local, root
    builtin = resident_binary()
    if builtin:
        return builtin, root
    if installed := shutil.which('tesseract'):
        return Path(installed), root
    return local, root


def system_libraries(binary,env):
    if not sys.platform.startswith('linux') or not shutil.which('ldd'):
        return {}
    try:
        output = subprocess.check_output(['ldd',str(binary)],env=env,stderr=subprocess.DEVNULL,timeout=5,text=True)
        if 'not found' in output:
            return {}
        paths = {Path(word) for word in output.split() if word.startswith('/')}
        return {str(path):filehash(path) for path in sorted(paths)}
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}


def run_tesseract(args, *, input, env):
    process, job = spawn_owned(args,parent_death=True,env=env,stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(input,timeout=90)
        if process.returncode:
            raise subprocess.CalledProcessError(process.returncode,args,stdout,stderr)
        return stdout
    finally:
        if job:
            close_windows_job(job)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for stream in (process.stdin,process.stdout,process.stderr):
            if stream:
                stream.close()


def configuration(tessdata=None):
    binary, root = tesseract_binary()
    if not binary.is_file():
        raise Error('missing_ocr', 'Raster pages need a compatible Tesseract runtime.',
                    'Install a compatible local Tesseract runtime, then resume the saved source.')
    env = os.environ.copy()
    env['OMP_THREAD_LIMIT'] = '2'
    private = binary.is_relative_to(root)
    if private:
        env['LD_LIBRARY_PATH'] = str(root / 'usr/lib')
        env['TESSDATA_PREFIX'] = str(root / 'usr/share/tessdata')
    if tessdata is not None or binary.parent==RESIDENT:
        env['TESSDATA_PREFIX'] = str(tessdata or RESIDENT)
    candidates = [Path(env['TESSDATA_PREFIX']) / 'eng.traineddata'] if env.get('TESSDATA_PREFIX') else []
    if not candidates:
        candidates = [Path('/usr/share/tessdata/eng.traineddata'),
                   Path('/usr/share/tesseract-ocr/5/tessdata/eng.traineddata')]
    model = next((p for p in candidates if p.is_file()), None)
    if model is None and tessdata is None and (RESIDENT/'eng.traineddata').is_file():
        from .ocr_setup import MODEL_SHA256
        model = RESIDENT/'eng.traineddata'
        if filehash(model)!=MODEL_SHA256:
            raise Error('asset_integrity','Bundled English OCR model failed its checksum.')
        env['TESSDATA_PREFIX'] = str(RESIDENT)
    if (tessdata is not None or binary.parent==RESIDENT) and model is None:
        raise Error('missing_ocr_data','Managed English OCR data is missing.', 'Run hamr setup --ocr-only.')
    libraries = {str(p.relative_to(root)):filehash(p)
                 for p in sorted((root/'usr/lib').glob('*.so*')) if p.is_file()} if private else {}
    system = system_libraries(binary,env) if not private else {}
    identity = {
        'schema_version': 1,
        'binary_sha256': filehash(binary),
        'model_sha256': filehash(model) if model else None,
        'language': 'eng',
        'page_segmentation': 'default',
        'output': 'tsv',
        'OMP_THREAD_LIMIT': 2,
        'private_library_sha256': libraries,
    }
    if system: identity['system_library_sha256'] = system
    # Without the actual model/library identity, a system OCR installation can
    # still run, but must not inherit observations from a different environment.
    reusable = bool(libraries or system or binary.parent==RESIDENT) and model is not None
    return binary, env, identity, reusable


def read_checkpoint(cache, binding):
    target = cache / (key(binding) + '.json')
    if target.is_symlink():
        raise Error('unsafe_path', 'PDF observation cannot be a symlink.')
    try:
        if target.stat().st_size <= MAX_RESPONSE:
            old = json.loads(target.read_text())
            tsv = old.get('tsv')
            if (isinstance(tsv,str) and tsv.startswith('level\tpage_num\t') and old['binding'] == binding
                    and digest(tsv.encode()) == old['tsv_sha256']):
                return tsv
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass


def reference_path(cache, image_sha, scope):
    return cache / (key({'image':image_sha,'scope':scope}) + '.ref.json')


def offline_observation(png, cache, scope):
    """Reuse the last identified recipe for this image when its runtime is absent."""
    if cache is None or cache.is_symlink():
        return None
    image_sha = digest(png)
    reference = reference_path(cache,image_sha,scope)
    if reference.is_symlink():
        raise Error('unsafe_path','OCR observation reference cannot be a symlink.')
    try:
        if reference.stat().st_size > 128*1024:
            return None
        receipt = json.loads(reference.read_text())
        binding = receipt['binding']
        if receipt['scope'] != scope or binding['image_sha256'] != image_sha:
            return None
        if tsv := read_checkpoint(cache,binding):
            return tsv,binding['observer']
    except (OSError,ValueError,KeyError,TypeError):
        pass


def observe(png, config, cache=None, scope=None):
    """Return exact TSV; successful observations survive interrupted PDF import."""
    binary, env, identity, reusable = config
    binding = {'image_sha256': digest(png), 'observer': identity}
    target = reference = None
    if cache is not None and reusable:
        cache = Path(cache)
        if cache.is_symlink():
            raise Error('unsafe_path', 'PDF observation cache cannot be a symlink.')
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (key(binding) + '.json')
        if scope is not None:
            reference = reference_path(cache,binding['image_sha256'],scope)
            if reference.is_symlink():
                raise Error('unsafe_path','OCR observation reference cannot be a symlink.')
        if old := read_checkpoint(cache,binding):
            if reference and not reference.exists():
                writejson(reference,{'binding':binding,'scope':scope})
            return old, True
    segmentation = identity.get('page_segmentation')
    args = ['--psm',str(segmentation)] if isinstance(segmentation,int) else []
    stdout = run_tesseract(
        [str(binary), 'stdin', 'stdout', '-l', 'eng', *args, '-c', 'tessedit_create_tsv=1'], env=env,
        input=png,
    )
    if len(stdout) > MAX_RESPONSE:
        raise Error('ocr_size', 'OCR response exceeds the per-page observation limit.')
    try:
        tsv = stdout.decode()
    except UnicodeDecodeError as exc:
        raise Error('ocr_contract','OCR returned an invalid text encoding.') from exc
    if not tsv.startswith('level\tpage_num\t'):
        raise Error('ocr_contract', 'OCR returned an invalid TSV header.')
    if target is not None:
        writejson(target, {'binding': binding, 'tsv': tsv, 'tsv_sha256': digest(tsv.encode())})
    if reference is not None:
        writejson(reference,{'binding':binding,'scope':scope})
    return tsv, False
