"""Installable runtime selectors. A research entry alone cannot enable setup."""
from pathlib import PurePosixPath, PureWindowsPath
import platform
import shutil
import stat
import tarfile
import zipfile

from .core import Error


def runtime_key(system=None, arch=None):
    system = platform.system() if system is None else system
    arch = platform.machine() if arch is None else arch
    if arch.lower() not in ('amd64', 'x86_64'):
        return None
    return {'Linux': 'linux_x86_64', 'Windows': 'windows_x86_64'}.get(system)


def hoppou_archives(runtime, variant, profile):
    """Return qualified replacements and the original archive for this profile."""
    return [
        *(entry for entry in runtime.get('profile_archives',{}).get(variant,()) if profile in entry['profiles']),
        {'revision':runtime['revision'],**runtime[variant]},
    ]


def extract_hoppou(archive, target, variant):
    if variant == 'linux_x86_64':
        with tarfile.open(archive) as bundle:
            bundle.extractall(target, filter='data')
        return
    # The pinned Windows bundle contains fewer than 100 regular files.
    with zipfile.ZipFile(archive) as bundle:
        members = bundle.infolist()
        files = set()
        total = 0
        if len(members) > 1000:
            raise Error('runtime_layout', 'Runtime ZIP has too many entries.')
        for entry in members:
            name = entry.filename
            path = PurePosixPath(name)
            mode = entry.external_attr >> 16
            if (not path.parts or path.is_absolute() or '..' in path.parts
                    or any(ch in name for ch in '\\:<>"|?*') or stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)
                    or any(part.endswith(('.', ' ')) or PureWindowsPath(part).is_reserved() for part in path.parts)):
                raise Error('runtime_layout', f'Unsafe path in the runtime ZIP: {name!r}')
            canonical = '/'.join(path.parts).casefold()
            if canonical in files:
                raise Error('runtime_layout', 'Runtime ZIP contains duplicate paths.')
            files.add(canonical)
            total += entry.file_size
        if total > 512 * 1024**2:
            raise Error('runtime_layout', 'Runtime ZIP exceeds its extraction budget.')
        for entry in members:
            output = target.joinpath(*PurePosixPath(entry.filename).parts)
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
            else:
                output.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(entry) as source, output.open('xb') as destination:
                    shutil.copyfileobj(source, destination)
