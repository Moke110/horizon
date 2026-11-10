"""Preparation of isolated test instances; existing installations are never adopted."""
import json
from pathlib import Path
import uuid
import os
import base64
import re
from contextlib import contextmanager, ExitStack, closing
from common import file_lock
from hostrun.windows_cleanup import io_path


class InstanceBusy(RuntimeError):
    """Cleanup owns the instance; startup must not write its data."""


class InstanceInvalid(ValueError):
    """Instance validation failed before startup may write any data."""


CONTROL_MARKER = '.horizon-test-control.json'
CONTROL_SCHEMA = {'schema_version': 1, 'purpose': 'isolated-horizon-test-instances'}
SESSION_ID_PATTERN = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
SESSION_DIRECTORY_PATTERN = r'data/agent/pi/sessions/' + SESSION_ID_PATTERN
UPGRADE_DATA_PATTERN = r'data/(?:version\.json|horizon\.sqlite3|agent/model\.json|agent/pi/sessions/' + SESSION_ID_PATTERN + r'/session\.jsonl)'
UPGRADE_BACKUP_PATTERN = r'data/upgrade-backups/[0-9a-f]{32}/' + UPGRADE_DATA_PATTERN.removeprefix('data/')
UPGRADE_DIRECTORY_PATTERN = r'data/upgrade-backups(?:/[0-9a-f]{32}(?:/agent(?:/pi(?:/sessions(?:/' + SESSION_ID_PATTERN + r')?)?)?)?)?'


