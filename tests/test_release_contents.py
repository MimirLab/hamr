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


def test_source_archive_uses_same_boundary(tmp_path):
    path = tmp_path / 'hamr-0.2.1.tar.gz'
    files = contents('src/')
    files['README.md'] = b'Public documentation.'
    files['tests/test_faithful3.py'] = b'# Generic regression test module with a version in its name.\n'
    files['docs/USAGE.md'] = b'Public command guide.'
    files['docs/art/hamr-storyforge.png'] = b'Public illustration fixture.'
    with tarfile.open(path, 'w:gz') as archive:
        for name, data in files.items():
            item = tarfile.TarInfo('hamr-0.2.1/' + name)
            item.size = len(data)
            archive.addfile(item, BytesIO(data))
    assert audit(path, '0.2.1')['character_files'] == 2
