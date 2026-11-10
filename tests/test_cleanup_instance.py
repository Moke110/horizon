from hostrun.cleanup import prepare_instance
from pathlib import Path
import pytest


@pytest.fixture
def tmp_path(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    control = tmp_path / 'Horizon' / 'test-instances'
    control.mkdir(parents=True)
    return control


def test_new_test_instance_never_adopts_existing_installation(tmp_path):
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'user.txt').write_text('preserve')
    first = prepare_instance(tmp_path)
    second = prepare_instance(tmp_path)
    assert first['root'] != second['root']
    assert (existing / 'user.txt').read_text() == 'preserve'
    assert first['status'] == 'prepared'
    assert first['code'] == first['root'] / 'app'
    assert first['home'] == first['root'] / 'data'


@pytest.mark.parametrize('root', [Path.home(), Path(Path.cwd().anchor)])
def test_refuses_shared_control_locations(root):
    with pytest.raises(ValueError, match='control root'):
        prepare_instance(root)


def test_preparation_refuses_report_or_suite_overlap(tmp_path):
    suite = tmp_path / 'suite'
    suite.mkdir()
    with pytest.raises(ValueError, match='overlap'):
        prepare_instance(tmp_path, protected_roots=[suite])


def test_preview_uses_fixed_instance_paths_not_registration_paths(tmp_path):
    import json
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    record = json.loads(instance['registration'].read_text())
    record['root'] = str(tmp_path / 'unrelated')
    instance['registration'].write_text(json.dumps(record))
    with pytest.raises(ValueError, match='registration'):
        preview(tmp_path, instance['instance_id'])


def test_preview_lists_preserved_data_and_requires_confirmation(tmp_path):
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    result = preview(tmp_path, instance['instance_id'], mode='keep-data')
    assert result['status'] == 'confirmation_required'
    assert str(instance['code']) in result['delete']
    assert str(instance['home']) in result['preserve']
    assert str(instance['registration']) in result['preserve']


def test_cleanup_lock_blocks_upgrade_before_recreating_deleted_home(tmp_path):
    from common import file_lock
    from hostrun.desktop_upgrade import upgrade
    instance = prepare_instance(tmp_path)
    with file_lock(tmp_path / (instance['instance_id'] + '.lock')):
        result = upgrade(instance['code'], instance['home'],
                         from_source='missing.zip', sha256='0' * 64)
    assert result == {'status': 'blocked', 'reason': 'instance_busy'}
    assert not instance['home'].exists()


def test_cleanup_lock_blocks_desktop_before_recreating_deleted_home(tmp_path):
    from common import file_lock
    import os
    import subprocess
    import sys
    instance = prepare_instance(tmp_path)
    with file_lock(tmp_path / (instance['instance_id'] + '.lock')):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-c',
             'from hostrun.desktop import main; main()',
             '--code', str(instance['code']), '--archive', str(instance['archive'])],
            env={**os.environ, 'HORIZON_DATA_HOME': str(instance['home'])},
            capture_output=True, timeout=15)
    assert b'instance_busy' in result.stderr
    assert not instance['home'].exists()


@pytest.mark.parametrize('spelling', ['plain', 'parent', 'extended'])
def test_real_desktop_module_does_not_recreate_home_when_cleanup_busy(tmp_path, spelling):
    from common import file_lock
    import os
    import subprocess
    import sys
    instance = prepare_instance(tmp_path)
    home = str(instance['home'])
    if spelling == 'parent':
        home = str(instance['root'] / '..' / instance['instance_id'] / 'data')
    elif spelling == 'extended':
        home = '\\\\?\\' + home
    with file_lock(tmp_path / (instance['instance_id'] + '.lock')):
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
             '--code', str(instance['code']), '--archive', str(instance['archive'])],
            env={**os.environ, 'HORIZON_DATA_HOME': home},
            capture_output=True, timeout=3)
    assert result.returncode != 0
    assert not instance['home'].exists()


