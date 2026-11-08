"""Assemble a local, manually transferred Windows installation test kit."""
import argparse
import hashlib
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--upgrade', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output directory so previous reports are preserved')
    for source in (args.baseline, args.upgrade):
        if not source.is_file():
            parser.error(f'Installer does not exist: {source}')
    args.output.mkdir(parents=True)
    checksums = []
    for source, name in ((args.baseline, '1-Horizon-baseline.exe'),
                         (args.upgrade, '2-Horizon-upgrade.exe')):
        target = args.output / name
        shutil.copyfile(source, target)
        with target.open('rb') as stream:
            checksums.append(f'{hashlib.file_digest(stream, "sha256").hexdigest()}  {name}')
    for source in (Path(__file__).resolve().parents[1] / 'installer/acceptance').iterdir():
        if source.is_file():
            shutil.copyfile(source, args.output / source.name)
    (args.output / 'SHA256.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
    print(args.output.resolve())


if __name__ == '__main__':
    main()
