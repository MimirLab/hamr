"""Describe this CLI and the user's installed command."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from . import __version__


def inspect():
    windows = os.name == 'nt'
    if windows:
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local')
        installed = base / 'hamr-app/venv/Scripts/hamr.exe'
        repair = 'From a current hamr checkout run uv build --wheel and .\\install.ps1 -PersistPath.'
    else:
        installed = Path.home() / '.local/bin/hamr'
        repair = 'From a current hamr checkout run uv build --wheel and ./install.sh.'
    found = shutil.which('hamr')
    issues = []
    version = None
    if installed.is_file() and os.access(installed, os.X_OK):
        try:
            result = subprocess.run([installed, '--version'], capture_output=True, text=True,
                                    check=True, timeout=8)
            version = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            issues.append({'code': 'installed_command_failed', 'action':
                           f'{repair} Checked {installed}.'})
        if version and version != __version__:
            issues.append({'code': 'installed_version_differs', 'action':
                           f'{installed} reports {version}, and this command is {__version__}. {repair}'})
        if not found or Path(found).resolve() != installed.resolve():
            action = (f'{repair} You can also invoke {installed} directly.' if windows else
                      f'Run export PATH={shlex.quote(str(installed.parent))}:"$PATH", then hash -r in Bash, or invoke {shlex.quote(str(installed))}.')
            issues.append({'code': 'installed_command_shadowed', 'action': action})
    elif not found:
        issues.append({'code': 'command_not_on_path', 'action':
                       repair if windows else repair + ' Then export PATH="$HOME/.local/bin:$PATH".'})
    manual = None
    if shutil.which('man'):
        try:
            result = subprocess.run(['man', '-w', '1', 'hamr'], capture_output=True, text=True, timeout=5)
            manual = result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            pass
    return {'version': __version__, 'interpreter': sys.executable, 'invoked_as': sys.argv[0],
            'path_command': found, 'installed_command': str(installed) if installed.is_file() else None,
            'installed_version': version, 'manual': manual}, issues