def _code_transaction_name(name):
    return (isinstance(name, str) and name and not Path(name).is_absolute()
            and '\\' not in name and ':' not in name and '..' not in Path(name).parts
            and (not name.startswith('.horizon/') or name in {'.horizon/current', '.horizon/dist.lock.json'}))


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
    stat = io_path(root).stat()
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
    if io_path(registration).is_symlink() or io_path(registration).is_junction():
        raise ValueError('Unsafe registration')
    record = _read_registration(registration)
    root = control / identity
    if io_path(root).is_symlink() or io_path(root).is_junction():
        raise ValueError('Unsafe instance root')
    children = record.pop('children', {})
    staging = record.pop('staging', None)
    staging_identity = record.pop('staging_identity', None)
    inventory = record.pop('application_inventory', {})
    application_directories = record.pop('application_directories', {})
    data_inventory = record.pop('data_inventory', {})
    data_directories = record.pop('data_directories', {})
    model_update = record.pop('model_update', None)
    snapshots = record.pop('data_backups', {})
    transactions = record.pop('code_transactions', {})
    if not isinstance(transactions, dict) or any(
            not re.fullmatch(r'[0-9a-f]{32}', name) or not isinstance(transaction, dict)
            or set(transaction) != {'before', 'after', 'digests'}
            or any(not isinstance(transaction[key], dict) for key in ('before', 'after', 'digests'))
            or set(transaction['digests']) != set(transaction['before'])
            or any(not _code_transaction_name(path) or not isinstance(value, list) or len(value) != 2
                   or any(type(number) is not int for number in value)
                   for group in ('before', 'after') for path, value in transaction[group].items())
            or any(not isinstance(digest, str) or not re.fullmatch(r'[0-9a-f]{64}', digest)
                   for digest in transaction['digests'].values())
            for name, transaction in transactions.items()):
        raise InstanceInvalid('Invalid code transaction')
    if not isinstance(snapshots, dict) or any(
            not re.fullmatch(r'[0-9a-f]{32}', name) or not isinstance(snapshot, dict)
            or set(snapshot) != {'complete', 'files'} or type(snapshot['complete']) is not bool
            or not isinstance(snapshot['files'], dict) or any(
                not re.fullmatch(UPGRADE_DATA_PATTERN, source) or not isinstance(digest, str)
                or not re.fullmatch(r'[0-9a-f]{64}', digest) for source, digest in snapshot['files'].items())
            for name, snapshot in snapshots.items()):
        raise InstanceInvalid('Invalid data backup inventory')
    upgrade = record.pop('upgrade', None)
    if upgrade is not None and (not isinstance(upgrade, str) or not re.fullmatch(r'[0-9a-f]{32}', upgrade)):
        raise InstanceInvalid('Invalid upgrade identity')
    operation = record.pop('upgrade_operation', None)
    if operation is not None and (not isinstance(operation, dict)
            or set(operation) not in ({'id', 'artifact'}, {'id', 'artifact', 'version'})
            or not isinstance(operation['id'], str) or not re.fullmatch(r'[0-9a-f]{32}', operation['id'])
            or not isinstance(operation['artifact'], str) or not re.fullmatch(r'[0-9a-f]{64}', operation['artifact'])):
        raise InstanceInvalid('Invalid overall upgrade operation')
    version = operation.get('version') if operation else None
    if version is not None and (not isinstance(version, dict)
            or set(version) != {'before', 'new', 'temporary', 'previous'}
            or version['temporary'] != 'data/version-' + operation['id'] + '.tmp'
            or not isinstance(version['previous'], str)
            or not re.fullmatch(r'data/version-[0-9a-f]{32}\.tmp', version['previous'])
            or version['previous'] == version['temporary']
            or any(value is not None and (not isinstance(value, list) or len(value) != 2
                or any(type(part) is not int or part < 0 for part in value))
                for value in (version['before'], version['new']))):
        raise InstanceInvalid('Invalid upgrade version journal')
    if not isinstance(data_inventory, dict) or any(name not in {'data/agent/model.json', 'data/diagnostics/events.jsonl', 'data/diagnostics/events.previous.jsonl', 'data/horizon.sqlite3', 'data/horizon.sqlite3-journal', 'data/desktop.lock', 'data/desktop.activate'}
            and not re.fullmatch(r'data/agent/model-[0-9a-f]{32}\.tmp', name)
            and not re.fullmatch(r'data/version(?:-[0-9a-f]{32}\.tmp|\.json)', name)
            and not re.fullmatch(UPGRADE_BACKUP_PATTERN, name)
            and not re.fullmatch(SESSION_DIRECTORY_PATTERN + r'/session\.jsonl', name) for name in data_inventory):
        raise InstanceInvalid('Invalid data inventory')
    if model_update is not None and (not isinstance(model_update, dict)
            or set(model_update) != {'temporary', 'backup', 'new', 'previous'}
            or not all(re.fullmatch(r'data/agent/model-[0-9a-f]{32}\.tmp', model_update[key]) for key in ('temporary', 'backup'))):
        raise InstanceInvalid('Invalid model update journal')
    if not isinstance(data_directories, dict) or any(name not in {'data/agent', 'data/agent/pi', 'data/agent/pi/sessions', 'data/diagnostics'}
            and not re.fullmatch(UPGRADE_DIRECTORY_PATTERN, name)
            and not re.fullmatch(SESSION_DIRECTORY_PATTERN, name) for name in data_directories):
        raise InstanceInvalid('Invalid data directory inventory')
    installation = record.pop('installation', None)
    if not isinstance(application_directories, dict):
        raise InstanceInvalid('Invalid application directory inventory')
    if installation is not None and (not isinstance(installation, dict) or (set(installation) != {'digest', 'identity'} and (set(installation) != {'digest', 'identity', 'protocol'} or installation.get('protocol') != 1))):
        raise InstanceInvalid('Invalid installation journal')
    if not isinstance(inventory, dict) or any(
        not isinstance(name, str) or not name.startswith('app/current/')
        or '\\' in name or '..' in Path(name).parts or ':' in name
        for name in inventory):
        raise InstanceInvalid('Invalid application inventory')
    if staging is not None and (not isinstance(staging, str) or uuid.UUID(staging).hex != staging):
        raise InstanceInvalid('Invalid staging identity')
    if staging:
        stage = control / (identity + '.stage-' + staging)
        if io_path(stage).is_symlink() or io_path(stage).is_junction():
            raise InstanceInvalid('Linked staging identity')
        if io_path(stage).exists():
            stage_stat = io_path(stage).stat()
            if staging_identity != [stage_stat.st_dev, stage_stat.st_ino]:
                raise InstanceInvalid('Staging directory identity changed')
    stat = io_path(root).stat()
    if record != {'schema_version': 1, 'instance_id': identity, 'state': 'prepared',
                  'volume': stat.st_dev, 'file_id': stat.st_ino}:
        raise InstanceInvalid('Instance directory identity changed')
    if not isinstance(children, dict) or set(children) - {'app', 'data', 'archive'}:
        raise InstanceInvalid('Invalid child registration')
    for name, expected in children.items():
        child = root / name
        if io_path(child).is_symlink() or io_path(child).is_junction():
            raise InstanceInvalid('Linked child identity')
        if io_path(child).exists():
            child_stat = io_path(child).stat()
            if expected != [child_stat.st_dev, child_stat.st_ino]:
                raise InstanceInvalid('Child directory identity changed')
    for relative, expected in {**application_directories, **data_directories}.items():
        if (not isinstance(relative, str) or '..' in Path(relative).parts or ':' in relative
                or not any(relative == prefix or relative.startswith(prefix + '/')
                           for prefix in ('app/current', 'data/agent', 'data/diagnostics', 'data/upgrade-backups'))):
            raise InstanceInvalid('Invalid enrolled directory path')
        directory = root / relative
        if io_path(directory).is_symlink() or io_path(directory).is_junction():
            raise InstanceInvalid('Linked enrolled directory')
        if io_path(directory).exists():
            directory_stat = io_path(directory).stat()
            if expected != [directory_stat.st_dev, directory_stat.st_ino]:
                raise InstanceInvalid('Enrolled directory identity changed')
    delete = [root / 'app']
    preserve = [registration]
    data = [root / 'data', root / 'archive']
    (delete if mode == 'full' else preserve).extend(data)
    candidates = set(inventory)
    directories = set(application_directories)
    if mode == 'full':
        candidates.update(data_inventory)
        directories.update(data_directories)
        if model_update:
            candidates.update((model_update['temporary'], model_update['backup']))
    return {'status': 'confirmation_required', 'instance_id': identity,
            'mode': mode, 'delete': [str(p) for p in delete],
            'registered_file_candidates': [str(root / name) for name in sorted(candidates)],
            'empty_directory_candidates': sorted({str(root / name) for name in directories} | {str(p) for p in delete if p.name in children}),
            'preserve': [str(p) for p in preserve], 'owned_children': sorted(children)}


