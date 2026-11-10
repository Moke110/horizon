"""Single Electron desktop entry point; Python owns the process job."""
import argparse
import ctypes
import os
import sys
from pathlib import Path
from common import file_lock
from hostrun import console, desktop_processes, layout
from hostrun.desktop_bridge import serve
from hostrun.agent_service import desktop_home
from hostrun.diagnostics import record_event

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    home = desktop_home()
    from hostrun.cleanup import InstanceBusy, instance_lock
    with instance_lock(home) as acquired:
        if not acquired:
            raise InstanceBusy('instance_busy')
        _run(args, home)


def _run(args, home):
    home.mkdir(parents=True, exist_ok=True)
    resolved = {'code': str(Path(args.code).resolve()), 'runtime': str(home)}
    desktop_processes.install()
    with file_lock(home / 'desktop.lock', blocking=False) as acquired:
        if not acquired:
            (home / 'desktop.activate').touch()
            return
        if (home / 'upgrade.pending.json').exists():
            raise RuntimeError('An interrupted upgrade needs recovery. Run the Horizon installer again before opening the desktop.')
        record_event(home,'desktop_started',pid=os.getpid())
        try:
            serve(resolved)
        except Exception as exc:
            record_event(home,'error',code=type(exc).__name__,pid=os.getpid())
            raise
        finally:
            record_event(home,'desktop_exit',pid=os.getpid())

if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        from hostrun.cleanup import InstanceBusy, InstanceInvalid
        if isinstance(exc, (InstanceBusy, InstanceInvalid)):
            raise SystemExit(2)
        record_event(desktop_home(),'error',code=type(exc).__name__,pid=os.getpid())
        import traceback
        path = Path(__file__).resolve().parents[1] / '.horizon/desktop-error.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, '错误详情：\n' + str(path), 'Horizon 启动失败', 0x10)
        raise
