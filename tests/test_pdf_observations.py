import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from audiobook.core import Error
from audiobook.pdf_ocr import observe

HEADER = 'level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n'


def config(identity='model-one', reusable=True):
    return Path('/fake/tesseract'), {}, {'model_sha256': identity}, reusable


def test_completed_page_observation_survives_later_interruption(tmp_path, monkeypatch):
    calls = []
    def recognize(*args, **kwargs):
        calls.append(kwargs['input'])
        if kwargs['input'] == b'page-two':
            raise KeyboardInterrupt
        return SimpleNamespace(stdout=HEADER.encode())
    monkeypatch.setattr('audiobook.pdf_ocr.subprocess.run', recognize)
    assert observe(b'page-one', config(), tmp_path) == (HEADER, False)
    with pytest.raises(KeyboardInterrupt):
        observe(b'page-two', config(), tmp_path)
    assert observe(b'page-one', config(), tmp_path) == (HEADER, True)
    assert calls == [b'page-one', b'page-two']


def test_image_observer_and_corrupt_bytes_cannot_reuse_old_observation(tmp_path, monkeypatch):
    calls = []
    def recognize(*args, **kwargs):
        calls.append(kwargs['input'])
        return SimpleNamespace(stdout=HEADER.encode())
    monkeypatch.setattr('audiobook.pdf_ocr.subprocess.run', recognize)
    observe(b'first-image', config(), tmp_path)
    original = next(tmp_path.iterdir())
    assert observe(b'changed-image', config(), tmp_path)[1] is False
    assert observe(b'first-image', config('changed-model'), tmp_path)[1] is False
    old = json.loads(original.read_text());old['tsv'] = {'corrupt': True};original.write_text(json.dumps(old))
    # The malformed entry is rejected and replaced, not treated as a page pass.
    assert observe(b'first-image', config(), tmp_path)[1] is False
    assert len(calls) == 4


def test_unidentified_environment_is_not_cached_and_symlink_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr('audiobook.pdf_ocr.subprocess.run', lambda *a, **k: SimpleNamespace(stdout=HEADER.encode()))
    assert observe(b'image', config(reusable=False), tmp_path)[1] is False
    assert not list(tmp_path.iterdir())
    target = tmp_path / 'outside';target.mkdir();link = tmp_path / 'link';link.symlink_to(target, target_is_directory=True)
    with pytest.raises(Error, match='symlink'):
        observe(b'image', config(), link)
