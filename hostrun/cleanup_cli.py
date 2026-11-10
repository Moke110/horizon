"""Independent Windows test tool entry; bundled runtime acceptance is still pending."""
import argparse
from contextlib import ExitStack, contextmanager
import json
from pathlib import Path
import sys

from hostrun import cleanup


@contextmanager
def _control(*, create=False):
    from hostrun.windows_cleanup import PinnedEntry, pin_ancestors
    control = cleanup.control_directory()
    tool = Path(__file__).resolve().parents[1]
    runtime = Path(sys.executable).resolve().parent
    for protected in (tool, runtime):
        if control.is_relative_to(protected) or protected.is_relative_to(control):
            raise cleanup.InstanceInvalid('Protected tool overlap')
    with ExitStack() as held:
        base = control.parent.parent
        held.enter_context(pin_ancestors(base))
        parent = PinnedEntry(base)
        held.callback(parent.close)
        for path in (control.parent, control):
            if create and not path.exists():
                pin = PinnedEntry(path, create_parent=parent, create_share=3, create_delete=False)
            else:
                pin = PinnedEntry(path)
            held.callback(pin.close)
            parent = pin
        yield control


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('prepare')
    install = commands.add_parser('install')
    install.add_argument('instance_id')
    install.add_argument('--package', required=True, type=Path)
    install.add_argument('--sha256', required=True)
    report = commands.add_parser('report')
    report.add_argument('instance_id')
    report.add_argument('--output', required=True, type=Path)
    for name in ('preview', 'cleanup'):
        command = commands.add_parser(name)
        command.add_argument('instance_id')
        command.add_argument('--mode', choices=('full', 'keep-data'), default='full')
        if name == 'cleanup':
            command.add_argument('--output', required=True, type=Path)
            confirmation = command.add_mutually_exclusive_group()
            confirmation.add_argument('--confirm')
            confirmation.add_argument('--interactive', action='store_true')
    args = parser.parse_args(argv)
    try:
        with _control(create=args.command == 'prepare') as control:
            if args.command == 'prepare':
                result = cleanup.prepare_instance(control)
                cleanup.initialize_layout(control, result['instance_id'])
            elif args.command == 'install':
                result = cleanup.install_test_package(control, args.instance_id, args.package, args.sha256)
            elif args.command == 'report':
                folder = cleanup.collect_instance_report(control, args.instance_id, args.output)
                metadata = json.loads((folder / 'instance.json').read_text(encoding='utf-8'))
                result = {'status': metadata['status'], 'report': folder}
            else:
                result = cleanup.preview(control, args.instance_id, mode=args.mode)
                result['containers'] = result.pop('delete')
                result['deletion_policy'] = 'Only registered matching file identities and empty registered directories; unknown content is preserved.'
                if args.command == 'cleanup' and args.interactive:
                    result['scope_complete'] = False
                    print(json.dumps(result, default=str), flush=True)
                    print('Review the scope above. Type the complete instance ID to continue; anything else cancels:',
                          file=sys.stderr, flush=True)
                    answer = sys.stdin.readline().strip()
                    if answer != args.instance_id:
                        print(json.dumps({'status': 'cancelled', 'scope_complete': False}))
                        return 0
                    args.confirm = answer
                if args.command == 'cleanup' and args.confirm is not None:
                    if args.confirm != args.instance_id:
                        raise cleanup.InstanceInvalid('Confirmation mismatch')
                    result['scope_complete'] = False
                    print(json.dumps(result, default=str), flush=True)
                    result = cleanup.cleanup_instance(control, args.instance_id, args.output,
                                                      confirmation=args.confirm, mode=args.mode)
        result['scope_complete'] = False
        print(json.dumps(result, default=str), flush=True)
        return 0 if result['status'] not in ('blocked', 'partial', 'incomplete') else 2
    except (OSError, ValueError, RuntimeError):
        print(json.dumps({'status': 'blocked', 'reason': 'validation_or_operation_failed', 'scope_complete': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
