"""Single Electron desktop entry point; Python owns the process job."""
import argparse
import ctypes
import os
import sys
from pathlib import Path
from hostrun.managed_desktop import desktop_lock, signal_activation
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
    resolved = {'code': str(Path(args.code).resolve()), 'runtime': str(home)}
    desktop_processes.install()
    with desktop_lock(home) as acquired:
        if not acquired:
            signal_activation(home)
            return
        if (home / 'upgrade.pending.json').exists():
            raise RuntimeError('An interrupted upgrade needs recovery. Run the Horizon installer again before opening the desktop.')
        from hostrun.cleanup import register_instance_owner
        register_instance_owner(home)
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
        from hostrun.cleanup import _lexical_home, control_directory
        home = _lexical_home(desktop_home())
        if os.name == 'nt' and home.parent.parent == control_directory():
            category = next((kind.__name__ for kind in (OSError, ValueError, RuntimeError)
                             if isinstance(exc, kind)), 'UnexpectedError')
            record_event(home, 'error', code=category, pid=os.getpid())
            from hostrun.diagnostics import export_report
            try:
                report = export_report(home)
                detail = '脱敏诊断报告：\n' + str(report)
            except (OSError, ValueError, RuntimeError):
                detail = '诊断导出未完成。请使用独立测试工具 report 命令收集诊断。'
            message = '启动失败（' + category + '）。\n' + detail
            print(message, file=sys.stderr)
            ctypes.windll.user32.MessageBoxW(None, message, 'Horizon 启动失败', 0x10)
            raise SystemExit(2)
        record_event(desktop_home(),'error',code=type(exc).__name__,pid=os.getpid())
        import traceback
        path = Path(__file__).resolve().parents[1] / '.horizon/desktop-error.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, '错误详情：\n' + str(path), 'Horizon 启动失败', 0x10)
        raise