def install_test_package(control_root, identity, package, digest):
    """Install a verified package with creation-time file identity receipts."""
    from hostrun.managed_install import install
    return install(control_root, identity, package, digest)


def initialize_layout(control_root, identity):
    """Create and enroll fresh child directories; never adopt user contents."""
    preview(control_root, identity)
    control = Path(control_root).resolve(strict=True)
    root = control / identity
    from hostrun.windows_cleanup import pin_ancestors, PinnedEntry
    with file_lock(control / (identity + '.gate'), blocking=False) as entrance, lifecycle_lock(control / (identity + '.lock'), exclusive=True) as acquired, pin_ancestors(root), ExitStack() as held:
        if not entrance or not acquired:
            raise InstanceBusy('instance_busy')
        preview(control, identity)
        registration = control / (identity + '.json')
        record = _read_registration(registration)
        if record.get('children') and not record.get('staging') and not any((root / name).exists() for name in record['children']):
            record.pop('children')
            for key in ('data_inventory', 'data_directories', 'model_update', 'application_inventory', 'application_directories', 'upgrade', 'upgrade_operation', 'data_backups', 'code_transactions', 'installation'):
                record.pop(key, None)
        if not record.get('children') and any(io_path(root).iterdir()):
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
                    stat = io_path(child).stat()
                    children[name] = [stat.st_dev, stat.st_ino]
                stage_stat = io_path(stage).stat()
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
            if not new_pins and io_path(stage).exists():
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
                    if io_path(child).is_symlink() or io_path(child).is_junction():
                        raise InstanceInvalid('Linked staging child')
                    stat = io_path(child).stat()
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
    with _instance_report(control_root, identity, output) as report:
        return report