def test_missing_registration_cannot_bypass_upgrade_cleanup_lock(tmp_path):
    from hostrun.desktop_upgrade import upgrade
    instance = prepare_instance(tmp_path)
    instance['registration'].unlink()
    with pytest.raises(ValueError, match='registration'):
        upgrade(instance['code'], instance['home'],
                from_source='missing.zip', sha256='0' * 64)
    assert not instance['home'].exists()


def test_running_desktop_still_uses_desktop_lock_with_instance_lease(tmp_path):
    from hostrun.cleanup import instance_lock
    from hostrun.desktop_upgrade import upgrade
    from common import file_lock
    instance = prepare_instance(tmp_path)
    with instance_lock(instance['home']), file_lock(instance['home'] / 'desktop.lock'):
        result = upgrade(instance['code'], instance['home'],
                         from_source='missing.zip', sha256='0' * 64)
    assert result['reason'] == 'desktop_running'


def test_existing_uuid_shaped_home_is_not_claimed(tmp_path):
    from hostrun.desktop_upgrade import upgrade
    home = tmp_path.parent / 'ordinary' / '0123456789abcdef0123456789abcdef' / 'data'
    result = upgrade(tmp_path / 'application', home,
                     from_source='missing.zip', sha256='0' * 64)
    assert result['reason'] == 'source_not_found'


def test_replaced_instance_directory_is_not_owned(tmp_path):
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    instance['root'].rename(tmp_path / 'original-preserved')
    instance['root'].mkdir()
    with pytest.raises(ValueError, match='identity'):
        preview(tmp_path, instance['instance_id'])


def test_plain_forged_registration_is_not_authority(tmp_path):
    import json
    from hostrun.cleanup import preview
    instance = prepare_instance(tmp_path)
    instance['registration'].write_text(json.dumps({
        'schema_version': 1, 'instance_id': instance['instance_id'], 'state': 'prepared'}))
    with pytest.raises(ValueError, match='registration'):
        preview(tmp_path, instance['instance_id'])


def test_independent_report_handles_missing_application_without_copying_secrets(tmp_path):
    from hostrun.cleanup import collect_instance_report
    instance = prepare_instance(tmp_path)
    instance['home'].mkdir()
    (instance['home'] / 'model-config.json').write_text('SECRET-CREDENTIAL-MARKER')
    report = collect_instance_report(tmp_path, instance['instance_id'], tmp_path.parent / 'report')
    text = '\n'.join(p.read_text(encoding='utf-8') for p in report.iterdir() if p.is_file())
    assert 'SECRET-CREDENTIAL-MARKER' not in text
    assert (report / 'summary.md').exists()
    assert not instance['code'].exists()


def test_report_inside_instance_is_rejected_before_writing(tmp_path):
    from hostrun.cleanup import collect_instance_report
    instance = prepare_instance(tmp_path)
    with pytest.raises(ValueError, match='report'):
        collect_instance_report(tmp_path, instance['instance_id'], instance['root'] / 'report')
    assert not (instance['root'] / 'report').exists()


def test_layout_creation_records_children_and_refuses_preexisting_content(tmp_path):
    from hostrun.cleanup import initialize_layout, preview
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    assert instance['home'].is_dir()
    instance['home'].rename(instance['root'] / 'old-data')
    instance['home'].mkdir()
    with pytest.raises(ValueError, match='identity'):
        preview(tmp_path, instance['instance_id'])


def test_layout_never_adopts_existing_data(tmp_path):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    instance['home'].mkdir()
    (instance['home'] / 'existing.txt').write_text('keep')
    with pytest.raises(ValueError, match='existing'):
        initialize_layout(tmp_path, instance['instance_id'])
    assert (instance['home'] / 'existing.txt').read_text() == 'keep'


