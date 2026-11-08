from common import file_lock
from hostrun.desktop_upgrade import upgrade


def test_running_desktop_refuses_upgrade_without_touching_data(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    data = home / 'session.jsonl'
    data.write_text('preserved', encoding='utf-8')
    with file_lock(home / 'desktop.lock'):
        result = upgrade(tmp_path / 'installed', home, from_source='missing.zip', sha256='0' * 64)
    assert result['reason'] == 'desktop_running'
    assert data.read_text(encoding='utf-8') == 'preserved'


def test_upgrade_retains_home_and_commits_version_after_verified_install(tmp_path):
    import json
    from test_dist_install import _build
    package = _build(tmp_path)
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'conversation.jsonl').write_text('same session', encoding='utf-8')
    result = upgrade(tmp_path / 'installed', home, from_source=package['artifact']['path'],
                     sha256=json.loads(open(package['digest'], encoding='utf-8').read())['artifact']['sha256'])
    assert result['status'] == 'ok'
    assert (home / 'conversation.jsonl').read_text(encoding='utf-8') == 'same session'
    marker = json.loads((home / 'version.json').read_text(encoding='utf-8'))
    assert marker['version'] == '0.1.0'
    assert (home / marker['backup'] / 'conversation.jsonl').read_text(encoding='utf-8') == 'same session'


def test_invalid_upgrade_preserves_data_and_version(tmp_path):
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'version.json').write_text('{"version":"old"}', encoding='utf-8')
    (home / 'session.jsonl').write_text('old context', encoding='utf-8')
    result = upgrade(tmp_path / 'installed', home, from_source='absent.zip', sha256='0' * 64)
    assert result['status'] == 'blocked'
    assert (home / 'version.json').read_text(encoding='utf-8') == '{"version":"old"}'
    assert (home / 'session.jsonl').read_text(encoding='utf-8') == 'old context'


def test_failed_schema_initialization_restores_home(tmp_path, monkeypatch):
    import json
    from test_dist_install import _build
    from hostrun import agent_service
    package = _build(tmp_path)
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'session.jsonl').write_text('old context', encoding='utf-8')
    def fail_schema(data_home, **kwargs):
        (data_home / 'session.jsonl').write_text('damaged', encoding='utf-8')
        (data_home / 'new-schema-file').write_text('partial', encoding='utf-8')
        raise RuntimeError('injected migration failure')
    monkeypatch.setattr(agent_service, 'AgentService', fail_schema)
    result = upgrade(tmp_path / 'installed', home, from_source=package['artifact']['path'], sha256=package['artifact']['sha256'])
    assert result['status'] == 'blocked'
    assert (home / 'session.jsonl').read_text(encoding='utf-8') == 'old context'
    assert not (home / 'version.json').exists()
    assert not (home / 'new-schema-file').exists()
    assert not (tmp_path / 'installed').exists()


def test_overlapping_install_and_home_is_rejected(tmp_path):
    result = upgrade(tmp_path, tmp_path / 'data', from_source='absent.zip', sha256='0' * 64)
    assert result['reason'] == 'overlapping_data_and_code'
    assert not (tmp_path / 'data').exists()


def test_interrupted_upgrade_recovers_before_retry(tmp_path, monkeypatch):
    import pytest
    from hostrun import agent_service
    from test_dist_install import _build
    package = _build(tmp_path)
    home = tmp_path / 'home'
    home.mkdir()
    (home / 'session.jsonl').write_text('old context', encoding='utf-8')
    original = agent_service.AgentService
    def crash(data_home, **kwargs):
        (data_home / 'session.jsonl').write_text('incomplete', encoding='utf-8')
        raise SystemExit('process interruption')
    monkeypatch.setattr(agent_service, 'AgentService', crash)
    with pytest.raises(SystemExit):
        upgrade(tmp_path / 'installed', home, from_source=package['artifact']['path'], sha256=package['artifact']['sha256'])
    assert (home / 'upgrade.pending.json').exists()
    monkeypatch.setattr(agent_service, 'AgentService', original)
    result = upgrade(tmp_path / 'installed', home, from_source=package['artifact']['path'], sha256=package['artifact']['sha256'])
    assert result['status'] == 'ok'
    assert (home / 'session.jsonl').read_text(encoding='utf-8') == 'old context'
    assert not (home / 'upgrade.pending.json').exists()


def test_installer_state_does_not_hide_unexpected_package_files(tmp_path):
    from test_dist_install import _build
    from hostrun import dist
    package = _build(tmp_path)
    code = tmp_path / 'installed'
    assert upgrade(code, tmp_path / 'home', from_source=package['artifact']['path'], sha256=package['artifact']['sha256'])['status'] == 'ok'
    state = code / '.horizon' / 'installer'
    state.mkdir(exist_ok=True)
    (state / 'unins000.exe').write_bytes(b'generated uninstaller')
    (code / 'Horizon.lnk').write_bytes(b'generated shortcut')
    assert dist.verify_install(code)['status'] == 'verified'
    (code / 'unins000.exe').write_bytes(b'not installer-owned here')
    assert dist.verify_install(code)['status'] != 'verified'


def test_journal_commit_failure_never_exposes_partial_pending(tmp_path, monkeypatch):
    import pytest
    from hostrun import desktop_upgrade
    def fail_replace(source, destination):
        if str(destination).endswith('upgrade.pending.json'):
            raise OSError('injected journal commit failure')
    monkeypatch.setattr(desktop_upgrade.os, 'replace', fail_replace)
    with pytest.raises(OSError):
        upgrade(tmp_path / 'code', tmp_path / 'home', from_source='absent.zip', sha256='0'*64)
    assert not (tmp_path / 'home/upgrade.pending.json').exists()
    assert not (tmp_path / 'code').exists()
