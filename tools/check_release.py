"""Check product archives without depending on private development material."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import tomllib
from zipfile import ZipFile


class ReleaseError(ValueError):
    pass


PRIVATE_TEXT = (
    re.compile(rb'(?<![~\w])/(?:home|Users)/[^/\s"\'<>]+'),
    re.compile(rb'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
)
ROOT_FILES = {'README.md', 'RELEASE.md', 'LICENSE', 'pyproject.toml', 'uv.lock', 'install.sh',
              'install.ps1', 'tools/inspect_gguf.py', 'tools/compare_breeze.py', 'tools/mix_hoppou.py',
              'tools/build_timing_name_spellings.py',
              'tools/build_intake_ocr.sh', 'tools/build_intake_ocr_windows.sh', 'tools/build_intake_mobi.sh',
              'tools/intake_djvu.cc', 'tools/intake_chm.c', 'tools/build_intake_chm.sh',
              'tools/build_intake_xps.py', 'tools/build_xps_libarchive.sh', 'tools/intake_xps_runtime.json',
              'fixtures/chm_ordered.chm', 'docs/INTAKE.md',
              'tools/performance_review.py', 'tools/master_cast_gallery.py', 'docs/ENGINES.md',
              'PKG-INFO', '.gitignore', 'AGENTS.md', 'tools/check_release.py',
              'docs/USAGE.md', 'docs/CAST.md', 'docs/ARTWORK.md', 'docs/listen/manifest.json',
              'docs/art/hamr-storyforge.png', 'docs/art/hamr-listener.png',
              'docs/demo/lantern.md', 'docs/demo/lantern-plain.mp3', 'docs/demo/lantern-expressive.mp3',
              'docs/man/hamr.1', 'docs/man/audiobook.1'}
TIMING_NAME_ASSETS = {'audiobook/timing_name_spellings.json', 'audiobook/timing_name_spellings_LICENSE.txt'}


INTAKE_ASSETS = {
    'audiobook/ocr_resident/eng.traineddata':'7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2',
    'audiobook/ocr_resident/tesseract-linux-x86_64':'31414b49ddaa2b2cb1d33c888b25cb06e1b6e70d1f0e1ed00d4b7a1f721f326a',
    'audiobook/ocr_resident/tesseract-windows-x86_64.exe':'fc95332f7f8c2e472635e40669ac4767b366225bbff5bd30489c16c008c5c95d',
    'audiobook/intake_resident/mobitool-linux-x86_64':'05567860bd98a8393621183ba977c9e3c4d8abbb4747045711501a1d70bd8b34',
    'audiobook/intake_resident/mobitool-windows-x86_64.exe':'1e63bece58cf2a71f7626c6025cba4c3c20973bce912978df47ebd7e5fe22dce',
    'audiobook/intake_resident/djvu-linux-x86_64':'dc0f35ae2e2170bc3a2b4c0e7c2a16abe8608c79cc0aa6bd794ae3bb20e0f357',
    'audiobook/intake_resident/chm-linux-x86_64':'a604e6b43866ed96ebd485061bd1c7e0be018a69d56c5907da738bf83ec85672',
    'audiobook/intake_resident/xps-renderer-linux-x86_64.tar.xz':'46e0fb5bb0be2764113111f62aa7d25529187f840618a2c9f283fc01fa474dc0',
}
INTAKE_NOTICES = {
    *('audiobook/ocr_resident/'+name for name in ('LICENSE.eng','LICENSE.leptonica','LICENSE.libpng',
                'LICENSE.tesseract','LICENSE.zlib','LICENSE.windows-libpng','LICENSE.windows-zlib','SOURCES.txt')),
    *('audiobook/intake_resident/'+name for name in ('LICENSE.djvu','LICENSE.libmobi','LICENSE.gpl3','LICENSE.chmlib','SOURCES.txt')),
}


def read_archive(path):
    files = {}
    if path.suffix == '.whl':
        with ZipFile(path) as archive:
            for entry in archive.infolist():
                if stat.S_ISLNK(entry.external_attr >> 16):
                    raise ReleaseError('Archive contains a symlink.')
                if not entry.is_dir():
                    if entry.filename in files:
                        raise ReleaseError('Archive contains a duplicate path.')
                    files[entry.filename] = archive.read(entry)
    else:
        with tarfile.open(path, 'r:gz') as archive:
            for entry in archive.getmembers():
                if entry.isdir():
                    continue
                if not entry.isfile() or entry.name in files:
                    raise ReleaseError('Source archive contains a link or duplicate path.')
                files[entry.name] = archive.extractfile(entry).read()
    for name in files:
        p = PurePosixPath(name)
        if p.is_absolute() or '..' in p.parts:
            raise ReleaseError('Archive contains an unsafe path.')
    return files


def audit(path, version):
    files = read_archive(path)
    wheel = path.suffix == '.whl'
    if not wheel:
        prefix = f'hamr-{version}/'
        if not all(name.startswith(prefix) for name in files):
            raise ReleaseError('Unexpected source archive root.')
        files = {name.removeprefix(prefix): data for name, data in files.items()}
    package = '' if wheel else 'src/'
    metadata = f'hamr-{version}.dist-info/'
    for name, data in files.items():
        runtime = name.removeprefix(package) if name.startswith(package) else ''
        allowed_runtime = re.fullmatch(
            r'audiobook/(?:[a-z_]+\.py|cast\.json|catalog\.json|asr_(?:fluid_)?assets\.json|gguf_recipes\.json|context_rules\.json|performance_rules\.json|asr(?:-windows|-fluid)?\.lock|wash-runtime\.lock|agent_guide\.md|cast_assets/[a-z0-9-]+/(?:reference\.wav|voice\.json))'
            r'|hamr/__(?:init|main)__\.py', runtime)
        allowed_metadata = wheel and name.startswith(metadata) and name.removeprefix(metadata) in (
            'METADATA', 'WHEEL', 'RECORD', 'entry_points.txt', 'licenses/LICENSE')
        allowed_preview = wheel and re.fullmatch(r'audiobook/cast_previews/(?:manifest\.json|[a-z0-9-]+\.mp3)', name)
        allowed_manual = wheel and name in {f'hamr-{version}.data/data/share/man/man1/{p}.1' for p in ('hamr','audiobook')}
        allowed_source = not wheel and (name in ROOT_FILES or re.fullmatch(r'tests/[a-z0-9_]+\.py', name)
                                       or re.fullmatch(r'docs/listen/[a-z0-9-]+\.mp3', name)
                                       or name == 'fixtures/technical.md')
        if not (allowed_runtime or runtime in TIMING_NAME_ASSETS or allowed_metadata or allowed_source
                or allowed_preview or allowed_manual or runtime in INTAKE_ASSETS or runtime in INTAKE_NOTICES):
            raise ReleaseError(f'Unexpected release file: {name}')
        if not name.endswith('/reference.wav') and runtime not in INTAKE_ASSETS:
            for pattern in PRIVATE_TEXT:
                if pattern.search(data):
                    raise ReleaseError(f'Private path or key pattern found in {name}')
    if package+'audiobook/source_bundle.py' in files:
        if not {package+name for name in INTAKE_NOTICES} <= files.keys():
            raise ReleaseError('Missing a resident intake notice.')
        for name,sha in INTAKE_ASSETS.items():
            if hashlib.sha256(files.get(package+name,b'')).hexdigest()!=sha:
                raise ReleaseError(f'Missing or changed resident intake asset: {name}')
    if package + 'audiobook/book_flow.py' in files:
        manual = f'hamr-{version}.data/data/share/man/man1/' if wheel else 'docs/man/'
        required = {manual + 'hamr.1', manual + 'audiobook.1',
                    *(package + 'audiobook/' + name for name in ('asr.lock', 'asr_assets.json', 'agent_guide.md'))}
        if not required <= files.keys():
            raise ReleaseError('Missing manual, speech checker inputs or operator guide.')
        if not files[manual + 'hamr.1'].startswith(b'.TH HAMR 1 ') or files[manual + 'audiobook.1'] != b'.so man1/hamr.1\n':
            raise ReleaseError('Invalid manual title or alias.')
    if package + 'audiobook/wash_setup.py' in files:
        if package + 'audiobook/wash-runtime.lock' not in files:
            raise ReleaseError('Missing the pinned voice preparation runtime.')
    if b'timing_name_spellings.json' in files.get(package + 'audiobook/quote_timing.py',b''):
        if not {package+name for name in TIMING_NAME_ASSETS} <= files.keys():
            raise ReleaseError('Missing timing name spellings or their attribution and license.')
    try:
        cast_document = json.loads(files[package + 'audiobook/cast.json'])
        cast = cast_document['characters']
    except (KeyError, ValueError, TypeError) as exc:
        raise ReleaseError('Missing or invalid character catalogue.') from exc
    expected = set()
    for entry in cast:
        if entry.get('sample_kind') not in ('reference', 'introduction'):
            raise ReleaseError('Public cast browsing must use an approved reference or introduction.')
        base = package + 'audiobook/cast_assets/' + entry['id'] + '/'
        expected.update((base + 'reference.wav', base + 'voice.json'))
        try:
            info = json.loads(files[base + 'voice.json'])
            recording = files[base + 'reference.wav']
        except (KeyError, ValueError) as exc:
            raise ReleaseError('Missing reference or metadata.') from exc
        if (set(info) - {'transcript', 'reference_sha256', 'source'}
                or not isinstance(info.get('transcript'), str) or not info['transcript'].strip()
                or hashlib.sha256(recording).hexdigest() != info.get('reference_sha256')):
            raise ReleaseError('Reference metadata is invalid or contains extra fields.')
    actual = {name for name in files if '/cast_assets/' in name}
    if actual != expected:
        raise ReleaseError('Character files differ from final references and metadata.')
    preview_prefix = 'audiobook/cast_previews/' if wheel else 'docs/listen/'
    introductions = {name for name in files if name.startswith(preview_prefix)}
    introduction_count = 0
    if not introductions and (cast_document.get('preview_kind') == 'introduction'
                              or any(entry.get('sample_kind') == 'introduction' for entry in cast)):
        raise ReleaseError('Missing public introductions for the installed cast.')
    if introductions:
        try:
            manifest = json.loads(files[preview_prefix + 'manifest.json'])
            schema = manifest['schema_version']
            expected_fields = {'schema_version', 'kind', 'recordings'}
            if schema == 2:
                expected_fields.add('mastering')
                mastering = manifest['mastering']
                if (set(mastering) != {'source_commit', 'meter', 'target_lufs', 'max_boost_db',
                                      'input_ceiling_dbtp', 'export_ceiling_dbtp'}
                        or not re.fullmatch(r'[0-9a-f]{40}', mastering['source_commit'])):
                    raise ValueError('Missing original introduction revision')
            if (set(manifest) != expected_fields or schema not in (1, 2)
                    or manifest['kind'] != 'hamr-generated-introductions'):
                raise ValueError('Invalid introduction manifest')
            recordings = manifest['recordings']
            if ({r['character'] for r in recordings} != {c['id'] for c in cast}
                    or len(recordings) != len(cast)):
                raise ValueError('Introduction cast differs')
            expected_introductions = {preview_prefix + 'manifest.json'}
            for recording in recordings:
                fields = {'character', 'file', 'text', 'sha256', 'seconds'}
                if schema == 2:
                    fields.add('source_sha256')
                    if not re.fullmatch(r'[0-9a-f]{64}', recording['source_sha256']):
                        raise ValueError('Invalid original introduction checksum')
                if (set(recording) != fields
                        or recording['file'] != recording['character'] + '.mp3'
                        or not isinstance(recording['text'], str) or not recording['text'].strip()
                        or type(recording['seconds']) not in (int, float) or not 0 < recording['seconds'] <= 60):
                    raise ValueError('Invalid introduction fields')
                name = preview_prefix + recording['file']
                if hashlib.sha256(files[name]).hexdigest() != recording['sha256']:
                    raise ValueError('Introduction checksum differs')
                expected_introductions.add(name)
            if introductions != expected_introductions:
                raise ValueError('Unexpected introduction files')
            introduction_count = len(recordings)
        except (KeyError, ValueError, TypeError) as exc:
            raise ReleaseError('Missing or invalid public introductions; expected the exact cast and verified audio.') from exc
    return {'artifact': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'files': len(files), 'characters': len(cast), 'character_files': len(actual),
            'public_introductions': introduction_count,
            'private_path_and_key_scan': 'passed', 'allowlist': 'passed'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('artifacts', nargs='*', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    artifacts = args.artifacts or [root / 'dist' / f'hamr-{version}-py3-none-any.whl',
                                   root / 'dist' / f'hamr-{version}.tar.gz']
    try:
        results = [audit(path, version) for path in artifacts]
    except (OSError, ValueError, KeyError, tarfile.TarError) as exc:
        parser.exit(2, f'Release check failed: {exc}\n')
    print(json.dumps({'version': version, 'artifacts': results}, indent=2))


if __name__ == '__main__':
    main()