@contextmanager
def _instance_report(control_root, identity, output):
    preview(control_root, identity)
    control = Path(control_root).resolve(strict=True)
    output = Path(os.path.abspath(output))
    if output.is_relative_to(control) or control.is_relative_to(output):
        raise InstanceInvalid('Unsafe report location')
    from hostrun.diagnostics import pinned_report_directory, _write_report
    with pinned_report_directory(output) as report:
        _collect_report_metadata(control, identity, report, _write_report)
        yield report


def _collect_report_metadata(control, identity, report, write_report):
    root = control / identity
    write_report(root / 'data', report, code=root / 'app', tasks=())
    record = _read_registration(control / (identity + '.json'))
    database = root / 'data/horizon.sqlite3'
    expected = record.get('data_inventory', {}).get('data/horizon.sqlite3')
    required = False
    if expected is not None and io_path(database).exists():
        stat = io_path(database).stat()
        required = expected == [stat.st_dev, stat.st_ino]
    snapshot = json.loads((report / 'tasks.json').read_text(encoding='utf-8'))
    missing = ['database_snapshot'] if required and not snapshot.get('available') else []
    import hashlib
    residuals = [hashlib.sha256(p.name.encode('utf-8')).hexdigest()
                 for p in control.glob(identity + '.stage-*')]
    (report / 'instance.json').write_text(json.dumps({
        'schema_version': 1, 'instance_id': identity,
        'status': 'incomplete' if missing else 'collected', 'missing': missing,
        'application_present': (root / 'app').is_dir(),
        'preserved_staging_residuals': residuals}), encoding='utf-8')


def cleanup_instance(control_root, identity, output, *, confirmation=None, mode='full'):
    plan = preview(control_root, identity, mode=mode)
    if confirmation != identity:
        return plan
    with _instance_report(control_root, identity, output) as report:
        return _cleanup_after_report(control_root, identity, report, mode)


def _cleanup_after_report(control_root, identity, report, mode):
    if json.loads((report / 'instance.json').read_text(encoding='utf-8'))['status'] == 'incomplete':
        result = {'status': 'blocked', 'reason': 'diagnostics_incomplete', 'report': str(report)}
        (report / 'cleanup.json').write_text(json.dumps(result), encoding='utf-8')
        return result
    control = Path(control_root).resolve(strict=True)
    with file_lock(control / (identity + '.gate'), blocking=False) as acquired:
        if not acquired:
            result = {'status': 'blocked', 'reason': 'instance_busy', 'report': str(report)}
        else:
            result = _cleanup_confirmed(control, identity, report, mode)
    (report / 'cleanup.json').write_text(json.dumps(result), encoding='utf-8')
    return result


def register_instance_owner(home):
    home = Path(home).resolve()
    control = control_directory()
    if home.parent.parent != control:
        return
    from hostrun.desktop_processes import has_owner_job
    if not has_owner_job():
        raise InstanceInvalid('Desktop containment is not established')
    identity = home.parent.name
    preview(control, identity)
    from hostrun.windows_process_cleanup import current_identity
    _write_registration(control / (identity + '.owner.json'), {
        'schema_version': 1, 'instance_id': identity, 'containment': 'kill_on_job_close',
        'process': current_identity()})


