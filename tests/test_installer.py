"""Installer control-flow checks; actual offline installation has separate evidence."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest


def installer_project(tmp_path, *, current_wheel=True):
    project = tmp_path / 'release é'
    project.mkdir()
    shutil.copy2(Path(__file__).parents[1] / 'install.sh', project / 'install.sh')
    (project / 'pyproject.toml').write_text('[project]\nversion = "0.9.9"\n')
    dist = project / 'dist'
    dist.mkdir()
    (dist / 'local_audiobook-0.1.36-py3-none-any.whl').touch()
    if current_wheel:
        (dist / 'hamr-0.9.9-py3-none-any.whl').touch()
    probes = tmp_path / 'probes'
    probes.mkdir()
    probe = probes / 'uv'
    probe.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
with open(os.environ['INSTALL_TEST_UV_LOG'], 'a') as stream:
    stream.write(json.dumps(sys.argv[1:]) + '\\n')
if sys.argv[1] == 'venv':
    bindir = Path(sys.argv[-1]) / 'bin'
    bindir.mkdir(parents=True, exist_ok=True)
    (bindir / 'python').touch()
    for name in ('hamr', 'audiobook'):
        command = bindir / name
        command.write_text('#!/bin/sh\\nprintf "%s\\\\n" 0.9.9\\n')
        command.chmod(0o755)
if sys.argv[1:3] == ['pip', 'sync']:
    Path(os.environ['INSTALL_TEST_UV_LOG'] + '.requirements').write_text(Path(sys.argv[-1]).read_text())
    pages = Path(sys.argv[sys.argv.index('--python')+1]).parents[1] / 'share/man/man1'
    pages.mkdir(parents=True, exist_ok=True)
    (pages / 'hamr.1').write_text('.TH HAMR 1\\n')
    (pages / 'audiobook.1').write_text('.so man1/hamr.1\\n')
if sys.argv[1] == 'export' and os.environ.get('INSTALL_TEST_EXPORT_FAIL'):
    sys.exit(1)
''')
    probe.chmod(0o755)
    log = tmp_path / 'uv-calls.jsonl'
    env = {**os.environ, 'PATH': str(probes) + os.pathsep + os.environ['PATH'],
           'INSTALL_TEST_UV_LOG': str(log)}
    app, commands = tmp_path / 'app é', tmp_path / 'bin é'
    return project, app, commands, log, env


def run_install(project, app, commands, env):
    return subprocess.run(
        ['sh', str(project / 'install.sh'), str(app), str(commands)],
        cwd=project.parent, env=env, text=True, capture_output=True, timeout=20,
    )


def test_installer_selects_project_version_among_older_artifacts(tmp_path):
    project, app, commands, log, env = installer_project(tmp_path)
    result = run_install(project, app, commands, env)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert calls[0][:3] == ['export', '--project', str(project)]
    assert '--locked' in calls[0] and '--no-emit-project' in calls[0]
    sync = next(call for call in calls if call[:2] == ['pip', 'sync'])
    assert '--require-hashes' in sync
    assert not Path(sync[-1]).exists()
    requirements = Path(str(log) + '.requirements').read_text()
    assert f'hamr @ {(project / "dist/hamr-0.9.9-py3-none-any.whl").as_uri()}' in requirements
    assert '--hash=sha256:' in requirements
    assert 'local_audiobook' not in requirements
    for name in ('hamr', 'audiobook'):
        assert (commands / name).resolve() == app / 'venv/bin' / name
        assert (commands.parent / 'share/man/man1' / (name + '.1')).resolve() == app / ('venv/share/man/man1/' + name + '.1')
    assert result.stdout.strip() == 'hamr 0.9.9'


def test_missing_current_wheel_does_not_install_historical_build(tmp_path):
    project, app, commands, log, env = installer_project(tmp_path, current_wheel=False)
    result = run_install(project, app, commands, env)
    assert result.returncode == 2
    assert 'Missing current wheel:' in result.stderr
    assert not log.exists() and not app.exists() and not commands.exists()


@pytest.mark.parametrize('command', ['hamr', 'audiobook'])
def test_conflicting_command_prevents_environment_mutation(tmp_path, command):
    project, app, commands, log, env = installer_project(tmp_path)
    commands.mkdir()
    sentinel = commands / command
    sentinel.write_text('unrelated user command\n')
    result = run_install(project, app, commands, env)
    assert result.returncode == 2
    assert sentinel.read_text() == 'unrelated user command\n'
    assert not log.exists() and not app.exists()


def test_lock_export_failure_precedes_environment_changes(tmp_path):
    project, app, commands, log, env = installer_project(tmp_path)
    env['INSTALL_TEST_EXPORT_FAIL'] = '1'

    result = run_install(project, app, commands, env)

    assert result.returncode == 1
    assert [json.loads(line)[0] for line in log.read_text().splitlines()] == ['export']
    assert not app.exists() and not commands.exists()


def test_upgrade_retains_owned_legacy_link_and_is_repeatable(tmp_path):
    project, app, commands, log, env = installer_project(tmp_path)
    commands.mkdir()
    legacy = commands / 'audiobook'
    legacy.symlink_to(app / 'venv/bin/audiobook')
    target = os.readlink(legacy)
    for _ in range(2):
        result = run_install(project, app, commands, env)
        assert result.returncode == 0, result.stderr
        assert os.readlink(legacy) == target
        assert (commands / 'hamr').resolve() == app / 'venv/bin/hamr'


def test_existing_manual_is_preserved_before_environment_mutation(tmp_path):
    project, app, commands, log, env = installer_project(tmp_path)
    root = tmp_path / 'custom manual'
    env['HAMR_MAN_DIR'] = str(root)
    page = root / 'man1/hamr.1'
    page.parent.mkdir(parents=True)
    page.write_text('unrelated manual\n')
    result = run_install(project, app, commands, env)
    assert result.returncode == 2 and 'Existing manual' in result.stderr
    assert page.read_text() == 'unrelated manual\n'
    assert not log.exists() and not app.exists()
