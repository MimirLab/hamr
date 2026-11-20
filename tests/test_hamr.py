"""Branding must preserve pinned jobs and legacy entry points."""
import json
from pathlib import Path

import pytest

from audiobook.cli import main, parser
from audiobook.core import Error, Store


@pytest.fixture
def user_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.delenv('HAMR_HOME', raising=False)
    monkeypatch.delenv('AUDIOBOOK_HOME', raising=False)
    return tmp_path


def test_fresh_install_uses_hamr_state(user_home):
    store = Store()
    assert store.root == user_home / '.local/share/hamr'
    assert not (user_home / '.local/share/audiobook').exists()
    store.db.close()


def test_legacy_state_reused_without_changing_job(user_home, capsys):
    legacy = user_home / '.local/share/audiobook'
    source = user_home / 'book.md'
    source.write_text('# A book\n\nThis is a complete sentence for the saved book.\n')
    assert main(['--home', str(legacy), 'queue', str(source), '--json']) == 0
    queued = json.loads(capsys.readouterr().out)
    before = Store(legacy)
    payload = before.get(queued['job'])
    before.db.close()
    after = Store()
    assert after.root == legacy
    assert after.get(queued['job']) == payload
    assert not (user_home / '.local/share/hamr').exists()
    after.db.close()


def test_environment_precedence_and_explicit_home(user_home, monkeypatch):
    old, new, explicit = [user_home / name for name in ('old', 'new', 'explicit')]
    monkeypatch.setenv('AUDIOBOOK_HOME', str(old))
    store = Store()
    assert store.root == old
    store.db.close()
    monkeypatch.setenv('HAMR_HOME', str(new))
    store = Store()
    assert store.root == new
    store.db.close()
    store = Store(explicit)
    assert store.root == explicit
    store.db.close()


def test_existing_hamr_state_takes_precedence_over_legacy(user_home):
    for name in ('audiobook', 'hamr'):
        Store(user_home / '.local/share' / name).db.close()
    store = Store()
    assert store.root == user_home / '.local/share/hamr'
    store.db.close()


def test_unowned_new_directory_does_not_hide_legacy_state(user_home):
    legacy = user_home / '.local/share/audiobook'
    Store(legacy).db.close()
    current = user_home / '.local/share/hamr'
    current.mkdir()
    (current / 'unrelated').write_text('Keep me.')
    store = Store()
    assert store.root == legacy
    store.db.close()
    assert (current / 'unrelated').read_text() == 'Keep me.'


def test_renamed_home_retains_symlink_guard(user_home, monkeypatch):
    target = user_home / 'real'
    Store(target).db.close()
    link = user_home / 'linked'
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.setenv('HAMR_HOME', str(link))
    with pytest.raises(Error, match='cannot be a symlink'):
        Store()


@pytest.mark.parametrize('args', [
    ['--json', 'voice', 'list'], ['voice', '--json', 'list'], ['voice', 'list', '--json'],
])
def test_json_flag_supported_at_each_command_level(user_home, capsys, args):
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out) == {'schema_version': 1, 'voices': []}


def test_help_uses_hamr_command(capsys):
    with pytest.raises(SystemExit) as exc:
        parser().parse_args(['--help'])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert 'usage: hamr ' in help_text
    assert 'HAMR_HOME' in help_text