def _recover_model_update(home, registration, record):
    """Finish a published update, or restore the enrolled previous file."""
    update = record.get('model_update')
    if not update:
        return
    from hostrun.windows_cleanup import PinnedEntry
    relative = 'data/agent/model.json'
    root = home.parent
    with ExitStack() as held:
        pins = {}
        identities = {}
        for name in (relative, update['temporary'], update['backup']):
            path = root / name
            try:
                pin = PinnedEntry(path, delete=True)
            except FileNotFoundError:
                continue
            held.callback(pin.close)
            stat = io_path(path).stat()
            pins[name] = pin
            identities[name] = [stat.st_dev, stat.st_ino]
        for name, expected in ((update['temporary'], update['new']),
                               (update['backup'], update['previous'])):
            if name in identities and identities[name] != expected:
                raise InstanceInvalid('Interrupted model file identity changed')
        current = identities.get(relative)
        if current == update['new']:
            committed = update['new']
        elif current == update['previous'] and current is not None:
            committed = update['previous']
        elif current is None and update['previous'] is None:
            committed = None
        elif current is None and identities.get(update['backup']) == update['previous']:
            pins.pop(update['backup']).rename(root / relative)
            committed = update['previous']
        else:
            raise InstanceInvalid('Interrupted model configuration cannot be recovered')
        inventory = record['data_inventory']
        if committed is not None:
            inventory[relative] = committed
        else:
            inventory.pop(relative, None)
        # Keep the journal and old backup until the selected identity is durable.
        _write_registration(registration, record)
        for name in (update['temporary'], update['backup']):
            if name in pins:
                pins[name].delete()
            inventory.pop(name, None)
        record.pop('model_update')
        _write_registration(registration, record)


def load_managed_model_config(home):
    """Read the committed managed config under the publication/recovery lock."""
    if os.name != 'nt':
        return False, None, None
    home = _lexical_home(home)
    control = control_directory()
    if home.parent.parent != control:
        return False, None, None
    identity = home.parent.name
    from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
    with instance_lock(home) as available:
        if not available:
            raise InstanceBusy('instance_busy')
        with file_lock(control / (identity + '.inventory.lock')), pin_ancestors(home):
            preview(control, identity)
            registration = control / (identity + '.json')
            record = _read_registration(registration)
            expected = record.get('data_directories', {}).get('data/agent')
            if expected is None:
                if (home / 'agent').exists():
                    raise InstanceInvalid('Unowned model directory')
                return True, None, None
            with closing(PinnedEntry(home / 'agent')):
                stat = (home / 'agent').stat()
                if expected != [stat.st_dev, stat.st_ino]:
                    raise InstanceInvalid('Model directory identity changed')
                _recover_model_update(home, registration, record)
                path = home / 'agent' / 'model.json'
                expected = record.get('data_inventory', {}).get('data/agent/model.json')
                if expected is None:
                    if io_path(path).exists():
                        raise InstanceInvalid('Unowned model configuration')
                    return True, None, None
                with closing(PinnedEntry(path)):
                    stat = io_path(path).stat()
                    if expected != [stat.st_dev, stat.st_ino]:
                        raise InstanceInvalid('Model file identity changed')
                    return True, json.loads(path.read_text(encoding='utf-8')), expected


