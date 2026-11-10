"""Public managed operations with native registration-reader sharing boundaries."""
import ctypes
import io
from pathlib import Path
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from hostrun import cleanup
from hostrun.embedded_agent import configure


@pytest.fixture
def instance(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = cleanup.control_directory()
    control.mkdir(parents=True)
    instance = cleanup.prepare_instance(control)
    cleanup.initialize_layout(control, instance['instance_id'])
    return control, instance


def test_preview_open_reader_does_not_block_public_configuration_receipt(instance, monkeypatch):
    control, instance = instance
    entered, release = threading.Event(), threading.Event()
    reader_thread = []
    original_open = io.open
    original_dll = ctypes.WinDLL
    kernel = original_dll('kernel32', use_last_error=True)
    native_read = kernel.ReadFile
    def pause():
        if reader_thread and threading.get_ident() == reader_thread[0] and not entered.is_set():
            entered.set()
            assert release.wait(10)
    class Reader:
        def __init__(self, stream): self.stream = stream
        def __enter__(self): return self
        def __exit__(self, *args): return self.stream.__exit__(*args)
        def read(self, *args, **kwargs):
            pause()
            return self.stream.read(*args, **kwargs)
    def opened(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        return Reader(stream) if Path(path) == instance['registration'] and threading.get_ident() in reader_thread else stream
    def read(handle, *args):
        native_read.argtypes = read.argtypes
        native_read.restype = read.restype
        pause()
        return native_read(handle, *args)
    kernel.ReadFile = read
    monkeypatch.setattr(io, 'open', opened)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, **kwargs: kernel if name == 'kernel32' else original_dll(name, **kwargs))
    def preview():
        reader_thread.append(threading.get_ident())
        return cleanup.preview(control, instance['instance_id'])
    failure = None
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(preview)
        try:
            assert entered.wait(5)
            try:
                configure(instance['home'], provider='test', model='test', api_key='TEST-KEY')
            except OSError as error:
                import traceback
                failure = (type(error).__name__, error.winerror,
                           [frame.name for frame in traceback.extract_tb(error.__traceback__)])
        finally:
            release.set()
        old = future.result(timeout=5)
    assert failure is None, failure
    candidate = str(instance['home'] / 'agent/model.json')
    assert candidate not in old['registered_file_candidates']
    assert candidate in cleanup.preview(control, instance['instance_id'])['registered_file_candidates']


@pytest.mark.parametrize('damage', ['truncated', 'invalid_protection'])
def test_preview_refuses_corrupt_registration_snapshot(instance, damage):
    import base64, json
    control, instance = instance
    path = instance['registration']
    original = path.read_bytes()
    if damage == 'truncated':
        path.write_bytes(original[:len(original) // 2])
    else:
        path.write_text(json.dumps({'protected': base64.b64encode(b'not valid DPAPI').decode()}))
    with pytest.raises(cleanup.InstanceInvalid, match='Invalid protected registration'):
        cleanup.preview(control, instance['instance_id'])


def test_external_reader_without_delete_sharing_still_blocks_public_receipt(instance):
    from hostrun.embedded_agent import load_model_config
    from hostrun.windows_cleanup import PinnedEntry
    control, instance = instance
    configure(instance['home'], provider='test', model='before', api_key='RETAINED-KEY')
    registration = instance['registration']
    original = registration.read_bytes()
    pin = PinnedEntry(registration)
    try:
        with pytest.raises(OSError) as error:
            configure(instance['home'], provider='test', model='after', api_key='NEW-KEY')
        assert error.value.winerror in (5, 32)
        assert registration.read_bytes() == original
        assert load_model_config(instance['home'])['model'] == 'before'
    finally:
        pin.close()
    configure(instance['home'], provider='test', model='after', api_key='NEW-KEY')
    assert load_model_config(instance['home'])['model'] == 'after'


@pytest.mark.parametrize('phase', ['before', 'after'])
def test_registration_publication_hard_exit_leaves_complete_old_or_new_snapshot(tmp_path, monkeypatch, phase):
    import subprocess, sys
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = cleanup.control_directory()
    control.mkdir(parents=True)
    instance = cleanup.prepare_instance(control)
    old = instance['registration'].read_bytes()
    script = '''
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from native_registration import intercept_registration_publish
from hostrun.cleanup import initialize_layout
intercept_registration_publish(lambda source,target: os._exit(77),after=sys.argv[3]=='after')
initialize_layout(sys.argv[1],sys.argv[2])
'''
    process = subprocess.run([sys.executable, '-B', '-c', script, str(control), instance['instance_id'], phase], capture_output=True, timeout=15)
    assert process.returncode == 77, process.stderr
    observed = cleanup.preview(control, instance['instance_id'])
    if phase == 'before':
        assert instance['registration'].read_bytes() == old
        assert observed['owned_children'] == []
    else:
        assert observed['owned_children'] == ['app', 'archive', 'data']
        cleanup.initialize_layout(control, instance['instance_id'])
        assert instance['home'].is_dir()


@pytest.mark.parametrize('failure', ['readonly', 'unsupported'])
def test_registration_publication_refusal_retains_old_protected_record(instance, monkeypatch, failure):
    from ctypes import wintypes as w
    from native_registration import intercept_registration_publish
    control, instance = instance
    path = instance['registration']
    before = path.read_bytes()
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.SetFileAttributesW.argtypes = [w.LPCWSTR, w.DWORD]
    if failure == 'readonly':
        assert api.SetFileAttributesW(str(path), 1)
    else:
        def unsupported(source, target):
            raise ctypes.WinError(87)
        intercept_registration_publish(unsupported, monkeypatch)
    try:
        with pytest.raises(OSError) as error:
            configure(instance['home'], provider='test', model='new', api_key='TEST-KEY')
        assert error.value.winerror == (5 if failure == 'readonly' else 87)
        assert path.read_bytes() == before
        assert cleanup.preview(control, instance['instance_id'])['registered_file_candidates'] == []
    finally:
        if failure == 'readonly':
            assert api.SetFileAttributesW(str(path), 32)


def test_new_preview_reads_published_snapshot_before_publisher_handle_closes(instance, monkeypatch):
    from native_registration import intercept_registration_publish
    control, instance = instance
    observed = []
    def after_publish(source, target):
        if target == instance['registration']:
            observed.append(cleanup.preview(control, instance['instance_id'])['registered_file_candidates'])
    intercept_registration_publish(after_publish, monkeypatch, after=True)
    configure(instance['home'], provider='test', model='test', api_key='TEST-KEY')
    assert str(instance['home'] / 'agent/model.json') in observed[-1]
