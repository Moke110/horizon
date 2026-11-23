"""Resolve and verify the Electron runtime used by Windows desktop launch."""
from contextlib import ExitStack, closing, contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

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
    root = Path(base) / 'Horizon/desktop-runtime'
    target = root / digest.hexdigest()
    # Published cache directories appear only after the complete staging copy is
    # atomically renamed. Reusing that immutable revision needs no write access.
    if (target / 'electron.exe').is_file():
        return target / 'electron.exe'
    root.mkdir(parents=True, exist_ok=True)
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


@contextmanager
def managed_executable(code, home):
    """Keep the installed, registered Electron files pinned for the desktop lifetime."""
    from hostrun import cleanup, dist
    from hostrun.windows_cleanup import PinnedEntry, io_path, pin_ancestors

    home, code = cleanup._lexical_home(home), cleanup._lexical_home(code)
    root, control = home.parent, home.parent.parent
    if home.name != 'data' or control != cleanup.control_directory() or code != root / 'app/current':
        raise cleanup.InstanceInvalid('Unexpected managed desktop application')
    with cleanup.instance_lock(home) as acquired, ExitStack() as held:
        if not acquired:
            raise cleanup.InstanceBusy('instance_busy')
        held.enter_context(pin_ancestors(code))
        record = cleanup._read_registration(control / (root.name + '.json'))
        files = record.get('application_inventory', {})
        directories = record.get('application_directories', {})
        pinned_directories = {code}

        def pin(path, *, directory):
            expected = (directories if directory else files).get(path.relative_to(root).as_posix())
            handle = held.enter_context(closing(PinnedEntry(path, read_only_share=not directory)))
            stat = io_path(path).stat()
            if (expected != [stat.st_dev, stat.st_ino] or handle.directory != directory
                    or (not directory and stat.st_nlink != 1)):
                raise cleanup.InstanceInvalid('Managed runtime entry identity changed')
            return stat

        def pin_directory(path):
            if path in pinned_directories:
                return
            pin_directory(path.parent)
            pin(path, directory=True)
            pinned_directories.add(path)

        def read_metadata(name):
            path = code / name
            pin_directory(path.parent)
            pin(path, directory=False)
            return io_path(path).read_bytes()

        manifest_bytes = read_metadata('manifest.json')
        manifest = json.loads(manifest_bytes)
        install_lock = json.loads(read_metadata('.horizon/dist.lock.json'))
        if (dist._validate_manifest(manifest) or not isinstance(install_lock, dict) or
                dist._lock_comparisons(manifest, hashlib.sha256(manifest_bytes).hexdigest(), install_lock)):
            raise cleanup.InstanceInvalid('Managed runtime package metadata is invalid')
        runtime = code / 'desktop/node_modules/electron/dist'
        prefix = runtime.relative_to(code).as_posix() + '/'
        entries = {code / row['path']: row for row in manifest['files'] if row['path'].startswith(prefix)}
        electron = runtime / 'electron.exe'
        if electron not in entries:
            raise cleanup.InstanceInvalid('Managed Electron runtime is missing from the package')

        children = {}
        for path, row in entries.items():
            pin_directory(path.parent)
            stat = pin(path, directory=False)
            with io_path(path).open('rb') as payload:
                digest = hashlib.file_digest(payload, 'sha256').hexdigest()
            if stat.st_size != row['bytes'] or digest != row['sha256']:
                raise cleanup.InstanceInvalid('Managed runtime file differs from the manifest')
            while path != runtime:
                children.setdefault(path.parent, set()).add(path.name)
                path = path.parent
        for directory, names in children.items():
            if {path.name for path in io_path(directory).iterdir()} != names:
                raise cleanup.InstanceInvalid('Managed runtime contains unregistered entries')
        yield electron
