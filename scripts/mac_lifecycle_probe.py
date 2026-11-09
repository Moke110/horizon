"""Native experimental kqueue supervisor; not production containment."""
import json
import os
import platform
import select
import signal
import subprocess
import sys
from pathlib import Path


def helper(owner, worker):
    with select.kqueue() as queue:
        event = select.kevent(owner, filter=select.KQ_FILTER_PROC,
                             flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                             fflags=select.KQ_NOTE_EXIT)
        queue.control([event], 0, 0)
        print('ready', flush=True)
        queue.control(None, 1, None)
        os.killpg(worker, signal.SIGKILL)


def run_case(kill_helper):
    processes = []
    try:
        owner = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'])
        processes.append(owner)
        worker = subprocess.Popen([sys.executable, '-c', 'import signal; signal.pause()'],
                                  start_new_session=True)
        processes.append(worker)
        watcher = subprocess.Popen([sys.executable, __file__, 'helper', str(owner.pid),
                                    str(worker.pid)], stdout=subprocess.PIPE, text=True)
        processes.append(watcher)
        with select.kqueue() as ready_queue:
            ready_queue.control([select.kevent(watcher.stdout.fileno(),
                filter=select.KQ_FILTER_READ, flags=select.KQ_EV_ADD)], 0, 0)
            if not ready_queue.control(None, 1, 10):
                raise RuntimeError('helper readiness timed out')
        if watcher.stdout.readline().strip() != 'ready':
            raise RuntimeError('helper failed before readiness')
        if kill_helper:
            watcher.kill()
            watcher.wait(timeout=5)
        owner.kill()
        owner.wait(timeout=5)
        try:
            worker.wait(timeout=5)
            cleaned = True
        except subprocess.TimeoutExpired:
            cleaned = False
        return {'case': 'helper_then_owner_killed' if kill_helper else 'owner_killed',
                'worker_cleaned_within_5s': cleaned}
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)


def main():
    if sys.platform != 'darwin' or platform.machine() != 'arm64':
        raise SystemExit('Requires native Apple Silicon macOS')
    if len(sys.argv) > 1:
        helper(int(sys.argv[2]), int(sys.argv[3]))
        return 0
    results = [run_case(False), run_case(True)]
    report = {'platform': platform.platform(), 'architecture': platform.machine(),
              'candidate': 'single event-driven kqueue helper and dedicated worker group',
              'results': results,
              'status': 'blocked' if any(not r['worker_cleaned_within_5s'] for r in results) else 'incomplete',
              'not_tested': ['GUI', 'setsid/double-fork', 'PID reuse', 'concurrent instances'],
              'scope': 'Candidate experiment only; no production or installation acceptance.'}
    Path('mac-lifecycle-report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
