"""Cooperative registration experiment; not kernel-enforced containment."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import select
import signal
import time


def case(name):
    # Harness holds cleanup only. Owner and broker each exclusively hold a lease.
    owner_r, owner_w = os.pipe()
    broker_r, broker_w = os.pipe()
    cleanup_r, cleanup_w = os.pipe()
    ready_r, ready_w = os.pipe()
    go_r, go_w = os.pipe()
    children = []
    reaped = set()
    result = {'case': name, 'harness_cleanup_counted': False}
    try:
        for role in ('owner', 'broker', 'worker'):
            pid = os.fork()
            if pid == 0:
                try:
                    keep = {cleanup_r}
                    if role == 'owner':
                        keep.add(owner_w)
                    elif role == 'broker':
                        keep.add(broker_w)
                    else:
                        keep.update((owner_r, broker_r, ready_w, go_r))
                    for fd in (owner_r, owner_w, broker_r, broker_w, cleanup_r, cleanup_w, ready_r, ready_w, go_r, go_w):
                        if fd not in keep:
                            os.close(fd)
                    if role != 'worker':
                        os.read(cleanup_r, 1)
                        os._exit(0)
                    os.setsid()
                    os.write(ready_w, b'R')  # registration before business work
                    watched = [cleanup_r, owner_r, broker_r, go_r]
                    started = False
                    while True:
                        readable, _, _ = select.select(watched, [], [])
                        if cleanup_r in readable:
                            os._exit(0)
                        # Death wins over GO when both are observable.
                        if owner_r in readable or broker_r in readable:
                            if name != 'lease_ignoring_negative':
                                os._exit(0)
                            watched = [cleanup_r, go_r] if not started else [cleanup_r]
                        if go_r in readable and not started:
                            if os.read(go_r, 1) != b'G':
                                os._exit(0)
                            started = True
                            os.write(ready_w, b'W')
                            watched.remove(go_r)
                finally:
                    os._exit(2)
            children.append(pid)
        for fd in (owner_r, owner_w, broker_r, broker_w, cleanup_r, ready_w, go_r):
            os.close(fd)
        if not select.select([ready_r], [], [], 5)[0] or os.read(ready_r, 1) != b'R':
            raise RuntimeError('registration_timeout')
        result['registered_before_work'] = True
        if name != 'owner_before_go':
            os.write(go_w, b'G')
            if not select.select([ready_r], [], [], 5)[0] or os.read(ready_r, 1) != b'W':
                raise RuntimeError('work_start_timeout')
        result['go_sent'] = name != 'owner_before_go'
        result['work_start_acknowledged'] = name != 'owner_before_go'
        started = time.monotonic()
        os.kill(children[1 if name == 'broker_killed' else 0], signal.SIGKILL)
        # EOF means worker closed its evidence descriptor, not kernel exit proof.
        readable = select.select([ready_r], [], [], 5)[0]
        closed = bool(readable) and os.read(ready_r, 1) == b''
        exited = False
        while closed and time.monotonic() - started < 5:
            waited, wait_status = os.waitpid(children[2], os.WNOHANG)
            if waited:
                reaped.add(children[2])
                result['worker_exit_code'] = os.waitstatus_to_exitcode(wait_status)
                exited = True
                break
            time.sleep(0.001)
        result.update(status=('observed_exit' if result['worker_exit_code'] == 0 else 'abnormal_exit') if exited else 'survived',
                      worker_reaped_before_cleanup=exited,
                      elapsed_seconds=time.monotonic() - started,
                      worker_evidence_closed=closed)
    except Exception as error:
        result.update(status='blocked', error=type(error).__name__)
    finally:
        os.close(cleanup_w)
        # Only own unreaped direct children: no reused PID or global-name targeting.
        deadline = time.monotonic() + 5
        pending = set(children) - reaped
        while pending and time.monotonic() < deadline:
            for pid in list(pending):
                if os.waitpid(pid, os.WNOHANG)[0]:
                    pending.remove(pid)
            if pending:
                time.sleep(0.01)
        result['harness_reaped_all'] = not pending
        for pid in pending:
            os.kill(pid, signal.SIGKILL)
        kill_deadline = time.monotonic() + 5
        while pending and time.monotonic() < kill_deadline:
            for pid in list(pending):
                if os.waitpid(pid, os.WNOHANG)[0]:
                    pending.remove(pid)
            if pending:
                time.sleep(0.01)
        result['fallback_remaining_children'] = len(pending)
        for fd in (owner_r, owner_w, broker_r, broker_w, cleanup_r, ready_r, ready_w, go_r, go_w):
            try:
                os.close(fd)
            except OSError:
                pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    report = {'status': 'blocked', 'results': [], 'architecture': platform.machine(),
              'os': platform.system(), 'release': platform.release(),
              'macos': platform.mac_ver()[0], 'python': platform.python_version(),
              'source_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'scope': 'cooperative direct-child fixtures only; not production containment',
              'limitations': ['No enforced spawn broker; registered worker can ignore leases.',
                              'No fork/exec inheritance guarantee or real GUI.',
                              'Evidence EOF is not precise kernel exit timing.',
                              'Owner and broker survivors are harness-cleaned, not candidate-cleaned.',
                              'No PID reuse/concurrency/handshake race acceptance.']}
    try:
        if platform.system() != 'Darwin' or platform.machine() != 'arm64':
            raise RuntimeError('native_apple_silicon_required')
        for name in ('owner_killed', 'broker_killed', 'owner_before_go', 'lease_ignoring_negative'):
            report['results'].append(case(name))
        expected = ['observed_exit', 'observed_exit', 'observed_exit', 'survived']
        report['status'] = 'incomplete' if all(
            r['status'] == e and r['harness_reaped_all']
            for r, e in zip(report['results'], expected)) else 'blocked'
    except Exception as error:
        report['error'] = type(error).__name__
    (args.output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
