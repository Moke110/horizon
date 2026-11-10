"""Offline development artifact, not the complete Windows acceptance kit."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

from prepare_runtime import prepare_runtime

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('__init__', 'cleanup_cli', 'cleanup', 'windows_cleanup', 'windows_protection',
           'windows_process_cleanup', 'desktop_processes', 'diagnostics', 'managed_diagnostics',
           'dist', 'distribution', 'launcher', 'release', 'runtime_inventory')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        print(json.dumps({'status': 'blocked', 'reason': 'output_exists'}))
        return 2
    manifest = json.loads((ROOT / 'installer/cleanup-runtime-inputs.json').read_text(encoding='utf-8'))
    for entry in manifest['inputs']:
        source = args.inputs / entry['filename']
        if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != entry['sha256']:
            print(json.dumps({'status': 'blocked', 'reason': 'input_digest_mismatch'}))
            return 2
    args.output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(dir=args.output, prefix='build-wheels-') as temporary:
        wheels = Path(temporary)
        for entry in manifest['inputs']:
            if entry['kind'] == 'wheel':
                shutil.copyfile(args.inputs / entry['filename'], wheels / entry['filename'])
        embed = next(entry for entry in manifest['inputs'] if entry['kind'] == 'cpython-embed')
        prepare_runtime(args.inputs / embed['filename'], wheels, args.output / 'runtime',
                        python_version=manifest['python_version'], required_modules=('yaml',))
    (args.output / 'hostrun').mkdir()
    for module in MODULES:
        shutil.copyfile(ROOT / 'hostrun' / (module + '.py'), args.output / 'hostrun' / (module + '.py'))
    shutil.copyfile(ROOT / 'common.py', args.output / 'common.py')
    shutil.copyfile(ROOT / 'installer/cleanup-runtime-inputs.json', args.output / 'runtime-inputs.json')
    (args.output / 'cleanup-tool.cmd').write_bytes(
        b'@echo off\r\n"%~dp0runtime\\python.exe" -X utf8 -m hostrun.cleanup_cli %*\r\nexit /b %errorlevel%\r\n')
    files = {path.relative_to(args.output).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(args.output.rglob('*')) if path.is_file()}
    (args.output / 'tool-manifest.json').write_text(json.dumps({
        'schema_version': 1, 'purpose': 'development-cleanup-tool', 'scope_complete': False,
        'files': files, 'runtime_inputs': manifest}), encoding='utf-8')
    print(json.dumps({'status': 'assembled', 'output': str(args.output.resolve()), 'scope_complete': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
