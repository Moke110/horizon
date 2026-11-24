from pathlib import Path

import pytest

from hostrun.agent_service import CommandError


def test_unverified_install_does_not_report_installed(tmp_path):
    from hostrun.lark_adapter import LarkAdapter

    adapter = LarkAdapter(tmp_path / 'home', tmp_path / 'code', fetch=lambda url, limit: b'corrupt')
    with pytest.raises(CommandError) as refusal:
        adapter.install()
    assert refusal.value.code == 'lark_install_integrity'
    assert adapter.status()['installation']['status'] == 'missing'


def test_legacy_cli_cannot_supply_adapter_credentials_or_readiness(tmp_path, monkeypatch):
    from hostrun.lark_adapter import LarkAdapter

    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    (legacy / 'config.json').write_text('{"secret":"not-from-horizon"}', encoding='utf8')
    monkeypatch.setenv('LARK_CLI_HOME', str(legacy))
    adapter = LarkAdapter(tmp_path / 'home', tmp_path / 'code')
    state = adapter.status()
    assert state['installation']['status'] == 'missing'
    assert state['credentials'] == 'unconfigured'
    assert state['checks']['binding'] == 'pending'
    assert 'not-from-horizon' not in str(state)

def test_permission_check_keeps_only_known_scope_names(tmp_path, monkeypatch):
    import json
    from hostrun.lark_adapter import LarkAdapter

    adapter = LarkAdapter(tmp_path / 'home', tmp_path / 'code')
    monkeypatch.setattr(adapter, '_credentials', lambda: {'app_id': 'test', 'secret': 'credential-marker'})
    payload = {'ok':False, 'error':{'permission_violations':[
        {'subject':'im:message:send_as_bot'}, {'subject':'credential-marker'}],
        'message':'credential-marker'}}
    calls = []
    def run(args, **options):
        calls.append((args, options['capture_error']))
        return json.dumps(payload)
    monkeypatch.setattr(adapter, 'run', run)
    assert adapter.check_bot() == {'status':'missing_scopes','missing_scopes':['im:message:send_as_bot']}
    assert calls == [(['api', 'GET', '/open-apis/bot/v3/info', '--as', 'bot'], True)]


def test_verified_install_survives_repeated_install_and_corrupt_download(tmp_path, monkeypatch):
    import base64
    import hashlib
    import io
    import json
    import tarfile
    import zipfile
    from hostrun import lark_adapter as module

    binary = b'fixture-runtime'
    notice = b'fixture-license'
    zip_data = io.BytesIO()
    with zipfile.ZipFile(zip_data, 'w') as zipped:
        zipped.writestr('lark-cli.exe', binary)
        zipped.writestr('LICENSE', notice)
    sha256 = hashlib.sha256(zip_data.getvalue()).hexdigest()
    npm_data = io.BytesIO()
    with tarfile.open(fileobj=npm_data, mode='w:gz') as archived:
        entries = {'package/package.json': json.dumps({'name':'@larksuite/cli','version':module.VERSION}).encode(),
                   'package/checksums.txt': f'{sha256}  {module.ASSET}'.encode()}
        for name, content in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archived.addfile(member, io.BytesIO(content))
    monkeypatch.setattr(module, 'NPM_INTEGRITY', base64.b64encode(hashlib.sha512(npm_data.getvalue()).digest()).decode())
    monkeypatch.setattr(module, 'ZIP_SHA256', sha256)
    monkeypatch.setattr(module, 'FILE_DIGESTS', {'lark-cli.exe':hashlib.sha256(binary).hexdigest(),
                                              'LICENSE':hashlib.sha256(notice).hexdigest()})
    good_download = True
    downloads = []
    def fetch(url, maximum):
        downloads.append(url)
        if not good_download:
            return b'corrupted archive'
        return npm_data.getvalue() if url == module.NPM_URL else zip_data.getvalue()
    adapter = module.LarkAdapter(tmp_path / 'home', tmp_path / 'code', fetch=fetch)
    monkeypatch.setattr(adapter, '_run', lambda *a, **kw: 'lark-cli version 1.0.99')
    monkeypatch.setattr(adapter, '_verify_npm_metadata', lambda metadata: None)
    assert adapter.install()['installation']['status'] == 'installed'
    assert adapter.install()['installation']['status'] == 'installed'
    assert downloads == [module.NPM_URL, module.RELEASE]
    persisted = module.LarkAdapter(tmp_path / 'home', tmp_path / 'code', fetch=fetch)
    assert persisted.status()['installation']['status'] == 'installed'
    manifest, = (tmp_path / 'home').glob('feishu/cli-*/manifest.json')
    manifest.write_text('{}', encoding='utf8')
    good_download = False
    with pytest.raises(CommandError, match='完整性校验失败'):
        adapter.install()
    assert persisted.status()['installation']['status'] == 'invalid'
    assert manifest.with_name('lark-cli.exe').read_bytes() == binary
    good_download = True
    assert adapter.install()['installation']['status'] == 'installed'
