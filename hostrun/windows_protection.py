"""Current-user Windows DPAPI integrity protection; never enables machine scope."""
import ctypes
from ctypes import wintypes as w


class Blob(ctypes.Structure):
    _fields_ = [('size', w.DWORD), ('data', ctypes.c_void_p)]


def protect(payload, entropy, *, decrypt=False):
    data_buffer = ctypes.create_string_buffer(payload)
    entropy_buffer = ctypes.create_string_buffer(entropy)
    source = Blob(len(payload), ctypes.addressof(data_buffer))
    salt = Blob(len(entropy), ctypes.addressof(entropy_buffer))
    result = Blob()
    api = ctypes.WinDLL('crypt32', use_last_error=True)
    function = api.CryptUnprotectData if decrypt else api.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob),
                         ctypes.c_void_p, ctypes.c_void_p, w.DWORD, ctypes.POINTER(Blob)]
    function.restype = w.BOOL
    if not function(ctypes.byref(source), None, ctypes.byref(salt), None, None, 1, ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel.LocalFree(result.data)
