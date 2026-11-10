"""Fault injection at the real Windows registration publication boundary."""
import ctypes
from ctypes import wintypes as w
from pathlib import Path
import re


def handle_path(api, handle):
    api.GetFinalPathNameByHandleW.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
    api.GetFinalPathNameByHandleW.restype = w.DWORD
    buffer = ctypes.create_unicode_buffer(32768)
    length = api.GetFinalPathNameByHandleW(handle, buffer, len(buffer), 0)
    if not length or length >= len(buffer):
        raise ctypes.WinError(ctypes.get_last_error())
    return Path(buffer.value.removeprefix('\\\\?\\'))


def is_registration_write(api, handle):
    return re.fullmatch(r'[0-9a-f]{32}(?:\.owner)?\.json\.[0-9a-f]{32}\.tmp', handle_path(api, handle).name) is not None


def intercept_registration_publish(callback, monkeypatch=None, *, after=False):
    native = ctypes.WinDLL
    api = native('kernel32', use_last_error=True)
    class Rename(ctypes.Structure):
        _fields_ = [('Flags', w.DWORD), ('RootDirectory', w.HANDLE),
                    ('FileNameLength', w.DWORD), ('FileName', w.WCHAR * 1)]
    def set_info(handle, kind, buffer, size):
        api.SetFileInformationByHandle.argtypes = set_info.argtypes
        if kind == 22:
            info = ctypes.cast(buffer, ctypes.POINTER(Rename)).contents
            destination = ctypes.wstring_at(ctypes.addressof(info) + Rename.FileName.offset,
                                             info.FileNameLength // 2)
            source, target = handle_path(api, handle), Path(destination.removeprefix('\\\\?\\'))
            if not after:
                callback(source, target)
        result = api.SetFileInformationByHandle(handle, kind, buffer, size)
        if kind == 22 and result and after:
            callback(source, target)
        return result
    class Kernel:
        def __getattr__(self, name):
            return set_info if name == 'SetFileInformationByHandle' else getattr(api, name)
    def dll(name, **kwargs):
        return Kernel() if name == 'kernel32' else native(name, **kwargs)
    if monkeypatch is None:
        ctypes.WinDLL = dll
    else:
        monkeypatch.setattr(ctypes, 'WinDLL', dll)
