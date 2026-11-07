"""Windows desktop process boundary. Install before starting any worker."""
import ctypes
from ctypes import wintypes as w
import os
import subprocess

_job = None
_original_init = subprocess.Popen.__init__


class GuiProcess(subprocess.Popen):
    """Visible GUI child in the same job, without a console or SW_HIDE."""
    def __init__(self, *args, **kwargs):
        if os.name == 'nt':
            kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
        _original_init(self, *args, **kwargs)


def install():
    global _job
    if os.name != 'nt' or _job is not None:
        return
    class Limits(ctypes.Structure):
        _fields_ = [('ProcessTime', ctypes.c_longlong), ('JobTime', ctypes.c_longlong),
                    ('Flags', w.DWORD), ('MinWorkingSet', ctypes.c_size_t),
                    ('MaxWorkingSet', ctypes.c_size_t), ('ActiveProcesses', w.DWORD),
                    ('Affinity', ctypes.c_size_t), ('Priority', w.DWORD), ('Scheduling', w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in
                    ('ReadOps', 'WriteOps', 'OtherOps', 'ReadBytes', 'WriteBytes', 'OtherBytes')]
    class Extended(ctypes.Structure):
        _fields_ = [('Basic', Limits), ('IO', IO), ('ProcessMemory', ctypes.c_size_t),
                    ('JobMemory', ctypes.c_size_t), ('PeakProcess', ctypes.c_size_t),
                    ('PeakJob', ctypes.c_size_t)]
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    api.CreateJobObjectW.restype = w.HANDLE
    api.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    api.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    api.GetCurrentProcess.restype = w.HANDLE
    api.CloseHandle.argtypes = [w.HANDLE]
    handle = api.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Extended()
    limits.Basic.Flags = 0x2000  # KILL_ON_JOB_CLOSE; no breakaway allowed.
    if not api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(handle, api.GetCurrentProcess()):
        error = ctypes.get_last_error()
        api.CloseHandle(handle)
        raise ctypes.WinError(error)
    # Non-inheritable handle lives until this GUI process exits. Windows then
    # closes it and kills every descendant, even on crash / forced termination.
    _job = handle
    os.environ['FRONTIER_DESKTOP'] = '1'
    hide_children()


def hide_children():
    if os.name == 'nt':
        subprocess.Popen.__init__ = _hidden_init


def _hidden_init(self, *args, **kwargs):
    flags = kwargs.get('creationflags', 0)
    flags &= ~(subprocess.CREATE_NEW_CONSOLE | subprocess.CREATE_BREAKAWAY_FROM_JOB)
    kwargs['creationflags'] = flags | subprocess.CREATE_NO_WINDOW
    startup = kwargs.get('startupinfo') or subprocess.STARTUPINFO()
    startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    kwargs['startupinfo'] = startup
    return _original_init(self, *args, **kwargs)