def save_managed_model_config(home, data, *, expected=None):
    """Persist model credentials only after enrolling the actual open file identity."""
    if os.name != 'nt':
        return False
    home = _lexical_home(home)
    control = control_directory()
    if home.parent.parent != control:
        return False
    identity = home.parent.name
    from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
    with instance_lock(home) as available:
        if not available:
            raise InstanceBusy('instance_busy')
        with file_lock(control / (identity + '.inventory.lock')), pin_ancestors(home), ExitStack() as held:
            preview(control, identity)
            registration = control / (identity + '.json')
            record = _read_registration(registration)
            directories = record.setdefault('data_directories', {})
            inventory = record.setdefault('data_inventory', {})
            folder = home / 'agent'
            if 'data/agent' not in directories:
                parent_pin = PinnedEntry(home)
                held.callback(parent_pin.close)
                folder_pin = PinnedEntry(folder, create_parent=parent_pin)
                held.callback(folder_pin.close)
                stat = io_path(folder).stat()
                directories['data/agent'] = [stat.st_dev, stat.st_ino]
                try:
                    _write_registration(registration, record)
                except BaseException:
                    folder_pin.delete()
                    raise
                folder_pin.close()
                folder_pin = PinnedEntry(folder)
                held.callback(folder_pin.close)
                preview(control, identity)
            else:
                folder_pin = PinnedEntry(folder)
                held.callback(folder_pin.close)
                stat = io_path(folder).stat()
                if directories['data/agent'] != [stat.st_dev, stat.st_ino]:
                    raise InstanceInvalid('Model directory identity changed')
            path = folder / 'model.json'
            relative = 'data/agent/model.json'
            _recover_model_update(home, registration, record)
            previous = inventory.get(relative)
            if expected is not None:
                if previous != expected[1]:
                    raise InstanceInvalid('Model configuration changed; retry the operation')
                current = {}
                if previous is not None:
                    with closing(PinnedEntry(path)):
                        stat = io_path(path).stat()
                        if previous != [stat.st_dev, stat.st_ino]:
                            raise InstanceInvalid('Model file identity changed')
                        current = json.loads(path.read_text(encoding='utf-8'))
                if current != expected[0]:
                    raise InstanceInvalid('Model configuration changed; retry the operation')
            if previous is not None:
                old_pin = PinnedEntry(path, delete=True)
                held.callback(old_pin.close)
                stat = io_path(path).stat()
                if previous != [stat.st_dev, stat.st_ino]:
                    raise InstanceInvalid('Model file identity changed')
            elif io_path(path).exists():
                raise InstanceInvalid('Unowned model configuration')
            temporary = folder / ('model-' + uuid.uuid4().hex + '.tmp')
            backup = folder / ('model-' + uuid.uuid4().hex + '.tmp')
            model_pin = PinnedEntry(temporary, create_parent=folder_pin, create_file=True, writable=True)
            held.callback(model_pin.close)
            stat = io_path(temporary).stat()
            file_identity = [stat.st_dev, stat.st_ino]
            temp_relative = temporary.relative_to(home.parent).as_posix()
            backup_relative = backup.relative_to(home.parent).as_posix()
            inventory[temp_relative] = file_identity
            if previous is not None:
                inventory[backup_relative] = previous
            record['model_update'] = {'temporary': temp_relative, 'backup': backup_relative,
                                      'new': file_identity, 'previous': previous}
            published = False
            old_moved = False
            try:
                _write_registration(registration, record)
                model_pin.write_bytes(json.dumps(data, ensure_ascii=False).encode('utf-8'))
                if previous is not None:
                    old_pin.rename(backup)
                    old_moved = True
                model_pin.rename(path)
                published = True
                inventory[relative] = file_identity
                _write_registration(registration, record)
                if old_moved:
                    old_pin.delete()
                inventory.pop(temp_relative)
                inventory.pop(backup_relative, None)
                record.pop('model_update')
                _write_registration(registration, record)
            except BaseException:
                if not published:
                    if old_moved:
                        old_pin.rename(path)
                    model_pin.delete()
                    inventory.pop(temp_relative, None)
                    inventory.pop(backup_relative, None)
                    record.pop('model_update', None)
                    _write_registration(registration, record)
                raise
    return True


