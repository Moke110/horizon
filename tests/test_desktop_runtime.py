import os
from pathlib import Path

import pytest

from hostrun.desktop_runtime import executable


def test_runtime_deployment_reuses_cache_and_tracks_resource_changes(tmp_path):
    code = tmp_path / 'workspace'
    source = code / 'desktop/node_modules/electron/dist'
    source.mkdir(parents=True)
    (source / 'electron.exe').write_bytes(b'runtime')
    (source / 'icudtl.dat').write_bytes(b'first')
    local = tmp_path / 'local'
    first = executable(code, local_app_data=local)
    assert first.read_bytes() == b'runtime'
    assert first.parent.parent == local / 'Horizon/desktop-runtime'
    assert executable(code, local_app_data=local) == first
    (source / 'icudtl.dat').write_bytes(b'updated')
    second = executable(code, local_app_data=local)
    assert second != first
    assert (second.parent / 'icudtl.dat').read_bytes() == b'updated'
    assert first.exists()
    assert not list(second.parent.parent.glob('install-*'))


def test_missing_runtime_has_actionable_error(tmp_path):
    with pytest.raises(RuntimeError, match='npm ci'):
        executable(tmp_path, local_app_data=tmp_path / 'local')


def test_gui_launch_does_not_apply_hidden_window_policy(monkeypatch):
    from hostrun import desktop_processes as processes
    captured = {}
    monkeypatch.setattr(processes, '_original_init', lambda self, *a, **kw: captured.update(kw))
    processes.GuiProcess(['electron.exe'])
    assert 'startupinfo' not in captured
    if processes.os.name == 'nt':
        assert captured['creationflags'] == processes.subprocess.CREATE_NO_WINDOW


def test_installed_runtime_is_reused_when_install_lock_is_not_writable(tmp_path, monkeypatch):
    from hostrun import desktop_runtime
    source = tmp_path / 'code/desktop/node_modules/electron/dist'
    source.mkdir(parents=True)
    (source / 'electron.exe').write_bytes(b'runtime')
    (source / 'icudtl.dat').write_bytes(b'resource')
    local = tmp_path / 'local'
    installed = executable(tmp_path / 'code', local_app_data=local)
    def denied(*args, **kwargs):
        raise PermissionError('read-only install lock')
    monkeypatch.setattr(desktop_runtime, 'file_lock', denied)
    assert executable(tmp_path / 'code', local_app_data=local) == installed
    assert (installed.parent / 'icudtl.dat').read_bytes() == b'resource'
    (source / 'icudtl.dat').write_bytes(b'new runtime revision')
    with pytest.raises(PermissionError, match='read-only install lock'):
        executable(tmp_path / 'code', local_app_data=local)
    assert installed.read_bytes() == b'runtime'

_ELECTRON = 'desktop/node_modules/electron/dist'


@pytest.fixture
def managed_package(tmp_path, monkeypatch):
    from hostrun import cleanup, distribution
    from test_dist_install import PACKAGE_FILES, _host_compat, _write_tree

    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    control = cleanup.control_directory()
    control.mkdir(parents=True)
    source = _write_tree(tmp_path / 'source', {**PACKAGE_FILES,
        f'{_ELECTRON}/electron.exe': 'electron fixture',
        f'{_ELECTRON}/locales/en-US.pak': 'resource fixture',
    })
    package = distribution.build(source=source, out=tmp_path / 'package', version='0.9.0',
                                 channel='stable', commit='test', compat=_host_compat())
    def install():
        instance = cleanup.prepare_instance(control)
        cleanup.initialize_layout(control, instance['instance_id'])
        assert cleanup.install_test_package(control, instance['instance_id'],
            package['artifact']['path'], package['artifact']['sha256'])['status'] == 'ok'
        return instance, instance['code'] / 'current'
    return control, install


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows instance receipts')
def test_managed_runtime_uses_its_owned_bundle_and_leaves_legacy_cache(managed_package, tmp_path, monkeypatch):
    from hostrun import cleanup, desktop_bridge, desktop_runtime

    control, install = managed_package
    first, first_code = install()
    second, second_code = install()
    legacy = control.parent / 'desktop-runtime/legacy/electron.exe'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'legacy runtime')
    with desktop_runtime.managed_executable(first_code, first['home']) as path:
        assert path == first_code / _ELECTRON / 'electron.exe'
        with pytest.raises(OSError):
            path.write_bytes(b'replacement')
        with pytest.raises(OSError):
            path.rename(path.with_name('renamed.exe'))
        with desktop_runtime.managed_executable(second_code, second['home']) as other:
            assert other == second_code / _ELECTRON / 'electron.exe'
        result = cleanup.cleanup_instance(control, first['instance_id'], tmp_path / 'blocked-report',
                                          confirmation=first['instance_id'])
        assert result['status'] == 'blocked' and result['reason'] == 'instance_busy'
    def expect_managed_launch(resolved, path, managed):
        assert path == first_code / _ELECTRON / 'electron.exe'
        assert managed
        with pytest.raises(OSError):
            path.write_bytes(b'live replacement')
        return 'launched'

    def refuse_shared_runtime(*args, **kwargs):
        pytest.fail('Managed package used shared runtime')

    monkeypatch.setattr(desktop_bridge, '_serve', expect_managed_launch)
    monkeypatch.setattr(desktop_runtime, 'executable', refuse_shared_runtime)
    assert desktop_bridge.serve({'code': str(first_code), 'runtime': str(first['home'])}) == 'launched'
    assert cleanup.cleanup_instance(control, first['instance_id'], tmp_path / 'report',
                                    confirmation=first['instance_id'])['status'] == 'cleaned'
    assert legacy.read_bytes() == b'legacy runtime'
    assert list(legacy.parent.parent.iterdir()) == [legacy.parent]
    assert (second_code / _ELECTRON / 'electron.exe').is_file()


@pytest.mark.skipif(os.name != 'nt', reason='requires native Windows instance receipts')
@pytest.mark.parametrize('damage', ['missing', 'modified', 'same_bytes_replacement', 'unknown', 'wrong_instance'])
def test_managed_runtime_does_not_fall_back_to_shared_cache(managed_package, damage):
    from hostrun import cleanup, desktop_runtime

    control, install = managed_package
    instance, code = install()
    runtime = code / _ELECTRON
    target = runtime / 'locales/en-US.pak'
    if damage == 'missing':
        target.unlink()
    elif damage == 'modified':
        target.write_bytes(b'changed resource')
    elif damage == 'same_bytes_replacement':
        backup = target.with_suffix('.old')
        target.rename(backup)
        target.write_bytes(backup.read_bytes())
        backup.unlink()
    elif damage == 'unknown':
        (runtime / 'unknown.dll').write_bytes(b'unowned')
    else:
        _, code = install()
    with pytest.raises((cleanup.InstanceInvalid, OSError)):
        with desktop_runtime.managed_executable(code, instance['home']):
            pytest.fail('An unverified bundle was selected')
    assert not (control.parent / 'desktop-runtime').exists()
