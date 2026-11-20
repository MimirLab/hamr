"""Publication boundaries must reject extra data and broken cast references."""
import hashlib
import json
from pathlib import Path
import tarfile
from io import BytesIO
from zipfile import ZipFile

import pytest

from tools.check_release import ReleaseError, audit


def contents(prefix=''):
    data = b'RIFF synthetic reference fixture'
    return {
        prefix + 'audiobook/cast.json': json.dumps({'characters': [
            {'id': 'example', 'sample_kind': 'reference'},
        ]}).encode(),
        prefix + 'audiobook/cast_assets/example/reference.wav': data,
        prefix + 'audiobook/cast_assets/example/voice.json': json.dumps({
            'transcript': 'A public reference fixture.',
            'reference_sha256': hashlib.sha256(data).hexdigest(),
            'source': 'Synthetic test recording',
        }).encode(),
    }


def wheel(tmp_path, files):
    path = tmp_path / 'hamr-0.2.1-py3-none-any.whl'
    with ZipFile(path, 'w') as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return path


def test_only_final_reference_and_metadata_pass(tmp_path):
    result = audit(wheel(tmp_path, contents()), '0.2.1')
    assert result['characters'] == 1 and result['character_files'] == 2


@pytest.mark.parametrize('name', [
    '.git/config', 'evidence/book-plan.json', 'jobs/state.sqlite',
    'audiobook/cast_assets/example/sample.wav',
    'docs/art/hamr-storyforge.png',
    'docs/listen/example.mp3',
])
def test_extra_workspace_or_audition_file_rejected(tmp_path, name):
    files = contents()
    files[name] = b'Do not publish this extra file.'
    with pytest.raises(ReleaseError, match='Unexpected release file'):
        audit(wheel(tmp_path, files), '0.2.1')


def test_private_home_path_in_product_text_rejected(tmp_path):
    files = contents()
    files['audiobook/cli.py'] = b'path = ' + b'/'.join([b'', b'home', b'example', b'private'])
    with pytest.raises(ReleaseError, match='Private path'):
        audit(wheel(tmp_path, files), '0.2.1')


def test_reference_checksum_must_match(tmp_path):
    files = contents()
    files['audiobook/cast_assets/example/reference.wav'] += b'changed'
    with pytest.raises(ReleaseError, match='Reference metadata'):
        audit(wheel(tmp_path, files), '0.2.1')


def source(tmp_path, files):
    path = tmp_path / 'hamr-0.2.1.tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        for name, data in files.items():
            item = tarfile.TarInfo('hamr-0.2.1/' + name)
            item.size = len(data)
            archive.addfile(item, BytesIO(data))
    return path


def introduction_files():
    data = b'synthetic public audio fixture'
    return {
        'docs/listen/example.mp3': data,
        'docs/listen/manifest.json': json.dumps({'schema_version': 1, 'kind': 'hamr-generated-introductions',
            'recordings': [{'character': 'example', 'file': 'example.mp3', 'text': 'An invitation to read.',
                            'sha256': hashlib.sha256(data).hexdigest(), 'seconds': 5}]}).encode(),
    }


def test_source_archive_uses_same_boundary(tmp_path):
    files = {**contents('src/'), **introduction_files()}
    files['README.md'] = b'Public documentation.'
    files['tests/test_faithful3.py'] = b'# Generic regression test module with a version in its name.\n'
    files['docs/USAGE.md'] = b'Public command guide.'
    files['docs/art/hamr-storyforge.png'] = b'Public illustration fixture.'
    result = audit(source(tmp_path, files), '0.2.1')
    assert result['character_files'] == 2 and result['public_introductions'] == 1


@pytest.mark.parametrize('fault', ['no_manifest', 'changed_audio', 'unknown_character', 'extra_file', 'incomplete_cast'])
def test_source_introductions_require_exact_public_cast_and_hashes(tmp_path, fault):
    files = {**contents('src/'), **introduction_files()}
    if fault == 'no_manifest':
        del files['docs/listen/manifest.json']
    elif fault == 'changed_audio':
        files['docs/listen/example.mp3'] += b'changed'
    elif fault == 'extra_file':
        files['docs/listen/extra.mp3'] = b'unapproved extra'
    else:
        manifest = json.loads(files['docs/listen/manifest.json'])
        if fault == 'incomplete_cast': manifest['recordings'] = []
        else: manifest['recordings'][0]['character'] = 'unknown'
        files['docs/listen/manifest.json'] = json.dumps(manifest).encode()
    with pytest.raises(ReleaseError, match='public introductions'):
        audit(source(tmp_path, files), '0.2.1')
