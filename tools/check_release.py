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
              'PKG-INFO', '.gitignore', 'tools/check_release.py',
              'docs/USAGE.md', 'docs/CAST.md', 'docs/ARTWORK.md',
              'docs/art/hamr-storyforge.png', 'docs/art/hamr-listener.png'}


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
            r'audiobook/(?:[a-z_]+\.py|cast\.json|catalog\.json|cast_assets/[a-z0-9-]+/(?:reference\.wav|voice\.json))'
            r'|hamr/__(?:init|main)__\.py', runtime)
        allowed_metadata = wheel and name.startswith(metadata) and name.removeprefix(metadata) in (
            'METADATA', 'WHEEL', 'RECORD', 'entry_points.txt', 'licenses/LICENSE')
        allowed_source = not wheel and (name in ROOT_FILES or re.fullmatch(r'tests/[a-z0-9_]+\.py', name)
                                       or name == 'fixtures/technical.md')
        if not (allowed_runtime or allowed_metadata or allowed_source):
            raise ReleaseError(f'Unexpected release file: {name}')
        if not name.endswith('/reference.wav'):
            for pattern in PRIVATE_TEXT:
                if pattern.search(data):
                    raise ReleaseError(f'Private path or key pattern found in {name}')
    try:
        cast = json.loads(files[package + 'audiobook/cast.json'])['characters']
    except (KeyError, ValueError, TypeError) as exc:
        raise ReleaseError('Missing or invalid character catalogue.') from exc
    expected = set()
    for entry in cast:
        if entry.get('sample_kind') != 'reference':
            raise ReleaseError('Public cast browsing must use the final reference.')
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
    return {'artifact': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'files': len(files), 'characters': len(cast), 'character_files': len(actual),
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
