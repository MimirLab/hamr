"""Describe this CLI and the user's installed command."""
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

from . import __version__


def inspect():
    installed = Path.home() / '.local/bin/hamr'
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
                           f'From a current hamr checkout run uv build --wheel, then ./install.sh. Checked {installed}.'})
        if version and version != __version__:
            issues.append({'code': 'installed_version_differs', 'action':
                           f'{installed} reports {version}; this command is {__version__}. From the desired checkout, run uv build --wheel, then ./install.sh.'})
        if not found or Path(found).resolve() != installed.resolve():
            issues.append({'code': 'installed_command_shadowed', 'action':
                           f'Run export PATH={shlex.quote(str(installed.parent))}:"$PATH", then hash -r in Bash, or invoke {shlex.quote(str(installed))}.'})
    elif not found:
        issues.append({'code': 'command_not_on_path', 'action':
                       'From the checkout, run uv build --wheel and ./install.sh, then export PATH="$HOME/.local/bin:$PATH".'})
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
