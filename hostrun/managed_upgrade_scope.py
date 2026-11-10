"""Private same-thread nesting of one managed upgrade's exclusive native lease."""
from contextlib import contextmanager
from contextvars import ContextVar
import os
import threading

from common import file_lock
from hostrun import cleanup
from hostrun.windows_cleanup import pin_ancestors

_owner = ContextVar('managed_upgrade_owner', default=None)


def owns_scope(home):
    return _owner.get() == (cleanup._lexical_home(home), os.getpid(), threading.get_ident())


@contextmanager
def exclusive_scope(home):
    home = cleanup._lexical_home(home)
    if owns_scope(home):
        yield True
        return
    control, identity = home.parent.parent, home.parent.name
    if control != cleanup.control_directory() or home.name != 'data':
        raise cleanup.InstanceInvalid('Unexpected managed home')
    cleanup.preview(control, identity)
    with file_lock(control / (identity + '.gate'), blocking=False) as gate, \
            cleanup.lifecycle_lock(control / (identity + '.lock'), exclusive=True) as lease:
        if not gate or not lease:
            yield False
            return
        with pin_ancestors(home):
            cleanup.preview(control, identity)
            token = _owner.set((home, os.getpid(), threading.get_ident()))
            try:
                yield True
            finally:
                _owner.reset(token)
