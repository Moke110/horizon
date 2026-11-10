"""Owned desktop lock and one-shot activation signals for managed instances."""
import os
from pathlib import Path
from contextlib import contextmanager, ExitStack, closing

from common import file_lock
from hostrun.cleanup import (InstanceBusy, InstanceInvalid, _lexical_home, control_directory,
                             instance_lock, preview, _read_registration, _write_registration)


@contextmanager
def _managed(home):
    control = control_directory() if os.name == 'nt' else None
    if control is None or home.parent.parent != control:
        yield None
        return
    from hostrun.windows_cleanup import pin_ancestors
    identity = home.parent.name
    with instance_lock(home) as available:
        if not available:
            raise InstanceBusy('instance_busy')
        if 'data' not in preview(control, identity)['owned_children']:
            raise InstanceInvalid('Managed desktop layout is not initialized')
        with ExitStack() as held:
            try:
                held.enter_context(pin_ancestors(home))
            except (OSError, ValueError) as error:
                raise InstanceInvalid('Managed desktop path unavailable') from error
            yield control, identity


def _open_entry(home, control, identity, name, *, create, delete=False):
    """Caller holds ancestors and inventory lock; arbitrary names are forbidden."""
    if name not in ('desktop.lock', 'desktop.activate'):
        raise InstanceInvalid('Invalid desktop entry')
    from hostrun.windows_cleanup import PinnedEntry
    registration = control / (identity + '.json')
    record = _read_registration(registration)
    inventory = record.setdefault('data_inventory', {})
    relative = 'data/' + name
    path = home / name
    expected = inventory.get(relative)
    try:
        if not path.exists():
            if not create:
                return None
            with closing(PinnedEntry(home)) as parent:
                with closing(PinnedEntry(path, create_parent=parent, create_file=True)) as created:
                    stat = path.stat()
                    expected = [stat.st_dev, stat.st_ino]
                    inventory[relative] = expected
                    try:
                        _write_registration(registration, record)
                    except BaseException:
                        created.delete()
                        raise
        if expected is None:
            raise InstanceInvalid('Unowned desktop entry')
        pin = PinnedEntry(path, delete=delete)
        try:
            stat = path.stat()
            if expected != [stat.st_dev, stat.st_ino]:
                raise InstanceInvalid('Desktop entry identity changed')
            return pin
        except BaseException:
            pin.close()
            raise
    except OSError as error:
        raise InstanceInvalid('Managed desktop entry unavailable') from error


@contextmanager
def desktop_lock(home):
    home = _lexical_home(home)
    with _managed(home) as managed:
        if managed is None:
            home.mkdir(parents=True, exist_ok=True)
            with file_lock(home / 'desktop.lock', blocking=False) as acquired:
                yield acquired
            return
        control, identity = managed
        with ExitStack() as held:
            with file_lock(control / (identity + '.inventory.lock')):
                pin = _open_entry(home, control, identity, 'desktop.lock', create=True)
                held.callback(pin.close)
            with file_lock(home / 'desktop.lock', blocking=False) as acquired:
                yield acquired


def signal_activation(home):
    home = _lexical_home(home)
    with _managed(home) as managed:
        if managed is None:
            (home / 'desktop.activate').touch()
            return
        control, identity = managed
        with file_lock(control / (identity + '.inventory.lock')):
            with closing(_open_entry(home, control, identity, 'desktop.activate', create=True)):
                pass  # Presence is the coalesced one-shot signal.


def consume_activation(home):
    home = _lexical_home(home)
    if not (home / 'desktop.activate').exists():
        return False
    with _managed(home) as managed:
        if managed is None:
            (home / 'desktop.activate').unlink(missing_ok=True)
            return True
        control, identity = managed
        with file_lock(control / (identity + '.inventory.lock')):
            pin = _open_entry(home, control, identity, 'desktop.activate', create=False, delete=True)
            if pin is None:
                return False
            with closing(pin):
                pin.delete()
            return True
