"""Exact Windows process handles for stopping an enrolled desktop owner."""
import ctypes
from ctypes import wintypes as w
from contextlib import ExitStack
import hashlib
import os
import time

QUERY = 0x1000
SYNCHRONIZE = 0x100000
TERMINATE = 1


def _kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
    api.OpenProcess.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    api.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
    api.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
    api.TerminateProcess.argtypes = [w.HANDLE, w.UINT]
    api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
    api.WaitForSingleObject.restype = w.DWORD
    return api


def _identity(api, handle, pid):
    times = [w.FILETIME() for _ in range(4)]
    if not api.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    image = ctypes.create_unicode_buffer(32768)
    length = w.DWORD(len(image))
    if not api.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length)):
        raise ctypes.WinError(ctypes.get_last_error())
    return {'pid': pid, 'created': (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime,
            'image_path_digest': hashlib.sha256(os.path.normcase(image.value).encode('utf-8')).hexdigest()}


def current_identity():
    api = _kernel()
    pid = os.getpid()
    handle = api.OpenProcess(QUERY | SYNCHRONIZE, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _identity(api, handle, pid)
    finally:
        api.CloseHandle(handle)


def _parents(api):
    class Entry(ctypes.Structure):
        _fields_ = [('size', w.DWORD), ('usage', w.DWORD), ('pid', w.DWORD),
                    ('heap', ctypes.c_size_t), ('module', w.DWORD), ('threads', w.DWORD),
                    ('parent', w.DWORD), ('priority', w.LONG), ('flags', w.DWORD), ('image', w.WCHAR * 260)]
    api.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    api.CreateToolhelp32Snapshot.restype = w.HANDLE
    api.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [w.HANDLE, ctypes.POINTER(Entry)]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = {}
    try:
        entry = Entry()
        entry.size = ctypes.sizeof(entry)
        found = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while found:
            result[entry.pid] = entry.parent
            found = api.Process32NextW(snapshot, ctypes.byref(entry))
        if ctypes.get_last_error() != 18:
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        api.CloseHandle(snapshot)
    return result


def stop_owner(expected):
    """Terminate only an identity-matched owner; its existing Job kills descendants."""
    api = _kernel()
    with ExitStack() as held:
        owner = api.OpenProcess(QUERY | SYNCHRONIZE | TERMINATE, False, expected['pid'])
        if not owner:
            if ctypes.get_last_error() == 87:
                return {'status': 'already_exited'}
            raise ctypes.WinError(ctypes.get_last_error())
        held.callback(api.CloseHandle, owner)
        if _identity(api, owner, expected['pid']) != expected:
            return {'status': 'blocked', 'reason': 'owner_identity_mismatch'}
        if api.WaitForSingleObject(owner, 0) == 0:
            return {'status': 'already_exited'}
        members = {expected['pid']: (owner, expected['created'])}
        parents = _parents(api)
        for _ in range(len(parents)):
            changed = False
            for pid, parent in parents.items():
                if parent not in members or pid in members:
                    continue
                handle = api.OpenProcess(QUERY | SYNCHRONIZE, False, pid)
                if not handle:
                    if ctypes.get_last_error() == 87:
                        continue
                    raise ctypes.WinError(ctypes.get_last_error())
                held.callback(api.CloseHandle, handle)
                identity = _identity(api, handle, pid)
                if identity['created'] >= members[parent][1]:
                    members[pid] = (handle, identity['created'])
                    changed = True
            if not changed:
                break
        started = time.monotonic()
        if not api.TerminateProcess(owner, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        for handle, _ in members.values():
            remaining = max(0, int((5 - (time.monotonic() - started)) * 1000))
            if api.WaitForSingleObject(handle, remaining) != 0:
                return {'status': 'blocked', 'reason': 'process_exit_timeout'}
        return {'status': 'stopped', 'observed_processes': len(members),
                'elapsed_ms': int((time.monotonic() - started) * 1000)}
