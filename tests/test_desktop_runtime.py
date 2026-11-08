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
