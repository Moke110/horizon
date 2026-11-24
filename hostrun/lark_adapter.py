"""Horizon-owned installation and setup diagnostics for the official Feishu CLI."""

from __future__ import annotations

from contextlib import closing
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sysconfig
import tarfile
import tempfile
import threading
import urllib.error
import urllib.request
import uuid
import zipfile

from common import file_lock, json_bytes
from hostrun.agent_service import CommandError
from hostrun.runtime_inventory import PINNED_VERSIONS
from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
from hostrun.windows_protection import protect


VERSION = '1.0.99'
NPM_URL = f'https://registry.npmjs.org/@larksuite/cli/-/cli-{VERSION}.tgz'
NPM_INTEGRITY = 'eUiGhjSEmeDu9w3741e1bF72LyYVtT5o+Dnb4SouTb52r6aRu2Us7QyZpcVdb5rfDvnPV4rPYWbGvvP1xFbp8w=='
ASSET = f'lark-cli-{VERSION}-windows-amd64.zip'
RELEASE = f'https://github.com/larksuite/cli/releases/download/v{VERSION}/{ASSET}'
MIRROR = f'https://registry.npmmirror.com/-/binary/lark-cli/v{VERSION}/{ASSET}'
ZIP_SHA256 = '1b43cb63ff533ee067db45d93a862074d147df5a0d05f95c2d2a7c70c3f97375'
FILE_DIGESTS = {
    'lark-cli.exe': '1b9babbff017bb5d4c9974f9ad547707aed61005196134b41e1e15764b213d74',
    'LICENSE': 'c969fc7e3af68e6bf40b0d8dd9c3dcc377eb685a2139535b203b39fdcad739ee',
}
CONSOLE = 'https://open.feishu.cn/app'
REQUIRED_SCOPES = ('im:message.p2p_msg:readonly', 'im:message:send_as_bot')
_VERSION_PROBE = f'lark-cli version {VERSION}'
_CHECK_NPM_MANIFEST = """
const metadata = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
if (metadata.name !== '@larksuite/cli' || metadata.version !== process.argv[1] ||
    metadata.bin?.['lark-cli'] !== 'scripts/run.js') process.exit(1);
"""
_ENV_KEYS = {'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE',
             'HTTPS_PROXY', 'HTTP_PROXY', 'NO_PROXY'}


def _download(url, maximum):
    with urllib.request.urlopen(url, timeout=20) as response:
        data = response.read(maximum + 1)
    if len(data) > maximum:
        raise CommandError('lark_install_integrity', '飞书 CLI 制品大小异常')
    return data


def _digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def _safe_entry(path):
    return not path.is_symlink() and not path.is_junction()


