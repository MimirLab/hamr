"""Front door, review checkpoint, and machine inspection regressions."""
import json
from pathlib import Path

from audiobook import cli, jobs, operator_cli
from audiobook.core import Store
from test_listening_findings import matching_observations
from test_media import prepared


def invoke(home, capsys, *args, expected=0):
    assert cli.main(['--home', str(home), *map(str, args), '--json']) == expected
    return json.loads(capsys.readouterr().out)


def test_open_checks_and_publishes_without_a_separate_qa_command(tmp_path, capsys, monkeypatch):
    source = tmp_path / 'A short book.md'
    source.write_text('A quiet bird waited on an old oak branch.\n')
    store, old = prepared(tmp_path, source)
    matching_observations(monkeypatch)
    monkeypatch.setattr('audiobook.qa_cache.observer_identity', lambda *args: {'model_files': {}})
    checker = (Path('/fixture/python'), Path('/fixture/model'), None)
    monkeypatch.setattr('audiobook.book_flow.observer', lambda *args: checker)

    def create_with_known_pcm(*args, **kwargs):
        # Exercise open against real saved clips without substituting the
        # publication pipeline or requiring a GPU in the test suite.
        return jobs.summary(store, old)

    monkeypatch.setattr(cli, 'create', create_with_known_pcm)
    # The fixture has a backend pinned to its job. Make preflight see the
    # settings of a user who has run setup.
    (store.root / 'settings.json').write_text('{}')
    result = invoke(store.root, capsys, 'open', source, '--voice', 'cast:nigel-thornberry',
                    '--output', old['output_parent'], '--yes')
    assert result['status'] == 'ready'
    assert (Path(result['output']) / 'book.m4b').is_file()
    assert result['pipeline_progress']['phase'] == 'complete'
    assert jobs.take_signature(store, store.get(old['id'])) == store.get(old['id'])['audio_gate_approved_takes']


def test_open_reports_missing_speech_checker_before_creating_job(tmp_path, capsys):
    store = Store(tmp_path / 'state')
    (store.root / 'settings.json').write_text('{}')
    source = tmp_path / 'Book.md'
    source.write_text('This is the book.')
    result = invoke(store.root, capsys, 'open', source, '--voice', 'cast:nigel-thornberry',
                    '--yes', expected=2)
    assert result['error']['code'] == 'speech_checks_required'
    assert store.jobs() == []


def test_agent_inspects_without_reconciling_or_exporting_text(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / 'state')
    source = tmp_path / 'Book.md'
    source.write_text('A private line for the source fixture.')
    job = jobs.create(store, source)
    payload = store.get(job['job'])
    payload['status'] = 'rendering'
    store.save(payload)
    before = store.get(job['job'])
    def hidden_hash(*args):
        raise AssertionError('Light inspection must avoid publication hashing.')
    monkeypatch.setattr(jobs, 'verify_publication', hidden_hash)
    result = invoke(store.root, capsys, 'agent', 'inspect', job['job'])
    assert 'A private line' not in json.dumps(result)
    assert result['status'] == 'rendering' and result['publication_verified'] is None
    assert store.get(job['job']) == before
    result = invoke(store.root, capsys, 'agent', 'inspect', job['job'], '--content')
    assert 'A private line' in json.dumps(result['plan'])


def test_watch_emits_one_initial_snapshot_and_never_hashes(tmp_path, capsys, monkeypatch):
    store = Store(tmp_path / 'state')
    source = tmp_path / 'book.md'
    source.write_text('A complete sentence.')
    job = jobs.create(store, source)
    original = operator_cli.read_job
    reads = []
    def observe(*args):
        reads.append(args[1])
        return original(*args)
    monkeypatch.setattr(operator_cli, 'read_job', observe)
    assert cli.main(['--home', str(store.root), 'agent', 'watch', job['job'],
                     '--interval', '0.1', '--timeout', '0.22']) == 0
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(rows) == 1 and reads == [job['job']]
    assert rows[0]['job'] == job['job']


def test_repair_and_review_reject_stale_revision(tmp_path, capsys):
    store = Store(tmp_path / 'state')
    source = tmp_path / 'book.md'
    source.write_text('A complete sentence.')
    job = jobs.create(store, source)
    before = store.get(job['job'])
    steps = [('repair', '--retake', 'u00001'), ('review', '--accept', 'u00001', '--reason', 'Checked')]
    for command, *options in steps:
        failure = invoke(store.root, capsys, command, job['job'], *options, '--expect-revision', 9, expected=2)
        assert failure['error']['code'] == 'revision_changed'
        assert store.get(job['job']) == before


def test_automation_discovery_and_completion_do_not_create_state(tmp_path, capsys):
    home = tmp_path / 'unused'
    result = invoke(home, capsys, 'agent', 'capabilities')
    assert 'open' in result['cli']['commands']
    assert result['cli']['commands']['setup']['arguments']
    assert not home.exists()
    for shell in ('bash', 'zsh', 'fish'):
        assert cli.main(['--home', str(home), 'completion', shell]) == 0
        out = capsys.readouterr().out
        assert '--engine-only' in out or '-l engine-only' in out
        assert not home.exists()
