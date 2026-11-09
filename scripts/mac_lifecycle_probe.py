"""Native candidate experiment, never production containment or acceptance."""
from contextlib import closing
import json
import os
import platform
import select
import signal
import sys
import time
from pathlib import Path


def announce(fd, role):
    os.write(fd, (json.dumps({'role': role, 'pid': os.getpid(),
                             'ppid': os.getppid()}) + '\n').encode())


def idle(cleanup):
    # Harness-only escape hatch. Its EOF is sent AFTER measurement, never success.
    os.read(cleanup, 1)
    os._exit(0)


def fixture(cleanup, evidence, escape):
    announce(evidence, 'owner')
    worker = os.fork()
    if worker == 0:
        os.setsid()
        if os.fork() == 0:
            announce(evidence, 'nested')
            idle(cleanup)
        if escape:
            intermediate = os.fork()
            if intermediate == 0:
                os.setsid()
                if os.fork() == 0:
                    announce(evidence, 'escaped')
                    idle(cleanup)
                os._exit(0)
            os.waitpid(intermediate, 0)
        announce(evidence, 'worker')
        idle(cleanup)
    watcher = os.fork()
    if watcher == 0:
        owner = os.getppid()
        with closing(select.kqueue()) as queue:
            queue.control([select.kevent(owner, filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT),
                select.kevent(cleanup, filter=select.KQ_FILTER_READ,
                flags=select.KQ_EV_ADD)], 0, 0)
            announce(evidence, 'helper')
            events = queue.control(None, 2, None)
            if any(event.filter == select.KQ_FILTER_PROC for event in events):
                os.killpg(worker, signal.SIGKILL)
        os._exit(0)
    idle(cleanup)


def run_case(case):
    cleanup_r, cleanup_w = os.pipe()
    evidence_r, evidence_w = os.pipe()
    owner = None
    result = {'case': case, 'status': 'blocked', 'targets': [],
              'harness_cleanup_counted_as_success': False}
    try:
        owner = os.fork()
        if owner == 0:
            os.close(cleanup_w)
            os.close(evidence_r)
            try:
                fixture(cleanup_r, evidence_w, case == 'owner_killed_with_escape')
            finally:
                os._exit(2)
        os.close(cleanup_r)
        cleanup_r = None
        os.close(evidence_w)
        evidence_w = None
        targets = {}
        buffer = b''
        deadline = time.monotonic() + 10
        while len(targets) < (5 if case == 'owner_killed_with_escape' else 4):
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([evidence_r], [], [], remaining)[0]:
                raise RuntimeError('fixture readiness timed out')
            data = os.read(evidence_r, 4096)
            if not data:
                raise RuntimeError('fixture exited before readiness')
            buffer += data
            while b'\n' in buffer:
                line, buffer = buffer.split(b'\n', 1)
                target = json.loads(line)
                targets[target['role']] = target
                result['targets'].append(target)
        # Register before fault injection. NOTE_EXIT also observes orphaned targets.
        with closing(select.kqueue()) as queue:
            queue.control([select.kevent(t['pid'], filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT) for t in targets.values()], 0, 0)
            pending = {t['pid'] for t in targets.values()}
            started = time.monotonic()
            if case in ('helper_then_owner_killed', 'helper_killed'):
                os.kill(targets['helper']['pid'], signal.SIGKILL)
            if case == 'helper_then_owner_killed':
                helper_pid = targets['helper']['pid']
                while helper_pid in pending:
                    remaining = 5 - (time.monotonic() - started)
                    if remaining <= 0:
                        raise RuntimeError('helper exit not observed before owner fault deadline')
                    for event in queue.control(None, 10, remaining):
                        pending.discard(event.ident)
                result['helper_exit_observed_before_owner_fault'] = True
            if case != 'helper_killed':
                os.kill(owner, signal.SIGKILL)
            while pending:
                remaining = 5 - (time.monotonic() - started)
                if remaining <= 0:
                    break
                for event in queue.control(None, 10, remaining):
                    pending.discard(event.ident)
            elapsed = time.monotonic() - started
            result.update(status='passed' if not pending and elapsed <= 5 else 'failed',
                          elapsed_seconds=elapsed, remaining_pids=sorted(pending),
                          timing='fault injection to last observed NOTE_EXIT, monotonic',
                          all_targets_exited_within_5s=not pending and elapsed <= 5)
    except Exception as error:
        result['error'] = f'{type(error).__name__}: {error}'
    finally:
        # Closing our sole writer releases every fixture, including setsid/doublefork.
        # Do not signal arbitrary numeric PIDs in this fallback.
        os.close(cleanup_w)
        if owner is not None and evidence_w is None:
            cleanup_deadline = time.monotonic() + 5
            eof = False
            while time.monotonic() < cleanup_deadline:
                if not select.select([evidence_r], [], [], max(0, cleanup_deadline - time.monotonic()))[0]:
                    break
                if not os.read(evidence_r, 4096):
                    eof = True
                    break
            result['harness_fixture_descriptors_closed'] = eof
            if not eof:
                result['status'] = 'blocked'
                result['cleanup_error'] = 'fixture evidence pipe did not reach EOF'
        for fd in (cleanup_r, evidence_r, evidence_w):
            if fd is not None:
                os.close(fd)
        if owner is not None:
            reap_deadline = time.monotonic() + 5
            while os.waitpid(owner, os.WNOHANG)[0] == 0:
                if time.monotonic() >= reap_deadline:
                    result['status'] = 'blocked'
                    result['cleanup_error'] = 'owner not reaped before harness deadline'
                    break
                time.sleep(0.01)  # Test harness only, never the candidate supervisor.
    return result


def main():
    report = {'platform': platform.platform(), 'architecture': platform.machine(),
              'candidate': 'single event-driven kqueue helper and worker process group',
              'results': [], 'status': 'blocked',
              'ci': {key: os.environ.get(key) for key in
                     ('GITHUB_SHA', 'GITHUB_REPOSITORY', 'GITHUB_RUN_ID',
                      'RUNNER_OS', 'RUNNER_ARCH', 'ImageOS', 'ImageVersion', 'SDKROOT')},
              'not_tested': ['real GUI', 'normal exit', 'handshake fault races',
                             'PID/PGID reuse', 'concurrent instances',
                             'unrelated-process sentinels', 'new-work prevention'],
              'scope': 'Candidate experiment only; no production or installation acceptance.',
              'limitations': ['Numeric PID identities are not a production ownership guarantee.',
                             'Harness EOF cleanup is excluded from candidate measurements.',
                             'Intermediate double-fork process exits before measurement.']}
    try:
        if sys.platform != 'darwin' or platform.machine() != 'arm64':
            raise RuntimeError('Requires native Apple Silicon macOS')
        for case in ('owner_killed', 'owner_killed_with_escape', 'helper_then_owner_killed', 'helper_killed'):
            report['results'].append(run_case(case))
        report['status'] = ('blocked' if any(r['status'] != 'passed' for r in report['results'])
                            else 'incomplete' if report['not_tested'] else 'passed')
    except Exception as error:
        report['error'] = f'{type(error).__name__}: {error}'
    finally:
        Path('mac-lifecycle-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
