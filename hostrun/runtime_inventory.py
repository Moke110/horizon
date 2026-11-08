"""Deterministic inventory of the runtime bytes and dependency metadata shipped."""
import hashlib
import json
import shutil
import subprocess
from email.parser import Parser
from pathlib import Path

from common import write_json_bytes

PINNED_VERSIONS = {'python': '3.13.7', 'node': '24.16.0',
                   'electron': '45.2.3', '@earendil-works/pi-coding-agent': '1.5.0'}


def record_runtime(runtime, *, node_license):
    """Probe the prepared binaries, binding exact versions to their file hashes."""
    runtime = Path(runtime).resolve()
    components = []
    for name, license_file in [('python', 'LICENSE.txt'), ('node', 'NODE-LICENSE.txt')]:
        binary = runtime / (name + '.exe')
        completed = subprocess.run([str(binary), '--version'], capture_output=True,
                                   text=True, encoding='utf-8', timeout=30,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        version = completed.stdout.strip().removeprefix('Python ').removeprefix('v')
        if completed.returncode or version != PINNED_VERSIONS[name]:
            raise ValueError('Unexpected bundled runtime version: ' + name)
        if name == 'node':
            shutil.copyfile(node_license, runtime / license_file)
        if not (runtime / license_file).is_file():
            raise ValueError('Missing runtime license: ' + license_file)
        with binary.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        components.append({'name': name, 'version': version, 'executable': binary.name,
                           'sha256': digest, 'license_files': [license_file]})
    write_json_bytes(runtime / 'components.json', {'schema_version': 1, 'components': components})


def _licenses(directory):
    return sorted({p for pattern in ('LICENSE*', 'LICENCE*', 'COPYING*', 'NOTICE*',
                                     'licenses/*', 'dist/LICENSE*')
                   for p in directory.glob(pattern) if p.is_file()})


def write_inventory(package):
    package = Path(package)
    runtime = package / 'runtime'
    prepared = runtime / 'components.json'
    if not prepared.exists():
        if (package / 'desktop/package.json').exists():
            raise ValueError('Full desktop runtime requires prepared runtime/components.json')
        return
    data = json.loads(prepared.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1:
        raise ValueError('Unsupported runtime component metadata')
    components = []
    for component in data['components']:
        if component['name'] in PINNED_VERSIONS and component['version'] != PINNED_VERSIONS[component['name']]:
            raise ValueError('Unpinned runtime version: ' + component['name'])
        executable = (runtime / component['executable']).resolve()
        if not executable.is_relative_to(runtime.resolve()):
            raise ValueError('Runtime executable escapes package')
        with executable.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if actual != component['sha256']:
            raise ValueError('Runtime component digest mismatch: ' + component['name'])
        licenses = []
        for reference in component['license_files']:
            path = (runtime / reference).resolve()
            if not path.is_relative_to(runtime.resolve()) or not path.is_file():
                raise ValueError('Missing or invalid runtime license: ' + reference)
            licenses.append(path.relative_to(package.resolve()).as_posix())
        if not licenses:
            raise ValueError('Runtime component requires its license notices')
        components.append({'name': component['name'], 'version': component['version'],
                           'location': executable.relative_to(package.resolve()).as_posix(),
                           'license': 'See bundled notices', 'license_files': sorted(licenses)})
    for root in (package / 'agent/node_modules', package / 'desktop/node_modules'):
        if not root.exists():
            continue
        for metadata in sorted(root.rglob('package.json')):
            parent = metadata.parent.parent
            if parent.name != 'node_modules' and not (parent.name.startswith('@') and parent.parent.name == 'node_modules'):
                continue
            item = json.loads(metadata.read_text(encoding='utf-8'))
            if not item.get('name') or not item.get('version'):
                continue
            if item['name'] in PINNED_VERSIONS and item['version'] != PINNED_VERSIONS[item['name']]:
                raise ValueError('Unpinned dependency version: ' + item['name'])
            components.append({'name': item['name'], 'version': item['version'],
                               'location': metadata.relative_to(package).as_posix(),
                               'license': item.get('license', 'Not declared in package metadata'),
                               'license_files': [p.relative_to(package).as_posix() for p in _licenses(metadata.parent)]})
    site = runtime / 'Lib/site-packages'
    for metadata in sorted(site.glob('*.dist-info/METADATA')):
        item = Parser().parsestr(metadata.read_text(encoding='utf-8'))
        components.append({'name': item['Name'], 'version': item['Version'],
                           'location': metadata.relative_to(package).as_posix(),
                           'license': item.get('License-Expression') or (item.get('License') or 'See package metadata')[:200],
                           'license_files': [p.relative_to(package).as_posix() for p in _licenses(metadata.parent)]})
    if (package / 'desktop/package.json').exists():
        missing = set(PINNED_VERSIONS) - {item['name'] for item in components}
        if missing:
            raise ValueError('Missing pinned runtime components: ' + ', '.join(sorted(missing)))
        electron_version = package / 'desktop/node_modules/electron/dist/version'
        if electron_version.read_text(encoding='utf-8').strip().removeprefix('v') != PINNED_VERSIONS['electron']:
            raise ValueError('Electron binary version differs from pinned package')
    components.sort(key=lambda item: (item['name'], item['version'], item['location']))
    write_json_bytes(package / 'runtime-components.json', {'schema_version': 1, 'components': components})


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--node-license', required=True)
    args = parser.parse_args()
    record_runtime(args.runtime, node_license=args.node_license)
