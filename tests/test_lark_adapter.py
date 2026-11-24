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
    write_new = module._write_new
    def write_checked(parent, path, body):
        for directory in (adapter.root, path.parent):
            with pytest.raises(OSError):
                directory.rename(directory.with_suffix('.replaced'))
        return write_new(parent, path, body)
    monkeypatch.setattr(module, '_write_new', write_checked)
    assert adapter.install()['installation']['status'] == 'installed'
    assert (adapter.root / 'install.lock').read_bytes() == b''
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
    publish = module._publish
    with monkeypatch.context() as interrupted:
        def fail_pointer(path, data):
            if path.name == 'active-cli.json':
                raise OSError('simulated interruption before publication')
            return publish(path, data)
        interrupted.setattr(module, '_publish', fail_pointer)
        with pytest.raises(CommandError, match='安装未完成'):
            adapter.install()
    assert persisted.status()['installation']['status'] == 'invalid'
    assert manifest.with_name('lark-cli.exe').read_bytes() == binary
    assert adapter.install()['installation']['status'] == 'installed'
    assert manifest.with_name('lark-cli.exe').read_bytes() == binary

def test_failed_runtime_recheck_preserves_files_and_reports_failure(tmp_path, monkeypatch):
    import hashlib
    import json
    from hostrun import lark_adapter as module

    home, code = tmp_path / 'home', tmp_path / 'code'
    adapter = module.LarkAdapter(home, code)
    installed = home / 'feishu' / f'cli-{module.VERSION}'
    installed.mkdir(parents=True)
    binary = b'verified native CLI bytes'
    path = installed / 'lark-cli.exe'
    path.write_bytes(binary)
    monkeypatch.setattr(module, 'FILE_DIGESTS', {'lark-cli.exe':hashlib.sha256(binary).hexdigest()})
    (installed / 'manifest.json').write_text(json.dumps(adapter._manifest()), encoding='utf8')
    monkeypatch.setattr(adapter, 'run', lambda *args: 'unexpected runtime output')
    with pytest.raises(CommandError):
        adapter.install()
    state = adapter.status()['installation']
    assert state['status'] == 'installed'
    assert state['runtime'] == 'failed'
    assert path.read_bytes() == binary
    assert module.LarkAdapter(home, code).status()['installation']['runtime'] == 'unchecked'

def test_native_executable_is_pinned_until_the_launch_returns(tmp_path, monkeypatch):
    import hashlib
    import os
    from hostrun import lark_adapter as module

    if os.name != 'nt':
        pytest.skip('Windows native pin behavior')
    home, code = tmp_path / 'home', tmp_path / 'code'
    runtime = code / 'runtime'
    runtime.mkdir(parents=True)
    node = runtime / 'node.exe'
    node.write_bytes(b'node fixture')
    staged = home / 'staged'
    staged.mkdir(parents=True)
    native = staged / 'lark-cli.exe'
    native.write_bytes(b'lark fixture')
    monkeypatch.setattr(module, '_NODE_SHA256', hashlib.sha256(node.read_bytes()).hexdigest())
    monkeypatch.setattr(module, 'FILE_DIGESTS', {'lark-cli.exe':hashlib.sha256(native.read_bytes()).hexdigest()})
    adapter = module.LarkAdapter(home, code)
    checked = []
    def execute(argv, **options):
        image = Path(argv[0])
        with pytest.raises(OSError):
            image.write_bytes(b'replacement')
        with pytest.raises(OSError):
            image.rename(image.with_suffix('.old'))
        checked.append(image)
        return 'lark-cli version 1.0.99'
    monkeypatch.setattr(adapter, '_execute', execute)
    adapter._verify_npm_metadata('{}')
    assert adapter._run(staged, ['--version']) == 'lark-cli version 1.0.99'
    assert checked == [node, native]

def test_native_install_lock_refuses_external_hard_links(tmp_path):
    import os
    from hostrun import lark_adapter as module

    if os.name != 'nt':
        pytest.skip('Windows native lock behavior')
    home, code = tmp_path / 'home', tmp_path / 'code'
    root = home / 'feishu'
    root.mkdir(parents=True)
    sentinel = tmp_path / 'external-empty.txt'
    sentinel.touch()
    os.link(sentinel, root / 'install.lock')
    adapter = module.LarkAdapter(home, code, fetch=lambda *args: pytest.fail('started downloading'))
    with pytest.raises(CommandError, match='安装未完成'):
        adapter.install()
    assert sentinel.read_bytes() == b''
    assert adapter.status()['installation']['status'] == 'missing'

def test_second_install_cannot_write_or_download_under_native_lock(tmp_path):
    from hostrun import lark_adapter as module

    home, code = tmp_path / 'home', tmp_path / 'code'
    contender = module.LarkAdapter(home, code, fetch=lambda *args: pytest.fail('contender downloaded'))
    def competing_fetch(*args):
        with pytest.raises(CommandError) as error:
            contender.install()
        assert error.value.code == 'lark_busy'
        return b'bad first archive'
    first = module.LarkAdapter(home, code, fetch=competing_fetch)
    with pytest.raises(CommandError) as error:
        first.install()
    assert error.value.code == 'lark_install_integrity'
    assert (home / 'feishu' / 'install.lock').read_bytes() == b''
