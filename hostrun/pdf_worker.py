"""Bounded PDF extraction child; never returns executable PDF content."""
import json
import sys
from pathlib import Path


def main():
    if sys.platform == 'win32':
        # A nested non-breakaway job limits this parser without changing the GUI job.
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time',ctypes.c_longlong),('job_time',ctypes.c_longlong),('flags',w.DWORD),('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic',Basic),('io',ctypes.c_ulonglong*6),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
        api=ctypes.WinDLL('kernel32',use_last_error=True)
        api.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]
        api.CreateJobObjectW.restype=w.HANDLE
        api.GetCurrentProcess.restype=w.HANDLE
        api.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        api.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        handle=api.CreateJobObjectW(None,None)
        limits=Extended()
        limits.basic.flags=0x100 | 0x2
        limits.basic.process_time=20*10_000_000
        limits.process_memory=512*1024*1024
        if not handle or not api.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not api.AssignProcessToJobObject(handle,api.GetCurrentProcess()):
            raise ctypes.WinError(ctypes.get_last_error())
    else:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    from pypdf import PdfReader
    result = {'pages': [], 'reason': 'damaged_or_unsupported'}
    try:
        with Path(sys.argv[1]).open('rb') as stream:
            reader = PdfReader(stream, strict=True)
            if reader.is_encrypted:
                result['reason'] = 'encrypted'
            elif len(reader.pages) > 2000:
                result['reason'] = 'page_limit'
            else:
                total = 0
                pages = []
                for page in reader.pages:
                    text = page.extract_text() or ''
                    total += len(text.encode('utf8'))
                    if total > 4 * 1024 * 1024:
                        raise ValueError('text_limit')
                    pages.append(text)
                result = {'pages': pages, 'reason': 'extraction_quality_unverified' if pages and all(text.strip() for text in pages) else 'scan_or_empty_page'}
    except Exception:
        pass
    Path(sys.argv[2]).write_text(json.dumps(result), encoding='utf8')


if __name__ == '__main__':
    main()
