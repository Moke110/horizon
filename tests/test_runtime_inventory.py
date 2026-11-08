import hashlib
import json
from pathlib import Path

import pytest

from hostrun.distribution import build, BuildError


def test_package_contains_verified_runtime_and_license_inventory(tmp_path):
    source, runtime = tmp_path / 'source', tmp_path / 'runtime'
    (source / 'hostrun').mkdir(parents=True)
    (source / 'hostrun/__init__.py').write_text('')
    runtime.mkdir()
    components = []
    for name, version in [('python', '3.13.7'), ('node', '24.16.0')]:
        binary = runtime / (name + '.exe')
        binary.write_bytes(name.encode())
        (runtime / (name + '-LICENSE.txt')).write_text('Fixture license')
        components.append({'name': name, 'version': version, 'executable': binary.name,
                           'sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
                           'license_files': [name + '-LICENSE.txt']})
    (runtime / 'components.json').write_text(json.dumps({'schema_version': 1, 'components': components}))
    metadata = source / 'agent/node_modules/example/package.json'
    metadata.parent.mkdir(parents=True)
    metadata.write_text(json.dumps({'name': 'example', 'version': '2.4.6', 'license': 'MIT'}))
    result = build(source=source, out=tmp_path / 'out', version='0.0.1', channel='stable',
                   runtime_dir=runtime, compat={'os': 'windows', 'arch': 'x86_64'})
    package = Path(result['package'])
    inventory = json.loads((package / 'runtime-components.json').read_text())
    assert [(c['name'], c['version']) for c in inventory['components']] == [('example', '2.4.6'), ('node', '24.16.0'), ('python', '3.13.7')]
    assert inventory['components'][0]['license'] == 'MIT'
    manifest = json.loads(Path(result['manifest']).read_text())
    assert 'runtime-components.json' in {f['path'] for f in manifest['files']}
    (runtime / 'node.exe').write_bytes(b'changed runtime')
    with pytest.raises(BuildError, match='digest'):
        build(source=source, out=tmp_path / 'bad', version='0.0.1', channel='stable', runtime_dir=runtime)


def test_desktop_distribution_requires_prepared_runtime_evidence(tmp_path):
    source, runtime = tmp_path / 'source', tmp_path / 'runtime'
    (source / 'desktop').mkdir(parents=True)
    (source / 'desktop/package.json').write_text('{"name":"desktop"}')
    runtime.mkdir()
    (runtime / 'python.exe').write_bytes(b'fixture')
    with pytest.raises(BuildError, match='components.json'):
        build(source=source, out=tmp_path / 'out', version='0.0.1', channel='stable', runtime_dir=runtime)
    (runtime / 'components.json').write_text('{"schema_version":1,"components":[]}')
    with pytest.raises(BuildError, match='Missing pinned'):
        build(source=source, out=tmp_path / 'incomplete', version='0.0.1', channel='stable', runtime_dir=runtime)


def test_preparation_records_binary_versions_and_rejects_unpinned_runtime(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from hostrun.runtime_inventory import record_runtime
    (tmp_path / 'python.exe').write_bytes(b'python fixture')
    (tmp_path / 'node.exe').write_bytes(b'node fixture')
    (tmp_path / 'LICENSE.txt').write_text('Python notices')
    node_license = tmp_path / 'node-notices.txt'
    node_license.write_text('Node notices')
    versions = {'python.exe': 'Python 3.13.7', 'node.exe': 'v24.16.0'}
    monkeypatch.setattr('subprocess.run', lambda args, **kw: SimpleNamespace(returncode=0, stdout=versions[Path(args[0]).name], stderr=''))
    record_runtime(tmp_path, node_license=node_license)
    prepared = json.loads((tmp_path / 'components.json').read_text())
    assert [c['version'] for c in prepared['components']] == ['3.13.7', '24.16.0']
    versions['node.exe'] = 'v25.0.0'
    with pytest.raises(ValueError, match='version'):
        record_runtime(tmp_path, node_license=node_license)
