"""Controlled package upgrade after the desktop releases its data-home lock."""
from pathlib import Path
from contextlib import closing
import sys
import json
import os
import shutil
import uuid
import sqlite3

from common import file_lock
from hostrun import dist
from hostrun.diagnostics import record_event


def _filesystem_path(path):
    path = Path(path).resolve()
    if os.name != 'nt' or str(path).startswith('\\\\?\\'):
        return path
    value = str(path)
    return Path('\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value)


def _restore(code, home, backup, code_backup, had_code):
    if not backup.is_dir() or (had_code and not code_backup.is_dir()):
        raise ValueError('Upgrade recovery backup is missing; original data has not been touched')
    record_event(home, 'recovery_started')
    for entry in home.iterdir():
        if entry.name in ('upgrade-backups', 'diagnostics', 'desktop.lock', 'desktop.activate', 'upgrade.pending.json'):
            continue
        if entry.is_symlink() or not entry.resolve().is_relative_to(home):
            raise ValueError('Unsafe restore path')
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    for entry in backup.iterdir():
        if entry.name == 'diagnostics':
            continue  # Recovery history is not transactional business state.
        destination = home / entry.name
        if entry.is_dir():
            shutil.copytree(entry, destination)
        else:
            shutil.copy2(entry, destination)
    failed_code = code.parent / (code.name + '.failed-upgrade-' + str(uuid.uuid4()))
    if code.exists():
        code.rename(failed_code)
    if had_code:
        shutil.copytree(code_backup, code)
    (home / 'upgrade.pending.json').unlink(missing_ok=True)
    record_event(home, 'recovery_completed', status='restored')


def _upgrade(code, home, *, from_source, sha256):
    code, home = _filesystem_path(code), _filesystem_path(home)
    if home.is_relative_to(code) or code.is_relative_to(home):
        return {'status': 'blocked', 'reason': 'overlapping_data_and_code'}
    with file_lock(home / 'desktop.lock', blocking=False) as acquired:
        if not acquired:
            return {'status': 'blocked', 'reason': 'desktop_running'}
        if _filesystem_path(sys.executable).is_relative_to(code):
            return {'status': 'blocked', 'reason': 'upgrader_inside_target'}
        pending = home / 'upgrade.pending.json'
        if pending.exists():
            record = json.loads(pending.read_text(encoding='utf-8'))
            if not isinstance(record, dict) or type(record.get('schema_version')) is not int or record['schema_version'] != 1:
                return {'status': 'blocked', 'reason': 'unsupported_recovery_schema'}
            identity = str(uuid.UUID(record['identity']))
            if _filesystem_path(record['code']) != code:
                return {'status': 'blocked', 'reason': 'different_pending_target'}
            _restore(code, home, home / 'upgrade-backups' / identity,
                     code.parent / (code.name + '.pre-upgrade-' + identity), record['had_code'])
        backup = home / 'upgrade-backups' / str(uuid.uuid4())
        backup.mkdir(parents=True)
        code_backup = code.parent / (code.name + '.pre-upgrade-' + backup.name)
        had_code = code.exists()
        if had_code:
            shutil.copytree(code, code_backup)
        for entry in home.iterdir():
            if entry.name in ('upgrade-backups', 'diagnostics', 'desktop.lock', 'desktop.activate', 'upgrade.pending.json'):
                continue
            if entry.name in ('horizon.sqlite3-wal', 'horizon.sqlite3-shm'):
                continue
            if entry.name == 'horizon.sqlite3':
                with closing(sqlite3.connect(entry)) as source, closing(sqlite3.connect(backup / entry.name)) as target:
                    source.backup(target)
                continue
            if entry.is_dir():
                shutil.copytree(entry, backup / entry.name)
            else:
                shutil.copy2(entry, backup / entry.name)
        record_event(home, 'backup_created', status='complete')
        pending_temp = home / 'upgrade.pending.json.tmp'
        with pending_temp.open('w', encoding='utf-8') as handle:
            json.dump({'schema_version': 1, 'identity': backup.name, 'code': str(code), 'had_code': had_code}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(pending_temp, pending)
        result = dist.install_package(code, from_source=from_source, sha256=sha256)
        if result['status'] == 'ok':
            from hostrun.agent_service import AgentService
            try:
                record_event(home, 'schema_migration', status='started')
                AgentService(home, token=str(uuid.uuid4()))
                record_event(home, 'schema_migration', status='complete')
                manifest = json.loads((code / 'manifest.json').read_text(encoding='utf-8'))
                owned = code / '.horizon/installer/owned-files.txt'
                owned.parent.mkdir(parents=True, exist_ok=True)
                owned.write_text('\n'.join(row['path'] for row in manifest['files']) + '\nmanifest.json\n', encoding='utf-8')
                marker = {'schema_version': 1, 'version': result['version'],
                          'backup': backup.relative_to(home).as_posix()}
                temporary = home / 'version.json.tmp'
                with temporary.open('w', encoding='utf-8') as handle:
                    json.dump(marker, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, home / 'version.json')
            except Exception:
                _restore(code, home, backup, code_backup, had_code)
                return {'status': 'blocked', 'reason': 'schema_upgrade_failed', 'recovery': 'restored'}
        else:
            _restore(code, home, backup, code_backup, had_code)
        pending.unlink(missing_ok=True)
        return result


def upgrade(code, home, *, from_source, sha256):
    from hostrun.cleanup import instance_lock
    with instance_lock(home) as acquired:
        if not acquired:
            return {'status': 'blocked', 'reason': 'instance_busy'}
        return _recorded_upgrade(code, home, from_source=from_source, sha256=sha256)


def _recorded_upgrade(code, home, *, from_source, sha256):
    code, home = _filesystem_path(code), _filesystem_path(home)
    # Invalid overlapping locations must not acquire diagnostic files either.
    if home.is_relative_to(code) or code.is_relative_to(home):
        return {'status': 'blocked', 'reason': 'overlapping_data_and_code'}
    record_event(home, 'upgrade_started', pid=os.getpid())
    try:
        result = _upgrade(code, home, from_source=from_source, sha256=sha256)
    except Exception as exc:
        record_event(home, 'result', status='error', code=type(exc).__name__,
                     exit_code=getattr(exc, 'winerror', None) or getattr(exc, 'errno', None))
        raise
    record_event(home, 'result', status=result['status'], code=result.get('reason', 'installed'),
                 version=result.get('version'))
    return result


def main():
    import argparse
    from hostrun.agent_service import desktop_home
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--package', required=True)
    parser.add_argument('--sha256', required=True)
    args = parser.parse_args()
    result = upgrade(args.code, desktop_home(), from_source=args.package, sha256=args.sha256)
    return 0 if result['status'] == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
