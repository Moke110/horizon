import json
from pathlib import Path
import pytest

from hostrun.cleanup import prepare_instance, initialize_layout, cleanup_instance, preview
from hostrun.update_kb import import_files
from hostrun import archive_index


def test_managed_text_import_is_incremental_and_owned(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = tmp_path / 'Horizon/test-instances'
    control.mkdir(parents=True)
    instance = prepare_instance(control)
    initialize_layout(control, instance['instance_id'])
    source = tmp_path / 'PRIVATE-SOURCE'
    source.mkdir()
    original = source / 'PRIVATE-NAME.txt'
    original.write_text('PRIVATE-BODY', encoding='utf-8')
    first = import_files(instance['home'], instance['archive'], source, upload=None)
    assert len(first['copied']) == 1
    copied = Path(first['copied'][0]['archive_path'])
    assert copied.read_bytes() == original.read_bytes()
    identity = copied.stat().st_ino
    second = import_files(instance['home'], instance['archive'], source, upload=None)
    assert len(second['skipped_registered']) == 1
    assert copied.stat().st_ino == identity
    assert len(archive_index.load(instance['home'])) == 1
    (source / 'PRIVATE-SECOND.txt').write_text('second body', encoding='utf-8')
    third = import_files(instance['home'], instance['archive'], source, upload=None)
    assert len(third['copied']) == 1
    assert len(archive_index.load(instance['home'])) == 2
    assert 'PRIVATE-' not in json.dumps(preview(control, instance['instance_id']))
    kept = cleanup_instance(control, instance['instance_id'], tmp_path / 'keep-report',
                            mode='keep-data', confirmation=instance['instance_id'])
    assert kept['status'] == 'uninstalled'
    assert copied.stat().st_ino == identity
    cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'full-report',
                               confirmation=instance['instance_id'])
    assert cleaned['status'] == 'cleaned'
    assert not copied.exists()
    assert original.read_text() == 'PRIVATE-BODY'
    assert 'PRIVATE-' not in json.dumps(cleaned)


