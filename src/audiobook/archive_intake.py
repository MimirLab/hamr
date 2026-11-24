"""Bounded reading of archive members without extracting a filesystem tree."""
from contextlib import contextmanager
import ctypes
from ctypes.util import find_library
from dataclasses import dataclass
import io
import os
import stat
import tarfile
from zipfile import BadZipFile, ZipFile

from .core import Error

READ_BLOCK = 64*1024


@dataclass(frozen=True)
class Member:
    index: int
    name: str
    size: int | None


class ZipMembers(ZipFile):
    def members(self):
        from .source_bundle import zip_members

        for index,item in zip_members(self):
            yield Member(index,item.filename,item.file_size)

    def open_member(self,item):
        return self.open(self.infolist()[item.index-1])


class TarMembers:
    def __init__(self,archive):
        self.archive = archive
        self._items = []

    def members(self):
        from .source_bundle import safe_name

        for index,item in enumerate(self.archive,1):
            self._items.append(item)
            if not item.isdir():
                if not item.isfile():
                    raise Error('unsafe_source_member', f'The archive contains a non-file member: {item.name!r}.')
                name = item.name.removeprefix('./')
                yield Member(index,safe_name(name),item.size)

    def open_member(self,item):
        return self.archive.extractfile(self._items[item.index-1])


class ArchiveLibrary:
    def __init__(self):
        from ctypes import c_int, c_size_t, c_ssize_t, c_void_p, c_char_p, c_int64, c_wchar_p

        name = find_library('archive')
        if os.name=='nt' and not name:
            name = 'archive.dll'
        try:
            lib = ctypes.CDLL(name) if name else None
            if lib is None:
                raise OSError
        except OSError as exc:
            raise Error('archive_reader_missing','This archive needs a compatible local libarchive.',
                        'Install libarchive or export the same book to ZIP and repeat.') from exc
        signatures = {
            'archive_read_new':(c_void_p,()),
            'archive_read_support_filter_all':(c_int,(c_void_p,)),
            'archive_read_support_format_7zip':(c_int,(c_void_p,)),
            'archive_read_support_format_rar':(c_int,(c_void_p,)),
            'archive_read_support_format_rar5':(c_int,(c_void_p,)),
            'archive_read_open_filename':(c_int,(c_void_p,c_char_p,c_size_t)),
            'archive_read_next_header':(c_int,(c_void_p,ctypes.POINTER(c_void_p))),
            'archive_read_data':(c_ssize_t,(c_void_p,c_void_p,c_size_t)),
            'archive_read_free':(c_int,(c_void_p,)),
            'archive_error_string':(c_char_p,(c_void_p,)),
            'archive_entry_pathname_utf8':(c_char_p,(c_void_p,)),
            'archive_entry_pathname':(c_char_p,(c_void_p,)),
            'archive_entry_filetype':(c_int,(c_void_p,)),
            'archive_entry_symlink':(c_char_p,(c_void_p,)),
            'archive_entry_hardlink':(c_char_p,(c_void_p,)),
            'archive_entry_is_encrypted':(c_int,(c_void_p,)),
            'archive_entry_size_is_set':(c_int,(c_void_p,)),
            'archive_entry_size':(c_int64,(c_void_p,)),
        }
        if os.name=='nt':
            signatures['archive_read_open_filename_w'] = (c_int,(c_void_p,c_wchar_p,c_size_t))
        try:
            for name,(result,args) in signatures.items():
                func = getattr(lib,name)
                func.restype, func.argtypes = result,args
        except AttributeError as exc:
            raise Error('archive_reader_unavailable','The local libarchive lacks this required streaming interface.') from exc
        self.lib = lib

    def check(self,handle,code):
        if code<0:
            reason = self.lib.archive_error_string(handle) or b'Archive reader failed.'
            raise Error('unreadable_archive', reason.decode('utf-8','replace'))

    @contextmanager
    def open(self, path):
        lib = self.lib
        handle = lib.archive_read_new()
        if not handle:
            raise Error('archive_memory','Could not allocate archive reader.')
        try:
            lib.archive_read_support_filter_all(handle)
            for format in ('7zip','rar','rar5'):
                getattr(lib,'archive_read_support_format_'+format)(handle)
            result = (lib.archive_read_open_filename_w(handle,str(path),READ_BLOCK) if os.name=='nt'
                      else lib.archive_read_open_filename(handle,os.fsencode(path),READ_BLOCK))
            self.check(handle,result)
            yield handle
        finally:
            lib.archive_read_free(handle)

    def headers(self,handle):
        from .source_bundle import safe_name

        entry = ctypes.c_void_p()
        for index in range(1, 1 << 63):
            status = self.lib.archive_read_next_header(handle,ctypes.byref(entry))
            if status==1:
                return
            self.check(handle,status)
            raw = self.lib.archive_entry_pathname_utf8(entry) or self.lib.archive_entry_pathname(entry)
            if not raw:
                raise Error('unreadable_archive','An archive member has no readable name.')
            try:
                name = raw.decode('utf-8')
            except UnicodeDecodeError:
                name = raw.decode('cp437')
            kind = self.lib.archive_entry_filetype(entry)
            if stat.S_ISDIR(kind):
                continue
            if (not stat.S_ISREG(kind) or self.lib.archive_entry_symlink(entry)
                    or self.lib.archive_entry_hardlink(entry)):
                raise Error('unsafe_source_member', f'The archive contains a link or non-file member: {name!r}.')
            if self.lib.archive_entry_is_encrypted(entry)>0:
                raise Error('protected_source', f'An archive member is encrypted: {name}.',
                            'Provide an unencrypted local export.')
            size = self.lib.archive_entry_size(entry) if self.lib.archive_entry_size_is_set(entry) else None
            yield Member(index,safe_name(name.replace(chr(92),'/')),size)


class ArchiveStream(io.RawIOBase):
    def __init__(self,library,handle):
        self.library,self.handle = library,handle

    def readable(self):
        return True

    def readinto(self,target):
        length = len(target)
        buffer = (ctypes.c_char*length).from_buffer(target)
        count = self.library.lib.archive_read_data(self.handle,buffer,length)
        self.library.check(self.handle,count)
        return count


class LibraryMembers:
    def __init__(self,path):
        self.path = path
        self.reader = ArchiveLibrary()

    def members(self):
        with self.reader.open(self.path) as handle:
            yield from self.reader.headers(handle)

    @contextmanager
    def open_member(self,item):
        with self.reader.open(self.path) as handle:
            if item not in self.reader.headers(handle):
                raise Error('source_member_missing', f'Archive entry {item.index} is missing.')
            with io.BufferedReader(ArchiveStream(self.reader,handle)) as stream:
                yield stream


@contextmanager
def containers(path,kind):
    try:
        if kind.startswith('tar'):
            with tarfile.open(path,'r:*') as archive:
                yield TarMembers(archive)
        elif kind.startswith('libarchive'):
            yield LibraryMembers(path)
        else:
            with ZipMembers(path) as archive:
                yield archive
    except (BadZipFile, tarfile.TarError, NotImplementedError) as exc:
        raise Error('unreadable_archive', f'Cannot read archive {path.name}: {exc}') from exc
