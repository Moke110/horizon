"""Single Electron desktop entry point; Python owns the process job."""
import argparse
import ctypes
import os
import sys
from pathlib import Path
from common import file_lock
from hostrun import console, desktop_processes, layout
from hostrun.desktop_bridge import serve

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--code', required=True)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    resolved = layout.resolve(code=args.code, archive=args.archive)
    layout.ensure(resolved)
    layout.remember(resolved)
    layout.export(resolved)
    desktop_processes.install()
    home = Path(resolved['runtime'])
    with file_lock(home / 'desktop.lock', blocking=False) as acquired:
        if not acquired:
            (home / 'desktop.activate').touch()
            return
        console.install(home, resolved=resolved, python_path=str(Path(sys.executable).with_name('python.exe')))
        serve(resolved)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        import traceback
        path = Path(__file__).resolve().parents[1] / '.frontier/desktop-error.log'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(traceback.format_exc(), encoding='utf-8')
        if os.name == 'nt':
            ctypes.windll.user32.MessageBoxW(None, '错误详情：\n' + str(path), 'Frontier 启动失败', 0x10)
        raise
