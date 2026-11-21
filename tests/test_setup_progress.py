import io
from pathlib import Path

from audiobook import installation, setup_progress


def test_resumed_progress_reports_new_bytes_per_second_and_range_reset(monkeypatch):
    times = iter([0, 5, 6, 11])
    monkeypatch.setattr(setup_progress.time, 'monotonic', lambda: next(times))
    output = io.StringIO()
    progress = setup_progress.SetupProgress(output)
    progress.transfer('model.bin', 4000, 10000)
    progress.transfer('model.bin', 8000, 10000)
    progress.transfer('model.bin', 2000, 10000)
    progress.transfer('model.bin', 10000, 10000, complete=True)
    lines = output.getvalue().splitlines()
    assert '40.0%' in lines[0]
    assert '80.0%' in lines[1] and '800.0 B/s' in lines[1]
    assert '20.0%' in lines[2] and 'ETA —' in lines[2]
    assert '100.0%' in lines[3] and 'ETA 0:00' in lines[3]
    assert '\033' not in output.getvalue()


def test_doctor_distinguishes_managed_install_from_shadowed_command(tmp_path, monkeypatch):
    target = tmp_path / '.local/bin/hamr'
    target.parent.mkdir(parents=True)
    target.write_text('#!/bin/sh\nprintf "%s\\n" 0.2.1\n')
    target.chmod(0o755)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.setattr(installation.shutil, 'which', lambda name: '/some/earlier/bin/hamr' if name == 'hamr' else None)
    result, issues = installation.inspect()
    assert result['installed_version'] == '0.2.1'
    assert {i['code'] for i in issues} == {'installed_version_differs', 'installed_command_shadowed'}
