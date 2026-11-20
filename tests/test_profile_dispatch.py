import json

import pytest

from audiobook import cli


def test_backend_profile_reaches_setup_without_saved_profile_file(tmp_path, monkeypatch, capsys):
    seen = []
    def setup(store, profile, cache):
        seen.append((store.root, profile, cache))
        return {'profile': profile, 'reached_backend_setup': True}
    monkeypatch.setattr(cli, 'setup', setup)
    home = tmp_path / 'new installation'
    assert cli.main(['--home', str(home), 'setup', '--profile', 'vulkan-f16', '--json']) == 0
    assert seen == [(home, 'vulkan-f16', None)]
    assert not (home / 'profiles/vulkan-f16.json').exists()
    assert json.loads(capsys.readouterr().out)['reached_backend_setup']


@pytest.mark.parametrize('command', ['plan', 'queue', 'open'])
def test_missing_listening_profile_identifies_exact_path(tmp_path, command, capsys):
    home = tmp_path / 'state'
    args = ['--home', str(home), command, str(tmp_path / 'book.md'), '--profile', 'technical', '--json']
    assert cli.main(args) == 2
    error = json.loads(capsys.readouterr().out)['error']
    assert error['code'] == 'listening_profile_missing'
    assert str(home / 'profiles/technical.json') in error['message']
    assert 'hamr profile technical' in error['action']


def test_saved_reading_defaults_and_aliases_still_apply(tmp_path, capsys):
    home = tmp_path / 'state'
    assert cli.main(['--home', str(home), 'profile', 'technical', '--mode', 'procedure', '--pronounce', 'XYZZY=the example']) == 0
    capsys.readouterr()
    source = tmp_path / 'book.md'
    source.write_text('XYZZY is a complete example.\n')
    assert cli.main(['--home', str(home), 'plan', str(source), '--profile', 'technical', '--json']) == 0
    plan = json.loads(capsys.readouterr().out)
    assert plan['policy']['mode'] == 'procedure'
    assert 'the example' in ' '.join(u['text'] for u in plan['utterances'])


def test_unimplemented_backend_is_reported_before_any_download(tmp_path, monkeypatch, capsys):
    from audiobook import backend
    def forbidden(*args, **kwargs):
        raise AssertionError('No acquisition or GPU startup is allowed for a catalog-only profile')
    monkeypatch.setattr(backend, 'acquire', forbidden)
    monkeypatch.setattr(backend, 'Engine', forbidden)
    assert cli.main(['--home', str(tmp_path / 'state'), 'setup', '--profile', 'cuda-bf16-eager', '--json']) == 2
    assert json.loads(capsys.readouterr().out)['error']['code'] == 'unsupported_profile'