@pytest.fixture
def material_instance(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = tmp_path / 'Horizon/test-instances'
    control.mkdir(parents=True)
    instance = prepare_instance(control)
    initialize_layout(control, instance['instance_id'])
    source = tmp_path / 'PRIVATE-SOURCE'
    source.mkdir()
    (source / 'PRIVATE-FIRST.txt').write_text('first')
    return instance, control, source


@pytest.mark.parametrize('kind', ['same_bytes', 'replacement', 'ledger_replacement', 'junction'])
def test_managed_import_refuses_unknown_and_redirected_entries(material_instance, tmp_path, kind):
    import subprocess
    from hostrun.update_kb import business_id_for
    instance, control, source = material_instance
    original = source / 'PRIVATE-FIRST.txt'
    target = instance['archive'] / 'originals' / business_id_for(original) / original.name
    if kind == 'same_bytes':
        target.parent.mkdir(parents=True)
        target.write_bytes(original.read_bytes())
    elif kind == 'junction':
        outside = tmp_path / 'outside'
        outside.mkdir()
        instance['archive'].rmdir()
        subprocess.run(['cmd', '/c', 'mklink', '/J', str(instance['archive']), str(outside)], check=True, capture_output=True)
    else:
        import_files(instance['home'], instance['archive'], source)
        changed = target if kind == 'replacement' else instance['home'] / 'archive-index/index.jsonl'
        before = changed.read_bytes()
        changed.rename(tmp_path / 'retained-original')
        changed.write_bytes(before)
    with pytest.raises((OSError, ValueError)):
        import_files(instance['home'], instance['archive'], source)
    if kind != 'junction': assert target.read_bytes() == original.read_bytes()
    else: assert not list(outside.iterdir())


def test_business_index_records_unknown_without_gaining_cleanup_ownership(material_instance, tmp_path):
    instance, control, source = material_instance
    import_files(instance['home'], instance['archive'], source)
    unknown = instance['archive'] / 'originals/PRIVATE-UNKNOWN.txt'
    unknown.write_text('PRIVATE-UNKNOWN-BODY')
    result = archive_index.register(instance['home'], instance['archive'])
    assert result['registered'] == 1
    assert len(archive_index.load(instance['home'])) == 2
    cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
    assert cleaned['status'] == 'partial'
    assert unknown.read_text() == 'PRIVATE-UNKNOWN-BODY'
    assert 'PRIVATE-' not in json.dumps(cleaned)


@pytest.mark.parametrize('phase', ['copy_before', 'copy_after', 'ledger_before', 'ledger_old_moved', 'ledger_new_published', 'ledger_replacement'])
def test_material_publication_hard_exit_recovers_via_public_import(material_instance, tmp_path, phase):
    import os
    import subprocess
    import sys
    instance, control, source = material_instance
    import_files(instance['home'], instance['archive'], source)
    original_ledger = (instance['home'] / 'archive-index/index.jsonl').read_bytes()
    (source / 'PRIVATE-SECOND.txt').write_text('second')
    script = r'''
import ctypes,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from native_registration import handle_path
from hostrun.update_kb import import_files
phase=sys.argv[4]; native=ctypes.WinDLL; api=native('kernel32',use_last_error=True)
def rename(handle,kind,buffer,size):
    api.SetFileInformationByHandle.argtypes=rename.argtypes
    path=handle_path(api,handle) if kind==3 else None
    copy=path is not None and 'originals' in path.parts
    old=path is not None and path.name=='index.jsonl'
    new=path is not None and path.parent.name=='archive-index' and path.name.startswith('.horizon-copy-')
    if (phase=='copy_before' and copy) or (phase=='ledger_before' and old): os._exit(77)
    result=api.SetFileInformationByHandle(handle,kind,buffer,size)
    if result and ((phase=='copy_after' and copy) or (phase=='ledger_old_moved' and old) or (phase=='ledger_new_published' and new)): os._exit(77)
    return result
class Kernel:
    def __getattr__(self,name): return rename if name=='SetFileInformationByHandle' else getattr(api,name)
ctypes.WinDLL=lambda name,**kwargs: Kernel() if name=='kernel32' else native(name,**kwargs)
import_files(sys.argv[1],sys.argv[2],sys.argv[3])
'''
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
                              str(instance['home']), str(instance['archive']), str(source), 'ledger_old_moved' if phase == 'ledger_replacement' else phase],
                             env=os.environ, capture_output=True, text=True, timeout=20)
    assert process.returncode == 77, process.stderr
    if phase == 'ledger_replacement':
        replacement = instance['home'] / 'archive-index/index.jsonl'
        replacement.write_bytes(original_ledger)
        with pytest.raises(ValueError):
            archive_index.load(instance['home'])
        cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
        assert cleaned['status'] == 'partial'
        assert replacement.read_bytes() == original_ledger
        assert 'PRIVATE-' not in json.dumps(cleaned)
        return
    if phase in ('copy_before', 'copy_after', 'ledger_before'):
        assert (instance['home'] / 'archive-index/index.jsonl').read_bytes() == original_ledger
    # Public readers recover the ledger before observing it; import resumes copies.
    rows = archive_index.load(instance['home'])
    assert len(rows) == (2 if phase == 'ledger_new_published' else 1)
    resumed = import_files(instance['home'], instance['archive'], source)
    assert not resumed['conflicts']
    assert len(archive_index.load(instance['home'])) == 2
    cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
    assert cleaned['status'] == 'cleaned'
    assert 'PRIVATE-' not in json.dumps(cleaned)


@pytest.mark.parametrize('phase', ['pre_receipt', 'post_receipt', 'partial_body', 'full_body'])
def test_staging_residues_never_become_business_ledger_material(material_instance, tmp_path, phase):
    import os
    import subprocess
    import sys
    instance, control, source = material_instance
    import_files(instance['home'], instance['archive'], source)
    (source / 'PRIVATE-SECOND.txt').write_text('second body')
    script = r'''
import ctypes,os,sys
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'tests'))
from native_registration import handle_path,intercept_registration_publish
from hostrun.update_kb import import_files
home=Path(sys.argv[1]); phase=sys.argv[4]
if phase=='pre_receipt':
    def stop(source,target):
        if list((home.parent/'archive/originals').rglob('.horizon-copy-*.tmp')): os._exit(77)
    intercept_registration_publish(stop)
else:
    native=ctypes.WinDLL;api=native('kernel32',use_last_error=True)
    def write(handle,buffer,size,count,overlapped):
        api.WriteFile.argtypes=write.argtypes
        path=handle_path(api,handle)
        if 'originals' in path.parts and path.name.startswith('.horizon-copy-'):
            if phase=='post_receipt': os._exit(77)
            if phase=='partial_body':
                assert api.WriteFile(handle,buffer,3,count,overlapped)
                os._exit(77)
        return api.WriteFile(handle,buffer,size,count,overlapped)
    def flush(handle):
        api.FlushFileBuffers.argtypes=flush.argtypes
        result=api.FlushFileBuffers(handle)
        path=handle_path(api,handle)
        if result and phase=='full_body' and 'originals' in path.parts and path.name.startswith('.horizon-copy-'): os._exit(77)
        return result
    class Kernel:
        def __getattr__(self,name):
            if name=='WriteFile': return write
            if name=='FlushFileBuffers': return flush
            return getattr(api,name)
    ctypes.WinDLL=lambda name,**kwargs: Kernel() if name=='kernel32' else native(name,**kwargs)
import_files(sys.argv[1],sys.argv[2],sys.argv[3])
'''
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
                              str(instance['home']), str(instance['archive']), str(source), phase],
                             env=os.environ, capture_output=True, text=True, timeout=20)
    assert process.returncode == 77, process.stderr
    staged = list(instance['archive'].rglob('.horizon-copy-*.tmp'))
    assert len(staged) == 1
    assert not list(instance['archive'].rglob('PRIVATE-SECOND.txt'))
    import_files(instance['home'], instance['archive'], source)
    archive_index.register(instance['home'], instance['archive'])
    rows = archive_index.load(instance['home'])
    assert len(rows) == 2
    assert {Path(row['path']).name for row in rows} == {'PRIVATE-FIRST.txt', 'PRIVATE-SECOND.txt'}
    cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
    assert cleaned['status'] == ('partial' if phase == 'pre_receipt' else 'cleaned')
    assert staged[0].exists() == (phase == 'pre_receipt')
    assert 'PRIVATE-' not in json.dumps(cleaned)


