"""Preparation of isolated test instances; existing installations are never adopted."""
import json
from pathlib import Path
import uuid
import os
import base64
from contextlib import contextmanager, ExitStack


class InstanceBusy(RuntimeError):
    """Cleanup owns the instance; startup must not write its data."""


class InstanceInvalid(ValueError):
    """Instance validation failed before startup may write any data."""


CONTROL_MARKER = '.horizon-test-control.json'
CONTROL_SCHEMA = {'schema_version': 1, 'purpose': 'isolated-horizon-test-instances'}


def control_directory():
    base = os.environ.get('LOCALAPPDATA')
    if not base or not Path(base).is_absolute():
        raise InstanceInvalid('Missing Windows local application directory')
    return Path(base).resolve() / 'Horizon' / 'test-instances'


def _read_registration(registration):
    from hostrun.windows_protection import protect
    try:
        envelope = json.loads(registration.read_text(encoding='utf-8'))
        if set(envelope) != {'protected'}:
            raise ValueError('Unrecognized registration')
        sealed = base64.b64decode(envelope['protected'], validate=True)
        return json.loads(protect(sealed, str(registration).encode('utf-8'), decrypt=True))
    except (OSError, ValueError, TypeError) as exc:
        raise InstanceInvalid('Invalid protected registration') from exc


