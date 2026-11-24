#!/usr/bin/env python3
"""Assemble the resident XPS renderer from checked release inputs."""

from hashlib import file_digest, sha256
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
import tarfile


LOCK = Path(__file__).with_name('intake_xps_runtime.json')
LAUNCHER = b'''#!/bin/sh
export LD_LIBRARY_PATH=/reader/lib
export GIO_USE_VFS=local
export GIO_MODULE_DIR=/reader/lib
exec /reader/xpstopng "$@"
'''


def checked(path, expected):
    with path.open('rb') as data:
        actual = file_digest(data,'sha256').hexdigest()
    if actual!=expected:
        raise SystemExit(f'Unexpected checksum for {path.name}')


def package_files(path):
    members = subprocess.check_output(['ar','t',path],text=True).splitlines()
    payload, = [name for name in members if name.startswith('data.tar.')]
    return tarfile.open(fileobj=BytesIO(subprocess.check_output(['ar','p',path,payload])))


def write(archive, name, contents, *, executable=False):
    entry = tarfile.TarInfo(name)
    entry.mode = 0o755 if executable else 0o644
    entry.size = len(contents)
    entry.mtime = 0
    archive.addfile(entry,BytesIO(contents))


def main(debs, archive_library, archive_license, output):
    manifest = json.loads(LOCK.read_text())
    library = manifest['libarchive']
    checked(archive_library,library['built_sha256'])
    checked(archive_license,library['license_sha256'])
    files = {
        'run':LAUNCHER,
        'lib/libarchive.so.13':archive_library.read_bytes(),
        'notices/libarchive':archive_license.read_bytes(),
        'notices/source-packages.json':LOCK.read_bytes(),
    }
    for package in manifest['packages']:
        path = debs/Path(package['filename']).name
        checked(path,package['sha256'])
        with package_files(path) as archive:
            for destination,member in package['members'].items():
                data = archive.extractfile(member)
                files[destination] = data.read()
    with tarfile.open(output,'x:xz',preset=6,format=tarfile.USTAR_FORMAT) as archive:
        for name,contents in sorted(files.items()):
            write(archive,name,contents,executable=name in ('run','xpstopng'))
    print(f'{sha256(output.read_bytes()).hexdigest()}  {output.name}')


if __name__=='__main__':
    if len(sys.argv)!=5:
        raise SystemExit('Usage: build_intake_xps.py DEBS LIBARCHIVE_SO LIBARCHIVE_COPYING OUTPUT.tar.xz')
    main(*(Path(value) for value in sys.argv[1:]))
