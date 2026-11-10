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