def test_managed_upload_callback_keeps_lease_without_inventory_deadlock(material_instance, tmp_path):
    from common import file_lock
    instance, control, source = material_instance
    (source / 'callback.pdf').write_bytes(b'bounded callback fixture')
    called = []
    def upload(home, business, path):
        with file_lock(control / (instance['instance_id'] + '.inventory.lock'), blocking=False) as acquired:
            assert acquired
        archive_index.register(home, instance['archive'])
        cleanup = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
        assert cleanup['status'] == 'blocked' and cleanup['reason'] == 'instance_busy'
        called.append(path)
        return {'ok': True}
    result = import_files(instance['home'], instance['archive'], source, upload=upload)
    assert len(called) == 1
    assert not result['upload_failed']


def test_reserved_staging_source_name_is_refused_without_ownership(material_instance, tmp_path):
    instance, control, source = material_instance
    (source / 'PRIVATE-FIRST.txt').unlink()
    reserved = '.horizon-copy-' + 'a' * 32 + '.tmp'
    (source / reserved).write_text('PRIVATE-RESERVED')
    with pytest.raises(ValueError, match='Invalid material destination'):
        import_files(instance['home'], instance['archive'], source)
    assert not (instance['archive'] / 'originals').exists()
    unknown = instance['archive'] / 'originals' / reserved
    unknown.parent.mkdir()
    unknown.write_text('PRIVATE-UNKNOWN')
    archive_index.register(instance['home'], instance['archive'])
    cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
    assert cleaned['status'] == 'partial'
    assert unknown.read_text() == 'PRIVATE-UNKNOWN'


def test_copy_readback_digest_failure_never_publishes_final(material_instance, monkeypatch, tmp_path):
    import ctypes
    from native_registration import handle_path
    instance, control, source = material_instance
    native = ctypes.WinDLL
    api = native('kernel32', use_last_error=True)
    def read(handle, buffer, size, count, overlapped):
        api.ReadFile.argtypes = read.argtypes
        result = api.ReadFile(handle, buffer, size, count, overlapped)
        path = handle_path(api, handle)
        if result and 'originals' in path.parts and path.name.startswith('.horizon-copy-') and ctypes.cast(count, ctypes.POINTER(ctypes.c_ulong)).contents.value:
            ctypes.memset(buffer, ord('X'), 1)
        return result
    class Kernel:
        def __init__(self): self.local = native('kernel32', use_last_error=True)
        def __getattr__(self, name): return read if name == 'ReadFile' else getattr(self.local, name)
    with monkeypatch.context() as faults:
        faults.setattr(ctypes, 'WinDLL', lambda name, **kwargs: Kernel() if name == 'kernel32' else native(name, **kwargs))
        with pytest.raises(ValueError, match='Material copy verification failed'):
            import_files(instance['home'], instance['archive'], source)
    assert not list(instance['archive'].rglob('PRIVATE-FIRST.txt'))
    assert archive_index.load(instance['home']) == []
    import_files(instance['home'], instance['archive'], source)
    rows = archive_index.load(instance['home'])
    assert len(rows) == 1 and Path(rows[0]['path']).name == 'PRIVATE-FIRST.txt'
    cleaned = cleanup_instance(control, instance['instance_id'], tmp_path / 'report', confirmation=instance['instance_id'])
    assert cleaned['status'] == 'cleaned'
