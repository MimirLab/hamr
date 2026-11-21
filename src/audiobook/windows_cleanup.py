"""Remove app-owned paths without traversing Windows reparse points."""
import os
from pathlib import Path
import stat

from .core import Error


def ordinary(path):
    info = path.lstat()
    if info.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise Error('unsafe_path', 'Cleanup refused a Windows reparse point.')
    return info


def remove_owned(root, relative):
    parent = root
    ordinary(parent)
    for name in relative.parts[:-1]:
        parent /= name
        ordinary(parent)
    target = parent / relative.name

    def inspect(path):
        info = ordinary(path)
        if stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                inspect(child)
        elif not stat.S_ISREG(info.st_mode):
            raise Error('unsafe_path', 'Cleanup refused an unexpected file type.')
        elif info.st_nlink != 1 and not info.st_mode & stat.S_IWRITE:
            raise Error('unsafe_path', 'Cleanup refused a read-only file with multiple links.')

    inspect(target)

    def remove(path):
        info = ordinary(path)
        if stat.S_ISDIR(info.st_mode):
            for child in path.iterdir():
                remove(child)
            path.rmdir()
        else:
            if not info.st_mode & stat.S_IWRITE:
                if info.st_nlink != 1:
                    raise Error('unsafe_path', 'Cleanup refused a read-only file with multiple links.')
                path.chmod(stat.S_IREAD | stat.S_IWRITE)
            path.unlink()

    remove(target)