def _cleanup_confirmed(control, identity, report, mode):
    root = control / identity
    result = {'status': 'blocked', 'report': str(report), 'removed': [], 'preserved': []}
    owner_record = control / (identity + '.owner.json')
    if io_path(owner_record).exists():
        try:
            owner = _read_registration(owner_record)
            if (set(owner) != {'schema_version', 'instance_id', 'containment', 'process'}
                    or owner['schema_version'] != 1 or owner['instance_id'] != identity
                    or owner['containment'] != 'kill_on_job_close'
                    or set(owner['process']) != {'pid', 'created', 'image_path_digest'}):
                raise InstanceInvalid('Invalid owner record')
            from hostrun.windows_process_cleanup import stop_owner
            result['processes'] = stop_owner(owner['process'])
            if result['processes']['status'] == 'blocked':
                result['reason'] = result['processes']['reason']
                return result
        except (OSError, ValueError, TypeError) as exc:
            result['reason'] = type(exc).__name__
            return result
    with lifecycle_lock(control / (identity + '.lock'), exclusive=True) as acquired:
        if not acquired:
            result['reason'] = 'instance_busy'
        else:
            plan = preview(control, identity, mode=mode)
            targets = []
            record = _read_registration(control / (identity + '.json'))
            identities = dict(record.get('application_inventory', {}))
            if mode == 'full':
                identities.update(record.get('data_inventory', {}))
                update = record.get('model_update')
                if update and (root / 'data/agent/model.json').exists():
                    stat = (root / 'data/agent/model.json').stat()
                    if update['new'] == [stat.st_dev, stat.st_ino]:
                        identities['data/agent/model.json'] = update['new']
            for name, expected in identities.items():
                path = root / name
                if io_path(path).exists():
                    stat = io_path(path).stat()
                    if expected == [stat.st_dev, stat.st_ino]:
                        targets.append(name)
            from hostrun.windows_cleanup import remove_owned_files, remove_empty_children, remove_empty_inventory
            try:
                remove_owned_files(root, targets, lambda: preview(control, identity),
                                   removed=result['removed'], identities=identities)
                directory_inventory = dict(record.get('application_directories', {}))
                if mode == 'full':
                    directory_inventory.update(record.get('data_directories', {}))
                remove_empty_inventory(root, directory_inventory,
                                       lambda: preview(control, identity), result['removed'])
                directories = [name for name in plan['owned_children'] if mode == 'full' or name == 'app']
                remove_empty_children(root, directories, lambda: preview(control, identity), result['removed'])
                result['preserved'] = [child.name for child in io_path(root).iterdir()]
                expected_preserved = {'data', 'archive'} if mode == 'keep-data' else set()
                unexpected = set(result['preserved']) - expected_preserved
                result['status'] = 'partial' if unexpected else ('uninstalled' if mode == 'keep-data' else 'cleaned')
            except (OSError, ValueError) as exc:
                result.update(status='partial', reason=type(exc).__name__)
    return result


def _lexical_home(home):
    spelling = os.fspath(home)
    if spelling.startswith('\\\\?\\UNC\\'):
        spelling = '\\\\' + spelling[8:]
    elif spelling.startswith('\\\\?\\'):
        spelling = spelling[4:]
    return Path(os.path.abspath(os.path.normpath(spelling)))


@contextmanager
def instance_lock(home):
    """Serialize registered test instances using a lock outside removable data."""
    home = _lexical_home(home)
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
            if io_path(component).is_symlink() or io_path(component).is_junction():
                raise ValueError('Linked managed instance location')
        if home.resolve() != home:
            raise ValueError('Managed instance path escaped')
        if json.loads(marker.read_text(encoding='utf-8')) != CONTROL_SCHEMA:
            raise ValueError('Invalid control root registration')
        if not io_path(registration).exists():
            raise ValueError('Missing instance registration')
        preview(root.parent, root.name)
        pending_record = _read_registration(registration)
        if any(pending_record.get(key) for key in ('upgrade', 'upgrade_operation', 'installation')):
            raise InstanceInvalid('Managed upgrade recovery pending')
    except (ValueError, OSError) as exc:
        raise InstanceInvalid('Invalid instance registration') from exc
    with ExitStack() as stack:
        try:
            with file_lock(root.parent / (root.name + '.gate'), blocking=False) as entrance:
                acquired = entrance and stack.enter_context(lifecycle_lock(root.parent / (root.name + '.lock')))
                if acquired:
                    preview(root.parent, root.name)
                    pending_record = _read_registration(registration)
                    if any(pending_record.get(key) for key in ('upgrade', 'upgrade_operation', 'installation')):
                        raise InstanceInvalid('Managed upgrade recovery pending')
        except (OSError, ValueError) as exc:
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
