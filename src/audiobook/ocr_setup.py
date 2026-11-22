"""Install one qualified CPU OCR recipe without changing speech settings."""
from pathlib import Path
import shutil
import subprocess

from .core import Error, atomic, digest, loadjson, writejson
from .pdf_ocr import configuration, tesseract_binary

PROFILE = 'tesseract-fast'
MODEL_SHA256 = '7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2'
MODEL_BYTES = 4_113_088
NOTICE_BYTES = 11_358
NOTICE_SHA256 = 'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'
UPSTREAM = 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/4.1.0/'
RECIPE = 'eng-fast-4.1.0'


def english_data(root):
    directory = root / 'assets' / 'ocr' / RECIPE
    try:
        saved = loadjson(root / 'ocr-settings.json')
        if saved.get('profile') == PROFILE and saved.get('model_sha256') == MODEL_SHA256:
            model = directory / 'eng.traineddata'
            if model.is_file() and not model.is_symlink() and model.stat().st_size == MODEL_BYTES:
                return directory
    except (OSError, ValueError, AttributeError):
        pass
    return None


def describe(store, host):
    binary, _ = tesseract_binary()
    managed = english_data(store.root) is not None
    ready = binary.is_file()
    available = host['ram_available_bytes']
    reason = ('The installed Tesseract can run English OCR without a graphics device.' if ready else
              'Install the Tesseract executable for this operating system before OCR setup.')
    return {
        'ready': ready and managed, 'local_engine': str(binary) if ready else None,
        'recommended_profile': PROFILE if ready else None, 'active_profile': PROFILE if ready and managed else None,
        'device': 'cpu', 'languages': ['en'], 'download_bytes': 0 if managed else MODEL_BYTES+NOTICE_BYTES,
        'available_ram_bytes': available, 'reason': reason,
        'action': 'hamr setup --ocr-only' if ready else 'Install Tesseract, then run hamr setup --ocr-only.',
    }


def preflight(profile):
    if profile not in ('auto', PROFILE):
        raise Error('ocr_profile', f'OCR profile {profile!r} is unavailable.', f'Use {PROFILE} or auto.')
    binary, _ = tesseract_binary()
    if not binary.is_file():
        raise Error('missing_ocr', 'The Tesseract executable was not found.',
                    'Install Tesseract for this operating system, then run hamr setup --ocr-only.')


def provision(store, progress, cache=None, profile='auto', *, activate=True):
    from .backend import acquire, remaining_bytes
    preflight(profile)
    directory = store.root / 'assets' / 'ocr' / RECIPE
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink():
        raise Error('unsafe_path', 'OCR assets must use the owned Hamr directory.')
    required = (("eng.traineddata",MODEL_BYTES,MODEL_SHA256,"English scanned-PDF model"),
                ('LICENSE',NOTICE_BYTES,NOTICE_SHA256,'English OCR model license'))
    needed = sum(size if cache and (Path(cache)/name).is_file() and not (directory/name).exists()
                 else remaining_bytes(UPSTREAM+name,directory/name,expected,size)
                 for name,size,expected,_ in required)
    if needed and shutil.disk_usage(directory).free < needed+32*1024**2:
        raise Error('disk_space', f'Insufficient room for OCR assets at {directory}.')
    for name,size,expected,label in required:
        target = directory/name
        if target.is_symlink():
            raise Error('unsafe_path', 'OCR assets must be regular, local files.')
        if cache and not target.exists():
            src = Path(cache)/name
            if src.is_file():
                if src.stat().st_size != size:
                    raise Error('asset_integrity',f'The supplied OCR asset differs from the pinned file: {name}.')
                data = src.read_bytes()
                if digest(data) != expected:
                    raise Error('asset_integrity',f'The supplied OCR asset differs from the pinned file: {name}.')
                atomic(target,data)
        acquire(UPSTREAM+name,target,expected,size,progress=progress,label=label)
    binary, env, identity, _ = configuration(directory)
    if identity['model_sha256'] != MODEL_SHA256:
        raise Error('asset_integrity','The active OCR data differs from the pinned English model.')
    progress.stage('Checking local English OCR')
    import io
    from PIL import Image, ImageDraw, ImageFont
    image = Image.new('RGB', (1000, 360), 'white')
    text = 'Scanned page check.\nThis local test reads a printed page.\nThe speech engine stays closed.'
    ImageDraw.Draw(image).multiline_text((50,50), text, font=ImageFont.load_default(size=32), fill='black', spacing=12)
    png = io.BytesIO()
    image.save(png, format='PNG')
    from .pdf_ocr import observe
    try:
        result, _ = observe(png.getvalue(), (binary, env, identity, False))
    except (Error, OSError, subprocess.SubprocessError) as exc:
        raise Error('ocr_setup_failed', f'Tesseract could not handle the local startup check at {binary}.',
                    'Check that the Tesseract installation can run, then repeat hamr setup --ocr-only.') from exc
    if [row.split('\t',11)[-1] for row in result.splitlines() if row.startswith('5\t')] != text.split():
        raise Error('ocr_setup_failed', 'Tesseract did not recover the English startup phrase.')
    saved = {'schema_version':1, 'profile':PROFILE, 'model_sha256':MODEL_SHA256,
             'model_license':'Apache-2.0', 'model_terms':UPSTREAM+'LICENSE',
             'model_notice_sha256':NOTICE_SHA256,
             'runtime':'Tesseract', 'device':'cpu', 'languages':['en']}
    if activate:
        writejson(store.root / 'ocr-settings.json', saved)
    return saved