def _write_registration(registration, record):
    from hostrun.windows_protection import protect
    sealed = protect(json.dumps(record).encode('utf-8'), str(registration).encode('utf-8'))
    temporary = registration.with_name(registration.name + '.' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump({'protected': base64.b64encode(sealed).decode('ascii')}, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(registration)


def prepare_instance(control_root, *, protected_roots=()):
    """Allocate a fresh instance and retain its registration outside removable data."""
    candidate = Path(control_root).absolute()
    if candidate == Path(candidate.anchor) or candidate == Path.home():
        raise ValueError('Unsafe control root')
    control = candidate.resolve(strict=True)
    if control == Path(control.anchor) or control == Path.home():
        raise ValueError('Unsafe control root')
    if control != control_directory():
        raise ValueError('Unexpected control root')
    for protected in protected_roots:
        protected = Path(protected).resolve(strict=True)
        if control.is_relative_to(protected) or protected.is_relative_to(control):
            raise ValueError('Protected location overlap')
    marker = control / CONTROL_MARKER
    try:
        with marker.open('x', encoding='utf-8') as stream:
            json.dump(CONTROL_SCHEMA, stream)
    except FileExistsError:
        if json.loads(marker.read_text(encoding='utf-8')) != CONTROL_SCHEMA:
            raise InstanceInvalid('Invalid control root registration')
    identity = uuid.uuid4().hex
    root = control / identity
    root.mkdir()  # Exclusive creation: never claim an existing directory.
    registration = control / (identity + '.json')
    from hostrun.windows_protection import protect
    stat = root.stat()
    record = {'schema_version': 1, 'instance_id': identity, 'state': 'prepared',
              'volume': stat.st_dev, 'file_id': stat.st_ino}
    sealed = protect(json.dumps(record).encode('utf-8'), str(registration).encode('utf-8'))
    with registration.open('x', encoding='utf-8') as stream:
        json.dump({'protected': base64.b64encode(sealed).decode('ascii')}, stream)
    return {'status': 'prepared', 'instance_id': identity, 'root': root,
            'code': root / 'app', 'home': root / 'data',
            'archive': root / 'archive', 'registration': registration}


def preview(control_root, identity, *, mode='full'):
    """Describe fixed owned locations without executing any cleanup."""
    if mode not in ('full', 'keep-data'):
        raise ValueError('Unknown cleanup mode')
    if not isinstance(identity, str) or uuid.UUID(identity).hex != identity:
        raise ValueError('Invalid instance identity')
    control = Path(control_root).resolve(strict=True)
    if control != control_directory():
        raise InstanceInvalid('Unexpected control root')
    registration = control / (identity + '.json')
    if registration.is_symlink() or registration.is_junction():
        raise ValueError('Unsafe registration')
    record = _read_registration(registration)
    root = control / identity
    if root.is_symlink() or root.is_junction():
        raise ValueError('Unsafe instance root')
    children = record.pop('children', {})
    staging = record.pop('staging', None)
    staging_identity = record.pop('staging_identity', None)
    if staging is not None and (not isinstance(staging, str) or uuid.UUID(staging).hex != staging):
        raise InstanceInvalid('Invalid staging identity')
    if staging:
        stage = control / (identity + '.stage-' + staging)
        if stage.is_symlink() or stage.is_junction():
            raise InstanceInvalid('Linked staging identity')
        if stage.exists():
            stage_stat = stage.stat()
            if staging_identity != [stage_stat.st_dev, stage_stat.st_ino]:
                raise InstanceInvalid('Staging directory identity changed')
    stat = root.stat()
    if record != {'schema_version': 1, 'instance_id': identity, 'state': 'prepared',
                  'volume': stat.st_dev, 'file_id': stat.st_ino}:
        raise InstanceInvalid('Instance directory identity changed')
    if not isinstance(children, dict) or set(children) - {'app', 'data', 'archive'}:
        raise InstanceInvalid('Invalid child registration')
    for name, expected in children.items():
        child = root / name
        if child.is_symlink() or child.is_junction():
            raise InstanceInvalid('Linked child identity')
        if child.exists():
            child_stat = child.stat()
            if expected != [child_stat.st_dev, child_stat.st_ino]:
                raise InstanceInvalid('Child directory identity changed')
    delete = [root / 'app']
    preserve = [registration]
    data = [root / 'data', root / 'archive']
    (delete if mode == 'full' else preserve).extend(data)
    return {'status': 'confirmation_required', 'instance_id': identity,
            'mode': mode, 'delete': [str(p) for p in delete],
            'preserve': [str(p) for p in preserve], 'owned_children': sorted(children)}


def initialize_layout(control_root, identity):
    """Create and enroll fresh child directories; never adopt user contents."""
    preview(control_root, identity)
    control = Path(control_root).resolve(strict=True)
    root = control / identity
    from hostrun.windows_cleanup import pin_ancestors, PinnedEntry
    with lifecycle_lock(control / (identity + '.lock'), exclusive=True) as acquired, pin_ancestors(root), ExitStack() as held:
        if not acquired:
            raise InstanceBusy('instance_busy')
        preview(control, identity)
        registration = control / (identity + '.json')
        record = _read_registration(registration)
        if not record.get('children') and any(root.iterdir()):
            raise InstanceInvalid('Refusing existing instance contents')
        if not record.get('children'):
            staging = uuid.uuid4().hex
            stage = control / (identity + '.stage-' + staging)
            control_pin = PinnedEntry(control)
            held.callback(control_pin.close)
            stage_pin = PinnedEntry(stage, create_parent=control_pin)
            held.callback(stage_pin.close)
            children = {}
            new_pins = {}
            try:
                for name in ('app', 'data', 'archive'):
                    child = stage / name
                    child_pin = PinnedEntry(child, create_parent=stage_pin)
                    held.callback(child_pin.close)
                    new_pins[name] = child_pin
                    stat = child.stat()
                    children[name] = [stat.st_dev, stat.st_ino]
                stage_stat = stage.stat()
                record.update(children=children, staging=staging,
                              staging_identity=[stage_stat.st_dev, stage_stat.st_ino])
                _write_registration(registration, record)
            except BaseException:
                for child_pin in reversed(list(new_pins.values())):
                    try:
                        child_pin.delete()
                    except OSError:
                        pass  # Retain any nonempty or inaccessible unknown residue.
                try:
                    stage_pin.delete()
                except OSError:
                    pass
                raise
        else:
            new_pins = {}
        if record.get('staging'):
            stage = control / (identity + '.stage-' + record['staging'])
            if not new_pins and stage.exists():
                stage_pin = PinnedEntry(stage, delete=True)
                held.callback(stage_pin.close)
                preview(control, identity)
            elif not new_pins:
                stage_pin = None
            for name, expected in record['children'].items():
                if (root / name).exists():
                    continue  # preview above already verified the enrolled identity.
                child = stage / name
                with ExitStack() as handles:
                    if name in new_pins:
                        pin = new_pins[name]
                    else:
                        pin = PinnedEntry(child, delete=True)
                        handles.callback(pin.close)
                    if child.is_symlink() or child.is_junction():
                        raise InstanceInvalid('Linked staging child')
                    stat = child.stat()
                    if expected != [stat.st_dev, stat.st_ino]:
                        raise InstanceInvalid('Staging child identity changed')
                    pin.rename(root / name)
            if stage_pin is not None:
                stage_pin.delete()
            record.pop('staging')
            record.pop('staging_identity')
            _write_registration(registration, record)
    return preview(control, identity)


def collect_instance_report(control_root, identity, output):
    """Export allowlisted metadata with this tool's runtime, never the target app."""
    preview(control_root, identity)
    control = Path(control_root).resolve(strict=True)
    output = Path(output).resolve()
    if output.is_relative_to(control) or control.is_relative_to(output):
        raise InstanceInvalid('Unsafe report location')
    from hostrun.diagnostics import export_report
    root = control / identity
    report = export_report(root / 'data', output, code=root / 'app')
    import hashlib
    residuals = [hashlib.sha256(p.name.encode('utf-8')).hexdigest()
                 for p in control.glob(identity + '.stage-*')]
    (report / 'instance.json').write_text(json.dumps({
        'schema_version': 1, 'instance_id': identity,
        'status': 'collected', 'application_present': (root / 'app').is_dir(),
        'preserved_staging_residuals': residuals}), encoding='utf-8')
    return report


def cleanup_instance(control_root, identity, output, *, confirmation=None, mode='full'):
    plan = preview(control_root, identity, mode=mode)
    if confirmation != identity:
        return plan
    report = collect_instance_report(control_root, identity, output)
    control = Path(control_root).resolve(strict=True)
    root = control / identity
    result = {'status': 'blocked', 'report': str(report), 'removed': [], 'preserved': []}
    with lifecycle_lock(control / (identity + '.lock'), exclusive=True) as acquired:
        if not acquired:
            result['reason'] = 'instance_busy'
        else:
            plan = preview(control, identity, mode=mode)
            targets = []
            if mode == 'full' and 'data' in plan['owned_children']:
                for name in ('model-config.json', 'horizon.sqlite3', 'horizon.sqlite3-wal',
                             'horizon.sqlite3-shm', 'version.json'):
                    if (root / 'data' / name).exists():
                        targets.append('data/' + name)
            from hostrun.windows_cleanup import remove_owned_files
            try:
                remove_owned_files(root, targets, lambda: preview(control, identity), removed=result['removed'])
                result['preserved'] = [child.name for child in root.iterdir()]
                result['status'] = 'partial' if result['preserved'] else 'cleaned'
            except (OSError, ValueError) as exc:
                result.update(status='partial', reason=type(exc).__name__)
    (report / 'cleanup.json').write_text(json.dumps(result), encoding='utf-8')
    return result


@contextmanager
def instance_lock(home):
    """Serialize registered test instances using a lock outside removable data."""
    spelling = os.fspath(home)
    if spelling.startswith('\\\\?\\UNC\\'):
        spelling = '\\\\' + spelling[8:]
    elif spelling.startswith('\\\\?\\'):
        spelling = spelling[4:]
    home = Path(os.path.abspath(os.path.normpath(spelling)))
    root = home.parent
    registration = root.parent / (root.name + '.json')
    try:
        managed = home.name == 'data' and uuid.UUID(root.name).hex == root.name
    except ValueError:
        managed = False
    marker = root.parent / CONTROL_MARKER
    if os.name != 'nt' or root.parent != control_directory():
        yield True
        return
    if not managed:
        raise InstanceInvalid('Invalid managed instance location')
    try:
        for component in (home, root, root.parent):
            if component.is_symlink() or component.is_junction():
                raise ValueError('Linked managed instance location')
        if home.resolve() != home:
            raise ValueError('Managed instance path escaped')
        if json.loads(marker.read_text(encoding='utf-8')) != CONTROL_SCHEMA:
            raise ValueError('Invalid control root registration')
        if not registration.exists():
            raise ValueError('Missing instance registration')
        preview(root.parent, root.name)
    except (ValueError, OSError) as exc:
        raise InstanceInvalid('Invalid instance registration') from exc
    with ExitStack() as stack:
        try:
            acquired = stack.enter_context(lifecycle_lock(root.parent / (root.name + '.lock')))
        except OSError as exc:
            raise InstanceInvalid('Cannot acquire instance lease') from exc
        yield acquired


@contextmanager
def lifecycle_lock(path, *, exclusive=False):
    """Nonblocking shared use lease or exclusive cleanup lease."""
    with Path(path).open('a+b') as stream:
        if os.name != 'nt':
            import fcntl
            try:
                fcntl.flock(stream, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)
            return
        import ctypes
        from ctypes import wintypes as w
        import msvcrt
        class Overlapped(ctypes.Structure):
            _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t),
                        ('Offset', w.DWORD), ('OffsetHigh', w.DWORD), ('Event', w.HANDLE)]
        api = ctypes.WinDLL('kernel32', use_last_error=True)
        api.LockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)]
        api.UnlockFileEx.argtypes = [w.HANDLE, w.DWORD, w.DWORD, w.DWORD, ctypes.POINTER(Overlapped)]
        handle = msvcrt.get_osfhandle(stream.fileno())
        overlap = Overlapped()
        acquired = bool(api.LockFileEx(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlap)))
        if not acquired and ctypes.get_last_error() != 33:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            yield acquired
        finally:
            if acquired:
                api.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
