"""Checkpoint raw visual OCR independently of PDF structure and listening policy."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

from .core import Error, digest, filehash, key, writejson


def configuration():
    root = Path(os.environ.get('AUDIOBOOK_OCR_ROOT', Path.home() / '.local/share/audiobook-tools/ocr'))
    binary = Path(shutil.which('tesseract') or root / 'usr/bin/tesseract')
    if not binary.is_file():
        raise Error('missing_ocr', 'Raster PDF page needs Tesseract.',
                    'Install Tesseract plus English data; replan the source.')
    env = os.environ.copy()
    env['OMP_THREAD_LIMIT'] = '2'
    private = binary.is_relative_to(root)
    if private:
        env['LD_LIBRARY_PATH'] = str(root / 'usr/lib')
        env['TESSDATA_PREFIX'] = str(root / 'usr/share/tessdata')
    candidates = [Path(env['TESSDATA_PREFIX']) / 'eng.traineddata'] if env.get('TESSDATA_PREFIX') else []
    candidates += [Path('/usr/share/tessdata/eng.traineddata'),
                   Path('/usr/share/tesseract-ocr/5/tessdata/eng.traineddata')]
    model = next((p for p in candidates if p.is_file()), None)
    identity = {
        'schema_version': 1,
        'binary_sha256': filehash(binary),
        'model_sha256': filehash(model) if model else None,
        'language': 'eng',
        'page_segmentation': 'default',
        'output': 'tsv',
        'OMP_THREAD_LIMIT': 2,
        'private_library_sha256': {
            str(p.relative_to(root)): filehash(p)
            for p in sorted((root / 'usr/lib').glob('*.so*')) if p.is_file()
        } if private else {},
    }
    # Without the actual model/library identity, a system OCR installation can
    # still run, but must not inherit observations from a different environment.
    reusable = private and model is not None
    return binary, env, identity, reusable


def observe(png, config, cache=None):
    """Return exact TSV; successful observations survive interrupted PDF import."""
    binary, env, identity, reusable = config
    binding = {'image_sha256': digest(png), 'observer': identity}
    target = None
    if cache is not None and reusable:
        cache = Path(cache)
        if cache.is_symlink():
            raise Error('unsafe_path', 'PDF observation cache cannot be a symlink.')
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (key(binding) + '.json')
        if target.is_symlink():
            raise Error('unsafe_path', 'PDF observation cannot be a symlink.')
        if target.exists():
            try:
                if target.stat().st_size <= 4 * 1024 * 1024:
                    old = json.loads(target.read_text())
                    if isinstance(old.get('tsv'),str) and old['binding'] == binding and digest(old['tsv'].encode()) == old['tsv_sha256']:
                        return old['tsv'], True
            except (OSError, ValueError, KeyError, TypeError):
                pass
    result = subprocess.run(
        [str(binary), 'stdin', 'stdout', '-l', 'eng', 'tsv'], env=env,
        input=png, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        timeout=90, check=True,
    )
    tsv = result.stdout.decode()
    if len(result.stdout) > 4 * 1024 * 1024:
        raise Error('ocr_size', 'OCR response exceeds the per-page observation limit.')
    if not tsv.startswith('level\tpage_num\t'):
        raise Error('ocr_contract', 'OCR returned an invalid TSV header.')
    if target is not None:
        writejson(target, {'binding': binding, 'tsv': tsv, 'tsv_sha256': digest(tsv.encode())})
    return tsv, False
