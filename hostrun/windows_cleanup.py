"""Delete through pinned Windows handles, never a re-resolved pathname."""
import ctypes
import os
import sys
from ctypes import wintypes as w
from contextlib import ExitStack, contextmanager
from pathlib import Path


class PinnedEntry:
    def __init__(self, path, *, delete=False, create_parent=None, create_share=1, create_delete=True):
        self.path = Path(path)
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateFileW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.c_void_p,
                                        w.DWORD, w.DWORD, w.HANDLE]
        self.api.CreateFileW.restype = w.HANDLE
        self.api.CloseHandle.argtypes = [w.HANDLE]
        self.api.GetFileInformationByHandleEx.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        self.api.SetFileInformationByHandle.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        # No delete sharing: this entry cannot be renamed or replaced while pinned.
        if create_parent is not None:
            self.handle = _create_directory(create_parent.handle, self.path.name, create_share, create_delete)
        else:
            self.handle = self.api.CreateFileW(str(self.path), 0x81 | (0x10000 if delete else 0),
                                              3, None, 3, 0x02200000, None)
        if self.handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            attributes = (w.DWORD * 2)()
            if not self.api.GetFileInformationByHandleEx(self.handle, 9, attributes, ctypes.sizeof(attributes)):
                raise ctypes.WinError(ctypes.get_last_error())
            if attributes[0] & 0x400:
                raise ValueError('Reparse points are not owned deletion targets')
            self.directory = bool(attributes[0] & 0x10)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle is not None:
            self.api.CloseHandle(self.handle)
            self.handle = None

    def delete(self):
        disposition = ctypes.c_ubyte(1)
        if not self.api.SetFileInformationByHandle(self.handle, 4, ctypes.byref(disposition), 1):
            raise ctypes.WinError(ctypes.get_last_error())
        self.close()

    def rename(self, destination):
        value = str(destination)
        class RenameInfo(ctypes.Structure):
            _fields_ = [('ReplaceIfExists', w.BOOL), ('RootDirectory', w.HANDLE),
                        ('FileNameLength', w.DWORD), ('FileName', w.WCHAR * (len(value) + 1))]
        info = RenameInfo()
        info.FileNameLength = len(value.encode('utf-16-le'))
        info.FileName = value
        if not self.api.SetFileInformationByHandle(self.handle, 3, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())


def _create_directory(parent_handle, name, share, delete):
    """FILE_CREATE returns the newly created directory handle atomically."""
    if os.name != 'nt' or sys.getwindowsversion().major < 10:
        raise OSError('Standalone test cleanup requires Windows 10 or later')
    if not name or name in ('.', '..') or any(c in name for c in '/\\:'):
        raise ValueError('Directory name must be one component')
    class UnicodeString(ctypes.Structure):
        _fields_ = [('Length', w.USHORT), ('MaximumLength', w.USHORT), ('Buffer', w.LPWSTR)]
    class Attributes(ctypes.Structure):
        _fields_ = [('Length', w.ULONG), ('RootDirectory', w.HANDLE),
                    ('ObjectName', ctypes.POINTER(UnicodeString)), ('Attributes', w.ULONG),
                    ('SecurityDescriptor', ctypes.c_void_p), ('SecurityQualityOfService', ctypes.c_void_p)]
    class IoStatus(ctypes.Structure):
        _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]
    text = ctypes.create_unicode_buffer(name)
    text_size = len(name.encode('utf-16-le'))
    string = UnicodeString(text_size, text_size + 2, ctypes.cast(text, w.LPWSTR))
    attrs = Attributes(ctypes.sizeof(Attributes), parent_handle, ctypes.pointer(string), 0x40, None, None)
    io = IoStatus()
    handle = w.HANDLE()
    api = ctypes.WinDLL('ntdll')
    api.NtCreateFile.argtypes = [ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.POINTER(Attributes),
        ctypes.POINTER(IoStatus), ctypes.c_void_p, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.c_void_p, w.DWORD]
    api.NtCreateFile.restype = w.LONG
    access = 0x100081 | (0x10000 if delete else 0)
    status = api.NtCreateFile(ctypes.byref(handle), access, ctypes.byref(attrs), ctypes.byref(io),
                              None, 0x10, share, 2, 0x21, None, 0)
    if status < 0:
        api.RtlNtStatusToDosError.argtypes = [w.LONG]
        api.RtlNtStatusToDosError.restype = w.ULONG
        raise ctypes.WinError(api.RtlNtStatusToDosError(status))
    return handle.value


@contextmanager
def pin_ancestors(root, *, already_pinned=()):
    with ExitStack() as stack:
        for directory in reversed([Path(root), *Path(root).parents]):
            if directory in already_pinned:
                continue
            pin = PinnedEntry(directory)
            stack.callback(pin.close)
        yield


def remove_owned_files(root, relative_paths, validate, *, removed=None, identities=None):
    """Pin every ancestor before validation; preserve unknown directory contents."""
    root = Path(root)
    if removed is None:
        removed = []
    with ExitStack() as stack:
        pins = {}
        # Pin from the volume downward so replacing an ancestor cannot redirect access.
        for directory in reversed([root, *root.parents]):
            pin = PinnedEntry(directory)
            stack.callback(pin.close)
            pins[directory] = pin
        validate()
        for relative in relative_paths:
            target = root / relative
            if not target.is_relative_to(root) or '..' in Path(relative).parts:
                raise ValueError('Invalid relative deletion target')
            for directory in [*reversed(list(target.parent.parents)), target.parent]:
                if directory.is_relative_to(root) and directory not in pins:
                    pin = PinnedEntry(directory)
                    stack.callback(pin.close)
                    pins[directory] = pin
            with ExitStack() as entry_stack:
                entry = PinnedEntry(target, delete=True)
                entry_stack.callback(entry.close)
                if entry.directory:
                    raise ValueError('Only inventoried files may be deleted')
                if identities and relative in identities:
                    stat = target.stat()
                    if identities[relative] != [stat.st_dev, stat.st_ino]:
                        raise ValueError('Inventoried file identity changed')
                validate()
                entry.delete()
                removed.append(relative)
    return removed


def remove_empty_children(root, names, validate, removed):
    root = Path(root)
    with pin_ancestors(root):
        validate()
        for name in names:
            if name not in ('app', 'data', 'archive'):
                raise ValueError('Unknown owned directory')
            path = root / name
            if not path.exists():
                continue
            with ExitStack() as held:
                pin = PinnedEntry(path, delete=True)
                held.callback(pin.close)
                validate()
                if pin.directory and not any(path.iterdir()):
                    pin.delete()
                    removed.append(name + '/')


def remove_empty_inventory(root, inventory, validate, removed):
    root = Path(root)
    for relative in sorted(inventory, key=lambda name: len(Path(name).parts), reverse=True):
        path = root / relative
        if not relative.startswith('app/current') or '..' in Path(relative).parts or ':' in relative:
            raise ValueError('Invalid directory inventory')
        if not path.exists():
            continue
        with pin_ancestors(path.parent), ExitStack() as held:
            pin = PinnedEntry(path, delete=True)
            held.callback(pin.close)
            validate()
            stat = path.stat()
            if inventory[relative] != [stat.st_dev, stat.st_ino]:
                raise ValueError('Directory inventory identity changed')
            if pin.directory and not any(path.iterdir()):
                pin.delete()
                removed.append(relative + '/')
