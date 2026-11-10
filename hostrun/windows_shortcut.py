"""Serialize a native ShellLink to memory without creating a pathname."""
import ctypes
from ctypes import wintypes as w
import uuid


def shell_link_bytes(target, arguments, working_directory, description):
    ole = ctypes.WinDLL('ole32')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    pointer = ctypes.c_void_p
    def guid(value):
        return (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID(value).bytes_le)
    def check(result):
        if result < 0:
            raise OSError('ShellLink COM operation failed', result)
    def method(obj, index, argtypes, *args):
        table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(pointer))).contents
        call = ctypes.WINFUNCTYPE(ctypes.c_long, pointer, *argtypes)(table[index])
        check(call(obj, *args))
    def release(obj):
        if obj.value:
            table = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(pointer))).contents
            ctypes.WINFUNCTYPE(w.ULONG, pointer)(table[2])(obj)
    ole.CoInitializeEx.argtypes = [pointer, w.DWORD]
    ole.CoInitializeEx.restype = ctypes.c_long
    ole.CoUninitialize.argtypes = []
    ole.CoUninitialize.restype = None
    ole.CoCreateInstance.argtypes = [pointer, pointer, w.DWORD, pointer, ctypes.POINTER(pointer)]
    ole.CoCreateInstance.restype = ctypes.c_long
    ole.CreateStreamOnHGlobal.argtypes = [w.HGLOBAL, w.BOOL, ctypes.POINTER(pointer)]
    ole.CreateStreamOnHGlobal.restype = ctypes.c_long
    ole.GetHGlobalFromStream.argtypes = [pointer, ctypes.POINTER(w.HGLOBAL)]
    ole.GetHGlobalFromStream.restype = ctypes.c_long
    kernel.GlobalLock.argtypes = [w.HGLOBAL]
    kernel.GlobalLock.restype = pointer
    kernel.GlobalUnlock.argtypes = [w.HGLOBAL]
    # S_OK and S_FALSE both require CoUninitialize; an incompatible apartment fails here.
    check(ole.CoInitializeEx(None, 2))
    link, persistence, stream = pointer(), pointer(), pointer()
    try:
        check(ole.CoCreateInstance(guid('00021401-0000-0000-C000-000000000046'), None, 1,
                                   guid('000214F9-0000-0000-C000-000000000046'), ctypes.byref(link)))
        for index, value in ((20, str(target)), (11, arguments), (9, str(working_directory)), (7, description)):
            method(link, index, [w.LPCWSTR], value)
        method(link, 15, [ctypes.c_int], 1)
        method(link, 0, [pointer, ctypes.POINTER(pointer)],
               guid('00000109-0000-0000-C000-000000000046'), ctypes.byref(persistence))
        check(ole.CreateStreamOnHGlobal(None, True, ctypes.byref(stream)))
        # Direct Save writes the .lnk payload; OleSaveToStream would prepend an extra CLSID.
        method(persistence, 6, [pointer, w.BOOL], stream, False)
        length = ctypes.c_ulonglong()
        method(stream, 5, [ctypes.c_longlong, w.DWORD, ctypes.POINTER(ctypes.c_ulonglong)], 0, 1, ctypes.byref(length))
        if not 0 < length.value <= 1024 * 1024:
            raise ValueError('Unexpected ShellLink size')
        memory = w.HGLOBAL()
        check(ole.GetHGlobalFromStream(stream, ctypes.byref(memory)))
        address = kernel.GlobalLock(memory)
        if not address:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            return ctypes.string_at(address, length.value)
        finally:
            kernel.GlobalUnlock(memory)
    finally:
        release(stream)
        release(persistence)
        release(link)
        ole.CoUninitialize()