def test_confirmed_cleanup_preserves_report_and_unknown_content(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    (instance['home'] / 'model-config.json').write_text('SECRET-MARKER')
    (instance['root'] / 'unknown.txt').write_text('preserve')
    report_root = tmp_path.parent / 'report'
    result = cleanup_instance(tmp_path, instance['instance_id'], report_root,
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert not (instance['home'] / 'model-config.json').exists()
    assert (instance['root'] / 'unknown.txt').read_text() == 'preserve'
    assert Path(result['report']).is_dir()
    assert instance['registration'].exists()


def test_cleanup_failure_reports_files_already_removed(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    (instance['home'] / 'model-config.json').write_text('secret')
    database = instance['home'] / 'horizon.sqlite3'
    database.write_bytes(b'broken database')
    with database.open('rb'):
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert result['removed'] == ['data/model-config.json']
    assert database.exists()


def test_layout_can_retry_after_directory_creation_failure(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.stat
    def fail_data(path, *args, **kwargs):
        if path.name == 'data':
            raise OSError('controlled filesystem failure')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', fail_data)
        with pytest.raises(OSError):
            initialize_layout(tmp_path, instance['instance_id'])
    initialize_layout(tmp_path, instance['instance_id'])
    assert instance['home'].is_dir()


def test_layout_pins_root_before_enumeration(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.iterdir
    attempts = []
    def swap_root(path):
        if path == instance['root'] and not attempts:
            try:
                path.rename(tmp_path / 'moved-original')
            except OSError:
                attempts.append('blocked')
            else:
                attempts.append('replaced')
                path.mkdir()
        return original(path)
    monkeypatch.setattr(Path, 'iterdir', swap_root)
    initialize_layout(tmp_path, instance['instance_id'])
    assert attempts == ['blocked']


def test_layout_resumes_after_final_registration_write_fails(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout, preview
    instance = prepare_instance(tmp_path)
    original = Path.replace
    writes = []
    def fail_final(source, target):
        if Path(target) == instance['registration']:
            writes.append(1)
            if len(writes) == 2:
                raise OSError('controlled final write failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_final)
        with pytest.raises(OSError):
            initialize_layout(tmp_path, instance['instance_id'])
    initialize_layout(tmp_path, instance['instance_id'])
    assert preview(tmp_path, instance['instance_id'])['owned_children'] == ['app', 'archive', 'data']
    assert instance['home'].is_dir()


def test_new_stage_and_child_are_pinned_before_identity_is_read(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.stat
    attempts = []
    def race(path, *args, **kwargs):
        if path.name == 'app' and '.stage-' in path.parent.name and not attempts:
            for candidate in (path.parent, path):
                try:
                    candidate.rename(candidate.with_name(candidate.name + '-replaced'))
                except OSError:
                    attempts.append('blocked')
                else:
                    attempts.append('replaced')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', race)
    initialize_layout(tmp_path, instance['instance_id'])
    assert attempts == ['blocked', 'blocked']


def test_resumed_layout_removes_its_empty_staging_directory(tmp_path, monkeypatch):
    from hostrun.cleanup import initialize_layout
    instance = prepare_instance(tmp_path)
    original = Path.stat
    def fail_mid_move(path, *args, **kwargs):
        if path.name == 'data' and '.stage-' in path.parent.name and (instance['code']).exists():
            raise OSError('controlled interrupted move')
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'stat', fail_mid_move)
        with pytest.raises(OSError):
            initialize_layout(tmp_path, instance['instance_id'])
    initialize_layout(tmp_path, instance['instance_id'])
    assert not list(tmp_path.glob(instance['instance_id'] + '.stage-*'))


def test_clean_instance_can_be_cleaned_twice_and_initialized_again(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    for _ in range(2):
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert result['status'] == 'cleaned'
        assert not instance['home'].exists()
    initialize_layout(tmp_path, instance['instance_id'])
    assert instance['home'].is_dir()


def test_keep_data_uninstall_preserves_session_and_reports_success(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    session = instance['home'] / 'horizon.sqlite3'
    session.write_bytes(b'original session')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'], mode='keep-data')
    assert result['status'] == 'uninstalled'
    assert session.read_bytes() == b'original session'
    assert not instance['code'].exists()


def test_installed_package_inventory_excludes_unknown_user_files(tmp_path):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, cleanup_instance
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text(encoding='utf-8'))['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    result = install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert result['status'] == 'ok'
    code = instance['code'] / 'current'
    assert (code / 'README.md').exists()
    unknown = code / 'personal.txt'
    unknown.write_text('original user document')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'], mode='keep-data')
    assert result['status'] == 'partial'
    assert not (code / 'README.md').exists()
    assert unknown.read_text() == 'original user document'


def test_package_uninstall_removes_registered_empty_directory_skeleton(tmp_path):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package, cleanup_instance
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'], mode='keep-data')
    assert result['status'] == 'uninstalled'
    assert not instance['code'].exists()


@pytest.mark.parametrize('replacement', ['# fixture horizon\n', 'USER REPLACEMENT'])
def test_package_inventory_commit_failure_preserves_unproven_files(tmp_path, monkeypatch, replacement):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = Path.replace
    def fail_inventory(source, target):
        if Path(target) == instance['registration'] and (instance['code'] / 'current' / 'README.md').exists():
            raise OSError('controlled inventory commit failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_inventory)
        with pytest.raises(OSError):
            install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    installed = instance['code'] / 'current' / 'README.md'
    installed.rename(installed.with_name('original-readme.md'))
    installed.write_text(replacement)
    original_id = installed.stat().st_ino
    result = install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert result['status'] == 'blocked'
    assert installed.read_text() == replacement
    assert installed.stat().st_ino == original_id


def test_package_install_refuses_current_junction_inserted_after_absence_check(tmp_path, monkeypatch):
    import json
    import _winapi
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    code = instance['code'] / 'current'
    external = tmp_path.parent / 'external'
    external.mkdir()
    original = Path.exists
    inserted = []
    def race(path):
        present = original(path)
        if path == code and not present and not inserted:
            _winapi.CreateJunction(str(external), str(code))
            inserted.append(True)
        return present
    monkeypatch.setattr(Path, 'exists', race)
    with pytest.raises((OSError, ValueError)):
        install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert inserted == [True]
    assert not list(external.iterdir())


def test_initial_install_journal_failure_leaves_no_unowned_current(tmp_path, monkeypatch):
    import json
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    original = Path.replace
    def fail_journal(source, target):
        if Path(target) == instance['registration']:
            raise OSError('controlled journal failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail_journal)
        with pytest.raises(OSError):
            install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert not (instance['code'] / 'current').exists()
    assert install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)['status'] == 'ok'


def test_interrupted_package_copy_preserves_unproven_replacement(tmp_path, monkeypatch):
    import json
    import shutil
    from test_dist_install import _build
    from hostrun.cleanup import initialize_layout, install_test_package
    package = _build(tmp_path.parent / 'fixture')
    digest = json.loads(Path(package['digest']).read_text())['artifact']['sha256']
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    code = instance['code'] / 'current'
    original = shutil.copyfile
    def interrupted(source, target, *args, **kwargs):
        value = original(source, target, *args, **kwargs)
        if Path(target) == code / 'common.py':
            raise KeyboardInterrupt('controlled abrupt copy interruption')
        return value
    with monkeypatch.context() as patch:
        patch.setattr(shutil, 'copyfile', interrupted)
        with pytest.raises(KeyboardInterrupt):
            install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert (code / '.horizon' / 'dist.pending.json').exists()

    (code / 'common.py').rename(code / 'original-partial.py')
    (code / 'common.py').write_text('USER REPLACEMENT MUST SURVIVE')
    result = install_test_package(tmp_path, instance['instance_id'], package['artifact']['path'], digest)
    assert result['status'] == 'blocked'
    assert (code / 'common.py').read_text() == 'USER REPLACEMENT MUST SURVIVE'
    assert (code / '.horizon' / 'dist.pending.json').exists()


@pytest.mark.parametrize('report_failure', [False, True])
def test_cleanup_stops_exact_job_owner_and_preserves_unrelated_process(tmp_path, report_failure):
    import os
    import subprocess
    import sys
    import time
    from hostrun.cleanup import initialize_layout, cleanup_instance
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    ready = tmp_path.parent / 'owner-ready'
    script = (
        'import subprocess,sys,time; from pathlib import Path; '
        'from hostrun.desktop_processes import install; '
        'from hostrun.cleanup import register_instance_owner, instance_lock; '
        'install(); lease=instance_lock(Path(sys.argv[1])); lease.__enter__(); '
        'register_instance_owner(Path(sys.argv[1])); '
        'child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(60)"]); '
        'Path(sys.argv[2]).write_text(str(child.pid)); time.sleep(60)'
    )
    owner = subprocess.Popen([sys.executable, '-X', 'utf8', '-c', script, str(instance['home']), str(ready)],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
    sentinel = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), owner.communicate(timeout=2)
        if report_failure:
            output = tmp_path.parent / 'report'
            output.write_text('not a directory')
            with pytest.raises(OSError):
                cleanup_instance(tmp_path, instance['instance_id'], output, confirmation=instance['instance_id'])
            assert owner.poll() is None
            assert sentinel.poll() is None
            return
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert owner.wait(timeout=5) is not None
        assert sentinel.poll() is None
        assert result['status'] == 'cleaned'
        assert (Path(result['report']) / 'summary.md').is_file()
        assert result['processes']['status'] == 'stopped'
        assert result['processes']['observed_processes'] >= 2
        import ctypes
        from ctypes import wintypes as w
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        api.OpenProcess.restype = w.HANDLE
        api.WaitForSingleObject.argtypes = [w.HANDLE, w.DWORD]
        api.CloseHandle.argtypes = [w.HANDLE]
        child = api.OpenProcess(0x100000, False, int(ready.read_text()))
        if child:
            try:
                assert api.WaitForSingleObject(child, 0) == 0
            finally:
                api.CloseHandle(child)
        else:
            assert ctypes.get_last_error() == 87
    finally:
        for process in (owner, sentinel):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def test_real_model_configuration_is_owned_and_removed_without_secret_report(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='test-model', api_key='SECRET-CONFIG-MARKER')
    config = instance['home'] / 'agent' / 'model.json'
    assert config.exists()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'
    assert not config.exists()
    report_text = '\n'.join(p.read_text(encoding='utf-8') for p in Path(result['report']).iterdir() if p.is_file())
    assert 'SECRET-CONFIG-MARKER' not in report_text


def test_model_cleanup_preserves_replaced_file_and_unknown_neighbor(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='test-model', api_key='SECRET')
    folder = instance['home'] / 'agent'
    config = folder / 'model.json'
    config.rename(folder / 'user-retained-original.json')
    config.write_text('USER REPLACEMENT')
    neighbor = folder / 'personal.txt'
    neighbor.write_text('USER DOCUMENT')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert config.read_text() == 'USER REPLACEMENT'
    assert neighbor.read_text() == 'USER DOCUMENT'


def test_model_reconfiguration_keeps_registered_identity(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    for key in ('FIRST SECRET', 'SECOND SECRET'):
        configure(instance['home'], provider='test', model='test-model', api_key=key)
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_model_update_write_failure_preserves_previous_configuration(tmp_path, monkeypatch):
    import ctypes
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='ORIGINAL SECRET')
    config = instance['home'] / 'agent' / 'model.json'
    before = config.read_bytes()
    original_dll = ctypes.WinDLL
    kernel = original_dll('kernel32', use_last_error=True)
    def partial_write(handle, buffer, size, written, overlap):
        kernel.WriteFile.argtypes = partial_write.argtypes
        kernel.WriteFile(handle, buffer, min(size, 55), written, overlap)
        ctypes.set_last_error(112)
        return 0
    class KernelProxy:
        def __getattr__(self, name):
            return partial_write if name == 'WriteFile' else getattr(kernel, name)
    def dll(name, *args, **kwargs):
        return KernelProxy() if name == 'kernel32' else original_dll(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(ctypes, 'WinDLL', dll)
        with pytest.raises(OSError):
            configure(instance['home'], provider='test', model='second', api_key='NEW SECRET')
    assert config.read_bytes() == before


def test_model_public_save_recovers_published_config_after_receipt_failure(tmp_path, monkeypatch):
    import os
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    original = os.replace
    writes = 0
    def replace(source, target):
        nonlocal writes
        if Path(target) == instance['registration']:
            writes += 1
            if writes == 2:
                raise OSError('injected final receipt failure')
        return original(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', replace)
        with pytest.raises(OSError):
            configure(instance['home'], provider='test', model='second', api_key='SECRET')
    configure(instance['home'], provider='test', model='third', api_key='SECRET')
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_model_runtime_waits_for_configuration_publication(tmp_path, monkeypatch):
    import ctypes
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure, runtime
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    original_dll = ctypes.WinDLL
    kernel = original_dll('kernel32', use_last_error=True)
    moved = threading.Event()
    release = threading.Event()
    def set_info(handle, kind, buffer, size):
        kernel.SetFileInformationByHandle.argtypes = set_info.argtypes
        result = kernel.SetFileInformationByHandle(handle, kind, buffer, size)
        if kind == 3 and result and not moved.is_set():
            moved.set()
            assert release.wait(5)
        return result
    class KernelProxy:
        def __init__(self):
            self.api = original_dll('kernel32', use_last_error=True)
        def __getattr__(self, name):
            return set_info if name == 'SetFileInformationByHandle' else getattr(self.api, name)
    def dll(name, *args, **kwargs):
        return KernelProxy() if name == 'kernel32' else original_dll(name, *args, **kwargs)
    with monkeypatch.context() as patch, ThreadPoolExecutor(max_workers=2) as pool:
        patch.setattr(ctypes, 'WinDLL', dll)
        writer = pool.submit(configure, instance['home'], provider='test', model='second', api_key='SECRET')
        assert moved.wait(5)
        reader = pool.submit(runtime, instance['home'])
        try:
            with pytest.raises(TimeoutError):
                reader.result(timeout=0.15)
        finally:
            release.set()
        writer.result(timeout=5)
        assert reader.result(timeout=5)['model'] == 'second'


def test_configure_cleanup_reinitialize_configure_round_trip(tmp_path):
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    for _ in range(2):
        initialize_layout(tmp_path, instance['instance_id'])
        configure(instance['home'], provider='test', model='test', api_key='SECRET')
        result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                                  confirmation=instance['instance_id'])
        assert result['status'] == 'cleaned'


def test_gui_model_discovery_selection_refresh_are_owned(tmp_path):
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun import embedded_agent
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'chat-a'}, {'id': 'chat-b'}]}).encode())
        def log_message(self, *args):
            pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        embedded_agent.discover_and_configure(instance['home'], provider='custom', api_key='SECRET',
                                             base_url=f'http://127.0.0.1:{server.server_port}/v1')
        embedded_agent.select_model(instance['home'], 'chat-b')
        embedded_agent.refresh_models(instance['home'])
        assert embedded_agent.runtime(instance['home'])['model'] == 'chat-b'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


@pytest.mark.parametrize('crash_at, expected', [('written', 'first'), ('backup', 'first'), ('published', 'second')])
def test_model_runtime_recovers_real_process_interruption(tmp_path, crash_at, expected):
    import os
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout, cleanup_instance
    from hostrun.embedded_agent import configure, runtime
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    script = '''
import ctypes, os, sys
from pathlib import Path
from hostrun.embedded_agent import configure
original = ctypes.WinDLL
kernel = original('kernel32', use_last_error=True)
count = 0
def set_info(handle, kind, buffer, size):
    global count
    kernel.SetFileInformationByHandle.argtypes = set_info.argtypes
    result = kernel.SetFileInformationByHandle(handle, kind, buffer, size)
    if kind == 3 and result:
        count += 1
        if (sys.argv[2] == 'backup' and count == 1) or (sys.argv[2] == 'published' and count == 2):
            os._exit(74)
    return result
def flush(handle):
    kernel.FlushFileBuffers.argtypes = flush.argtypes
    result = kernel.FlushFileBuffers(handle)
    if result and sys.argv[2] == 'written': os._exit(74)
    return result
class Proxy:
    def __getattr__(self, name):
        if name == 'SetFileInformationByHandle': return set_info
        if name == 'FlushFileBuffers': return flush
        return getattr(kernel, name)
ctypes.WinDLL = lambda name, *args, **kwargs: Proxy() if name == 'kernel32' else original(name, *args, **kwargs)
configure(Path(sys.argv[1]), provider='test', model='second', api_key='SECRET')
'''
    process = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(instance['home']), crash_at],
                             env=os.environ.copy(), capture_output=True, timeout=10)
    assert process.returncode == 74, process.stderr
    assert runtime(instance['home'])['model'] == expected
    result = cleanup_instance(tmp_path, instance['instance_id'], tmp_path.parent / 'report',
                              confirmation=instance['instance_id'])
    assert result['status'] == 'cleaned'


def test_node_agent_rejects_replaced_managed_credentials(tmp_path):
    import os
    import shutil
    import subprocess
    import sys
    from hostrun.cleanup import initialize_layout
    from hostrun.embedded_agent import configure
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    configure(instance['home'], provider='test', model='first', api_key='SECRET')
    config = instance['home'] / 'agent' / 'model.json'
    config.rename(config.with_suffix('.retained'))
    config.write_text('{"provider":"test","model":"first","apiKey":"REPLACED"}')
    process = subprocess.run([shutil.which('node'), 'agent/dist/main.js', '--home', str(instance['home']),
                              '--task', 'hello', '--kind', 'intent_judgement'],
                             env={**os.environ, 'HORIZON_PYTHON': sys.executable}, capture_output=True,
                             text=True, encoding='utf-8', timeout=15)
    assert process.returncode != 0
    assert 'Model configuration identity validation failed' in process.stdout + process.stderr
    assert not (instance['home'] / 'agent' / 'pi').exists()


def test_gui_refresh_cannot_overwrite_new_credentials(tmp_path):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from hostrun.cleanup import initialize_layout
    from hostrun import embedded_agent
    entered, release = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            entered.set()
            assert release.wait(5)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'data': [{'id': 'old-model'}]}).encode())
        def log_message(self, *args): pass
    instance = prepare_instance(tmp_path)
    initialize_layout(tmp_path, instance['instance_id'])
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    embedded_agent.configure(instance['home'], provider='custom', model='old-model', api_key='OLD',
                             base_url=f'http://127.0.0.1:{server.server_port}/v1')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            refresh = pool.submit(embedded_agent.refresh_models, instance['home'])
            try:
                assert entered.wait(5)
                embedded_agent.configure(instance['home'], provider='new-provider', model='new-model', api_key='NEW')
            finally:
                release.set()
            with pytest.raises(ValueError, match='configuration changed'):
                refresh.result(timeout=5)
        assert embedded_agent.runtime(instance['home'])['provider'] == 'new-provider'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize('damage', ['missing', 'tampered', 'lock_directory', 'junction'])
def test_invalid_instance_real_desktop_never_writes_home(tmp_path, damage):
    import os
    import subprocess
    import sys
    instance = prepare_instance(tmp_path)
    if damage == 'missing':
        instance['registration'].unlink()
    elif damage == 'tampered':
        instance['registration'].write_text('{}')
    elif damage == 'lock_directory':
        (tmp_path / (instance['instance_id'] + '.lock')).mkdir()
    else:
        import _winapi
        external = tmp_path.parent / 'external'
        external.mkdir()
        _winapi.CreateJunction(str(external), str(instance['home']))
    result = subprocess.run(
        [sys.executable, '-X', 'utf8', '-m', 'hostrun.desktop',
         '--code', str(instance['code']), '--archive', str(instance['archive'])],
        env={**os.environ, 'HORIZON_DATA_HOME': str(instance['home'])},
        capture_output=True, timeout=3)
    assert result.returncode != 0
    if damage == 'junction':
        assert not list(external.iterdir())
    else:
        assert not instance['home'].exists()