def _publish(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.horizon-write-', delete=False) as stream:
        stage = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            os.replace(stage, path)
        finally:
            stage.unlink(missing_ok=True)


class LarkAdapter:
    def __init__(self, home, code, *, fetch=None):
        self.home = Path(home).resolve()
        self.code = Path(code).resolve()
        self.root = self.home / 'feishu'
        self.fetch = fetch or _download
        self.stopped = threading.Event()
        self.processes = set()
        self.lock = threading.Lock()

    @property
    def _target(self):
        return self.root / f'cli-{VERSION}'

    @property
    def _credential_path(self):
        return self.root / 'credentials.dpapi'

    def _prepare_root(self):
        if not _safe_entry(self.root):
            raise CommandError('lark_store_unavailable', '飞书适配器目录不可用')
        self.root.mkdir(parents=True, exist_ok=True)
        store = self.root / 'cli-state'
        if not _safe_entry(store):
            raise CommandError('lark_store_unavailable', '飞书 CLI 状态目录不可用')
        store.mkdir(exist_ok=True)

    def _manifest(self):
        return {'version': VERSION, 'npm_url': NPM_URL, 'npm_integrity': 'sha512-' + NPM_INTEGRITY,
                'release': RELEASE, 'archive_sha256': ZIP_SHA256, 'files': FILE_DIGESTS}

    def _verified(self):
        try:
            if not (_safe_entry(self.root) and _safe_entry(self._target)):
                return False
            if not all(_safe_entry(self._target / name) for name in ('manifest.json', *FILE_DIGESTS)):
                return False
            manifest = json.loads((self._target / 'manifest.json').read_text(encoding='utf-8'))
            return manifest == self._manifest() and all(
                _digest(self._target / name) == expected for name, expected in FILE_DIGESTS.items())
        except (OSError, ValueError):
            return False

    def _node(self):
        path = self.code / 'runtime' / 'node.exe'
        try:
            if not _safe_entry(path) or not path.resolve().is_relative_to(self.code):
                raise ValueError
            record = json.loads((path.parent / 'components.json').read_text(encoding='utf-8'))
            if record['schema_version'] != 1:
                raise ValueError
            node, = [item for item in record['components'] if item.get('name') == 'node']
            if (node['version'] != PINNED_VERSIONS['node'] or node['executable'] != 'node.exe'
                    or _digest(path) != node['sha256']):
                raise ValueError
        except (KeyError, OSError, TypeError, ValueError):
            raise CommandError('lark_runtime_missing', '需要通过 Horizon 发行包使用内置 Node.js') from None
        return path

    def status(self):
        if self._verified():
            install = 'installed'
        elif self._target.exists():
            install = 'invalid'
        else:
            install = 'missing'
        credential_path = self._credential_path
        state, console = 'unconfigured', CONSOLE
        if _safe_entry(self.root) and _safe_entry(credential_path) and credential_path.is_file():
            try:
                console = f"{CONSOLE}/{self._credentials()['app_id']}/auth"
                state = 'configured'
            except CommandError:
                state = 'unreadable'
        return {'installation': {'status': install, 'version': VERSION, 'source': RELEASE,
                                 'integrity': 'sha512-' + NPM_INTEGRITY, 'archive_sha256': ZIP_SHA256},
                'credentials': state, 'console_url': console, 'required_scopes': list(REQUIRED_SCOPES),
                'checks': {'permissions': 'unverified', 'messages': 'pending', 'cards': 'pending', 'binding': 'pending'}}

    def install(self):
        if sysconfig.get_platform() != 'win-amd64':
            raise CommandError('lark_platform', '此固定版本的适配器需要 Windows x64')
        try:
            self._prepare_root()
            with file_lock(self.root / 'install.lock'):
                if self._verified():
                    if self.run(['--version']).strip() != _VERSION_PROBE:
                        raise CommandError('lark_install_integrity', '飞书 CLI 版本自检失败')
                else:
                    self._install_bytes()
        except CommandError:
            raise
        except (OSError, ValueError, subprocess.SubprocessError, tarfile.TarError, zipfile.BadZipFile):
            raise CommandError('lark_install_failed', '飞书 CLI 安装未完成，请重试') from None
        return self.status()

    def _install_bytes(self):
        npm = self.fetch(NPM_URL, 1024 * 1024)
        if base64.b64encode(hashlib.sha512(npm).digest()).decode() != NPM_INTEGRITY:
            raise CommandError('lark_install_integrity', '官方 npm 制品的完整性校验失败')
        with tarfile.open(fileobj=io.BytesIO(npm), mode='r:gz') as archive:
            metadata = archive.extractfile('package/package.json').read().decode('utf-8')
            checksums = archive.extractfile('package/checksums.txt').read().decode('ascii').splitlines()
        if f'{ZIP_SHA256}  {ASSET}' not in checksums:
            raise CommandError('lark_install_integrity', '官方 npm 制品清单不匹配')
        self._verify_npm_metadata(metadata)
        try:
            release = self.fetch(RELEASE, 70 * 1024 * 1024)
        except (OSError, urllib.error.HTTPError):
            release = self.fetch(MIRROR, 70 * 1024 * 1024)
        if hashlib.sha256(release).hexdigest() != ZIP_SHA256:
            raise CommandError('lark_install_integrity', '飞书 CLI 二进制制品的完整性校验失败')
        with tempfile.TemporaryDirectory(prefix='.cli-stage-', dir=self.root) as temp:
            staged = Path(temp) / 'payload'
            staged.mkdir()
            with zipfile.ZipFile(io.BytesIO(release)) as archive:
                for name, expected in FILE_DIGESTS.items():
                    body = archive.read(name)
                    if hashlib.sha256(body).hexdigest() != expected:
                        raise CommandError('lark_install_integrity', '飞书 CLI 文件摘要不匹配')
                    (staged / name).write_bytes(body)
            if self._run(staged, ['--version']).strip() != _VERSION_PROBE:
                raise CommandError('lark_install_integrity', '飞书 CLI 版本自检失败')
            (staged / 'manifest.json').write_bytes(json_bytes(self._manifest()))
            if self._target.exists():
                if not _safe_entry(self._target):
                    raise CommandError('lark_store_unavailable', '飞书 CLI 安装路径不可用')
                backup = self.root / ('previous-' + uuid.uuid4().hex)
                self._target.rename(backup)
                try:
                    staged.rename(self._target)
                except OSError:
                    try:
                        backup.rename(self._target)
                    except OSError:
                        # Preserve the last bytes under the owned backup name.
                        pass
                    raise
            else:
                staged.rename(self._target)

    def configure(self, app_id, secret):
        if not isinstance(app_id, str) or not re.fullmatch(r'cli_[a-zA-Z0-9]{6,64}', app_id):
            raise CommandError('lark_credential_input', '请输入应用凭据页的 App ID')
        if not isinstance(secret, str) or not 1 <= len(secret) <= 512 or any(
                not 33 <= ord(character) <= 126 for character in secret):
            raise CommandError('lark_credential_input', '请输入有效的 App Secret')
        if not self._verified():
            raise CommandError('lark_install_required', '请先安装并校验飞书 CLI')
        self._prepare_root()
        try:
            candidate = {'app_id': app_id, 'secret': secret}
            verified = json.loads(self.run(['api', 'GET', '/open-apis/bot/v3/info', '--as', 'bot', '--dry-run'],
                                           credentials=candidate))
            if (verified.get('ok') is not True or verified.get('identity') != 'bot'
                    or verified.get('data', {}).get('context', {}).get('app_id') != app_id):
                raise CommandError('lark_credentials_unavailable', '内置 CLI 未采用所填应用凭据')
            sealed = protect(json_bytes(candidate), self._entropy())
            _publish(self._credential_path, sealed)
        except (OSError, ValueError):
            raise CommandError('lark_credentials_unavailable', '无法保存受 Windows 保护的应用凭据') from None
        return self.status()

    def _entropy(self):
        return f'Horizon/Feishu/v1:{self.home}'.encode('utf8')

    def _credentials(self):
        try:
            if not _safe_entry(self._credential_path):
                raise ValueError
            value = json.loads(protect(self._credential_path.read_bytes(), self._entropy(), decrypt=True))
            if not isinstance(value, dict) or set(value) != {'app_id', 'secret'} or any(
                    not isinstance(item, str) or not item for item in value.values()) or not re.fullmatch(
                    r'cli_[a-zA-Z0-9]{6,64}', value['app_id']):
                raise ValueError
            return value
        except (OSError, ValueError):
            raise CommandError('lark_credentials_unavailable', '飞书凭据不可读，请重新保存') from None

    def check_bot(self):
        credentials = self._credentials()
        try:
            result = json.loads(self.run(['api', 'GET', '/open-apis/bot/v3/info', '--as', 'bot'], credentials=credentials, capture_error=True))
        except (ValueError, CommandError) as error:
            if isinstance(error, CommandError) and error.code in ('lark_install_required', 'lark_runtime_missing'):
                raise
            return {'status': 'unverified', 'missing_scopes': []}
        if isinstance(result, dict) and result.get('ok') is True:
            return {'status': 'bot_verified', 'missing_scopes': []}
        error = result.get('error') if isinstance(result, dict) else None
        reported = []
        if isinstance(error, dict):
            if isinstance(error.get('missing_scopes'), list):
                reported += error['missing_scopes']
            if isinstance(error.get('required_scopes'), list):
                reported += error['required_scopes']
            if isinstance(error.get('permission_violations'), list):
                reported += [row.get('subject') for row in error['permission_violations'] if isinstance(row, dict)]
        missing = [scope for scope in REQUIRED_SCOPES if scope in reported]
        return {'status': 'missing_scopes' if missing else 'unverified', 'missing_scopes': missing}

    def qr(self):
        self._prepare_root()
        url = self.status()['console_url']
        with tempfile.TemporaryDirectory(prefix='.auth-qr-', dir=self.root) as temp:
            self.run(['auth', 'qrcode', url, '--output', 'authorization.png'], cwd=Path(temp))
            png = (Path(temp) / 'authorization.png').read_bytes()
            if not png.startswith(b'\x89PNG\r\n\x1a\n') or len(png) > 200_000:
                raise CommandError('lark_qr_failed', '无法生成飞书授权入口二维码')
        return {'url': url, 'png': base64.b64encode(png).decode('ascii')}

    def run(self, args, *, credentials=None, cwd=None, capture_error=False):
        if not self._verified():
            raise CommandError('lark_install_required', '请先安装并校验飞书 CLI')
        with pin_ancestors(self._target), closing(PinnedEntry(self._target / 'lark-cli.exe', read_only_share=True)):
            if _digest(self._target / 'lark-cli.exe') != FILE_DIGESTS['lark-cli.exe']:
                raise CommandError('lark_install_integrity', '飞书 CLI 文件摘要不匹配')
            return self._run(self._target, args, credentials=credentials, cwd=cwd, capture_error=capture_error)

    def _verify_npm_metadata(self, metadata):
        try:
            self._execute([str(self._node()), '-e', _CHECK_NPM_MANIFEST, VERSION], input_text=metadata)
        except CommandError as error:
            if error.code == 'lark_cli_failed':
                raise CommandError('lark_install_integrity', '官方 npm 制品清单不匹配') from None
            raise

    def _run(self, directory, args, *, credentials=None, cwd=None, capture_error=False):
        argv = [str(directory / 'lark-cli.exe'), *args]
        return self._execute(argv, credentials=credentials, cwd=cwd, capture_error=capture_error)

    def _execute(self, argv, *, credentials=None, cwd=None, capture_error=False, input_text=None):
        env = {key: val for key, val in os.environ.items() if key.upper() in _ENV_KEYS}
        env['LARKSUITE_CLI_CONFIG_DIR'] = str(self.root / 'cli-state')
        if credentials:
            env.update(LARKSUITE_CLI_APP_ID=credentials['app_id'], LARKSUITE_CLI_APP_SECRET=credentials['secret'],
                       LARKSUITE_CLI_BRAND='feishu', LARKSUITE_CLI_DEFAULT_AS='bot', LARKSUITE_CLI_STRICT_MODE='bot')
        with self.lock:
            if self.stopped.is_set():
                raise CommandError('lark_cancelled', '飞书 CLI 操作已停止')
            process = subprocess.Popen(argv, cwd=cwd or self.root, env=env,
                stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE if capture_error else subprocess.DEVNULL,
                encoding='utf-8', errors='replace', creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            self.processes.add(process)
        try:
            stdout, stderr = process.communicate(input_text, timeout=45)
            if process.returncode:
                if capture_error:
                    return next((stream for stream in (stdout, stderr)
                                 if isinstance(stream, str) and stream.lstrip().startswith('{')), '{}')
                raise CommandError('lark_cli_failed', '飞书 CLI 执行失败，可在重新检查后重试')
            return stdout
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise CommandError('lark_cli_timeout', '飞书 CLI 执行超时') from None
        finally:
            with self.lock:
                self.processes.discard(process)

    def stop(self):
        self.stopped.set()
        with self.lock:
            for process in self.processes:
                if process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
