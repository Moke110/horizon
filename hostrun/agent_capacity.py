"""Process-local execution slots shared by all contained desktop workers."""
import threading
from contextlib import contextmanager
from hostrun.agent_scheduling import get_limits
_condition=threading.Condition()
_active={}

@contextmanager
def child_slot(service, token, cancelled):
    key=str(service.home.resolve())
    with _condition:
        while _active.get(key,0)>=get_limits(service,token=token)['children']:
            if cancelled.is_set():raise RuntimeError('Child cancelled while waiting')
            _condition.wait(.1)
        if cancelled.is_set():raise RuntimeError('Child cancelled while waiting')
        _active[key]=_active.get(key,0)+1
    try:yield
    finally:
        with _condition:
            _active[key]-=1
            _condition.notify_all()
