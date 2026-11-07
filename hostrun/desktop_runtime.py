"""Deploy Electron binaries outside workspace filesystem restrictions on Windows."""
import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from common import file_lock


def executable(code, *, local_app_data=None):
    source = Path(code) / 'desktop/node_modules/electron/dist'
    if not (source / 'electron.exe').is_file():
        raise RuntimeError('Desktop runtime missing: run npm ci and npm run build in desktop/')
    # Chromium initializes its Windows sandbox before application JS executes.
    # Running from some workspace volumes fails even with intact binaries.
    # Copy only immutable runtime files; app code and user data stay in place.
    digest = hashlib.sha256()
    for path in sorted(source.rglob('*')):
        if path.is_file():
            digest.update(path.relative_to(source).as_posix().encode())
            with path.open('rb') as stream:
                digest.update(hashlib.file_digest(stream, 'sha256').digest())
    base = local_app_data or os.environ.get('LOCALAPPDATA')
    if not base:
        raise RuntimeError('LOCALAPPDATA is required for the Windows desktop runtime')
    root = Path(base) / 'Frontier/desktop-runtime'
    root.mkdir(parents=True, exist_ok=True)
    target = root / digest.hexdigest()
    with file_lock(root / 'install.lock', blocking=True):
        if not (target / 'electron.exe').is_file():
            staging = Path(tempfile.mkdtemp(prefix='install-', dir=root))
            try:
                shutil.copytree(source, staging, dirs_exist_ok=True)
                staging.rename(target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
    return target / 'electron.exe'
