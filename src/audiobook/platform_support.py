"""Host-specific process ownership and atomic file promotion."""
import ctypes
import errno
import os
from pathlib import Path
import signal
import subprocess
import sys


def windows_api(name, result, *arguments):
    fn = getattr(ctypes.WinDLL('kernel32', use_last_error=True), name)
    fn.restype = result
    fn.argtypes = arguments
    return fn


def windows_memory():
    from ctypes import wintypes as W

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', W.DWORD), ('load', W.DWORD)] + [
            (name, ctypes.c_uint64) for name in
            ('total', 'available', 'page_total', 'page_available', 'virtual_total', 'virtual_available', 'extended')
        ]

    info = MemoryStatus()
    info.length = ctypes.sizeof(info)
    ok = windows_api('GlobalMemoryStatusEx', W.BOOL, ctypes.POINTER(MemoryStatus))(ctypes.byref(info))
    avx2 = bool(windows_api('IsProcessorFeaturePresent', W.BOOL, W.DWORD)(40))
    return (info.total, info.available, avx2) if ok else (None, None, avx2)


def windows_move(src, dst, *, replace):
    from ctypes import wintypes as W
    move = windows_api('MoveFileExW', W.BOOL, W.LPCWSTR, W.LPCWSTR, W.DWORD)
    if not move(os.fspath(src), os.fspath(dst), 8 | int(replace)):
        code = ctypes.get_last_error()
        if code in (80, 183) or (code == 5 and not replace and Path(dst).exists()):
            raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), os.fspath(dst))
        raise ctypes.WinError(code)


def windows_lock(fd, wait):
    from ctypes import wintypes as W
    from msvcrt import get_osfhandle

    class Overlapped(ctypes.Structure):
        _fields_ = [('internal', ctypes.c_size_t), ('internal_high', ctypes.c_size_t),
                    ('offset_low', W.DWORD), ('offset_high', W.DWORD), ('event', W.HANDLE)]

    flags = 2 | int(not wait)  # Exclusive, optionally fail immediately.
    reserve = windows_api('LockFileEx', W.BOOL, W.HANDLE, W.DWORD, W.DWORD, W.DWORD, W.DWORD,
                          ctypes.POINTER(Overlapped))
    if not reserve(get_osfhandle(fd), flags, 0, 1, 0, ctypes.byref(Overlapped())):
        code = ctypes.get_last_error()
        if code == 33:  # ERROR_LOCK_VIOLATION
            raise BlockingIOError(errno.EAGAIN, 'Writer lock is held.')
        raise ctypes.WinError(code)


def windows_job(process):
    from ctypes import wintypes as W

    class Limits(ctypes.Structure):
        _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
                    ('flags', W.DWORD), ('min_working_set', ctypes.c_size_t),
                    ('max_working_set', ctypes.c_size_t), ('process_count', W.DWORD),
                    ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [('limits', Limits), ('io', ctypes.c_uint64 * 6),
                    ('memory', ctypes.c_size_t * 4)]

    create = windows_api('CreateJobObjectW', W.HANDLE, ctypes.c_void_p, W.LPCWSTR)
    set_info = windows_api('SetInformationJobObject', W.BOOL, W.HANDLE, W.INT, ctypes.c_void_p, W.DWORD)
    assign = windows_api('AssignProcessToJobObject', W.BOOL, W.HANDLE, W.HANDLE)
    handle = create(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    info = Extended()
    info.limits.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not set_info(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not assign(handle, int(process._handle)):
        code = ctypes.get_last_error()
        close_windows_job(handle)
        raise ctypes.WinError(code)
    return handle


def close_windows_job(handle):
    from ctypes import wintypes as W
    windows_api('CloseHandle', W.BOOL, W.HANDLE)(handle)


def detached_flags():
    if os.name == 'nt':
        return {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
    return {'start_new_session': True}


def spawn_owned(args, *, parent_death=False, **kwargs):
    """Start a worker and return its Windows containment handle, if applicable."""
    if os.name == 'nt':
        process = subprocess.Popen(args, creationflags=subprocess.CREATE_NO_WINDOW, **kwargs)
        try:
            return process, windows_job(process)
        except BaseException:
            process.kill()
            process.wait()
            raise
    options = {'start_new_session': True}
    if parent_death and sys.platform == 'linux':
        parent = os.getpid()

        def set_parent_death():
            if ctypes.CDLL(None).prctl(1, signal.SIGTERM) or os.getppid() != parent:
                os._exit(1)

        options['preexec_fn'] = set_parent_death
    return subprocess.Popen(args, **options, **kwargs), None
