import json
import os
from pathlib import Path
import subprocess
import sys
import shutil

import pytest

from hostrun import cleanup


@pytest.fixture
def installed(tmp_path, monkeypatch):
    from test_dist_install import _write_tree, PACKAGE_FILES, _host_compat
    from hostrun.distribution import build
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'local'))
    control = cleanup.control_directory()
    control.mkdir(parents=True)
    instance = cleanup.prepare_instance(control)
    cleanup.initialize_layout(control, instance['instance_id'])
    files = {**PACKAGE_FILES, 'hostrun/desktop.py': 'DESKTOP = True\n'}
    runtime = _write_tree(tmp_path / 'runtime', {'python.exe': 'MZ fixture', 'pythonw.exe': 'MZ shortcut target fixture'})
    package = build(source=_write_tree(tmp_path / 'source', files), out=tmp_path / 'package',
                    version='0.1.0', channel='stable', commit='test', compat=_host_compat(), runtime_dir=runtime)
    assert cleanup.install_test_package(control, instance['instance_id'], package['artifact']['path'], package['artifact']['sha256'])['status'] == 'ok'
    return control, instance


@pytest.mark.parametrize('mode', ['full', 'keep-data'])
def test_managed_shortcut_has_bound_target_and_is_cleaned_with_instance(installed, tmp_path, mode):
    control, instance = installed
    result = cleanup.create_test_shortcut(control, instance['instance_id'])
    link = instance['code'] / 'current/.horizon/test-launch.lnk'
    assert result['status'] == 'created'
    assert Path(result['shortcut']) == link
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    probe = subprocess.run([str(powershell), '-NoProfile', '-Command',
                            '$l=(New-Object -ComObject WScript.Shell).CreateShortcut($env:HORIZON_LINK_PROBE); '
                            '@{target=$l.TargetPath;arguments=$l.Arguments;cwd=$l.WorkingDirectory}|ConvertTo-Json -Compress'],
                           env={**os.environ, 'HORIZON_LINK_PROBE': str(link)}, capture_output=True, text=True, timeout=15)
    assert probe.returncode == 0, probe.stderr
    values = json.loads(probe.stdout)
    assert Path(values['target']) == instance['code'] / 'current/runtime/pythonw.exe'
    assert values['arguments'].startswith('-B -X utf8 -m hostrun.desktop ')
    assert f'--managed-home "{instance["home"]}"' in values['arguments']
    assert Path(values['cwd']) == instance['code'] / 'current'
    identity = link.stat().st_ino
    assert cleanup.create_test_shortcut(control, instance['instance_id'])['status'] == 'already_created'
    assert link.stat().st_ino == identity
    result = cleanup.cleanup_instance(control, instance['instance_id'], tmp_path / 'reports',
                                      confirmation=instance['instance_id'], mode=mode)
    assert result['status'] == ('cleaned' if mode == 'full' else 'uninstalled'), result
    assert not link.exists()


def test_shortcut_full_cleanup_reinitialize_reinstall_has_no_stale_receipt(installed, tmp_path):
    import hashlib
    control, instance = installed
    identity = instance['instance_id']
    cleanup.create_test_shortcut(control, identity)
    assert cleanup.cleanup_instance(control, identity, tmp_path / 'reports', confirmation=identity)['status'] == 'cleaned'
    cleanup.initialize_layout(control, identity)
    package = next((tmp_path / 'package').glob('*.zip'))
    assert cleanup.install_test_package(control, identity, package, hashlib.sha256(package.read_bytes()).hexdigest())['status'] == 'ok'
    assert cleanup.create_test_shortcut(control, identity)['status'] == 'created'


def test_shortcut_survives_public_upgrade_with_same_identity(installed, tmp_path):
    from test_dist_install import _write_tree, PACKAGE_FILES, _host_compat
    from hostrun.distribution import build
    from hostrun.agent_service import AgentService
    from hostrun.desktop_upgrade import upgrade
    control, instance = installed
    cleanup.create_test_shortcut(control, instance['instance_id'])
    code = instance['code'] / 'current'
    link = code / '.horizon/test-launch.lnk'
    before = link.stat().st_ino, link.read_bytes()
    AgentService(instance['home'], token='test')
    module = (Path(__file__).resolve().parents[1] / 'hostrun/agent_service.py').read_text(encoding='utf-8')
    package = build(source=_write_tree(tmp_path / 'upgrade-source', {**PACKAGE_FILES,
                    'hostrun/desktop.py': 'DESKTOP = True\n', 'hostrun/agent_service.py': module}),
                    out=tmp_path / 'upgrade-package', version='0.2.0', channel='stable', commit='test',
                    compat=_host_compat(), runtime_dir=tmp_path / 'runtime')
    result = upgrade(code, instance['home'], from_source=package['artifact']['path'], sha256=package['artifact']['sha256'])
    assert result['status'] == 'ok', result
    assert (link.stat().st_ino, link.read_bytes()) == before
    assert cleanup.create_test_shortcut(control, instance['instance_id'])['status'] == 'already_created'


@pytest.mark.parametrize('refusal', [None, 'foreign', 'code', 'pending'])
def test_explicit_managed_desktop_home_is_bound_before_any_home_writes(installed, tmp_path, refusal):
    control, instance = installed
    code = instance['code'] / 'current'
    home = instance['home']
    poisoned = tmp_path / 'poisoned-home'
    poisoned.mkdir()
    (poisoned / 'personal.txt').write_text('preserved')
    if refusal == 'pending':
        stat = code.stat()
        record = cleanup._read_registration(instance['registration'])
        record['installation'] = {'digest': '0' * 64, 'identity': [stat.st_dev, stat.st_ino]}
        cleanup._write_registration(instance['registration'], record)
    script = '''
import json,runpy
from hostrun import desktop_bridge
desktop_bridge.serve=lambda resolved: print(json.dumps({'bound':resolved['runtime']}),flush=True)
runpy.run_module('hostrun.desktop',run_name='__main__')
'''
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script,
                              '--code', str(poisoned if refusal == 'code' else code),
                              '--archive', str(instance['archive']), '--managed-home', str(poisoned if refusal == 'foreign' else home)],
                             env={**os.environ, 'HORIZON_DATA_HOME': str(poisoned)}, capture_output=True,
                             text=True, encoding='utf-8', timeout=15)
    assert process.returncode == (2 if refusal else 0), process.stdout + process.stderr
    if refusal:
        assert not list(home.iterdir())
    else:
        assert json.loads(process.stdout)['bound'] == str(home)
    assert sorted(path.name for path in poisoned.iterdir()) == ['personal.txt']
    assert (poisoned / 'personal.txt').read_text() == 'preserved'


@pytest.mark.parametrize('embedded', [False, True])
def test_shortcut_cli_uses_fixed_instance_location(installed, tmp_path, embedded):
    control, instance = installed
    tool = tmp_path / 'independent-tool'
    if embedded:
        if not os.environ.get('HORIZON_CLEANUP_TEST_INPUTS'):
            pytest.skip('explicit pinned offline runtime inputs required')
        built = subprocess.run([sys.executable, '-B', '-X', 'utf8', 'scripts/prepare_cleanup_tool.py', '--inputs',
                                os.environ['HORIZON_CLEANUP_TEST_INPUTS'], '--output', str(tool)],
                               capture_output=True, text=True, timeout=30)
        assert built.returncode == 0, built.stderr
        python = str(tool / 'runtime/python.exe')
    else:
        repository = Path(__file__).resolve().parents[1]
        shutil.copytree(repository / 'hostrun', tool / 'hostrun', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(repository / 'common.py', tool / 'common.py')
        python = sys.executable
    result = subprocess.run([python, '-B', '-X', 'utf8', '-m', 'hostrun.cleanup_cli',
                             'shortcut', instance['instance_id']], cwd=tool,
                            env={**os.environ, **({'PATH': os.environ['SystemRoot'] + '/System32',
                                 'PYTHONHOME': 'invalid-inherited-python', 'PYTHONPATH': 'invalid-inherited-python'} if embedded else {})},
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)['status'] == 'created'
    assert (instance['code'] / 'current/.horizon/test-launch.lnk').is_file()


def test_shortcut_refuses_parent_replacement_before_pin(installed, monkeypatch):
    import ctypes
    from hostrun.windows_cleanup import io_path
    control, instance = installed
    parent = instance['code'] / 'current/.horizon'
    original = ctypes.WinDLL
    kernel = original('kernel32', use_last_error=True)
    raced = []
    def create_file(path, *args):
        kernel.CreateFileW.argtypes = create_file.argtypes
        kernel.CreateFileW.restype = create_file.restype
        if path == str(io_path(parent)) and not raced:
            parent.rename(parent.with_name('retained-metadata'))
            parent.mkdir()
            raced.append(True)
        return kernel.CreateFileW(path, *args)
    class Proxy:
        def __getattr__(self, name):
            return create_file if name == 'CreateFileW' else getattr(kernel, name)
    monkeypatch.setattr(ctypes, 'WinDLL', lambda name, *args, **kwargs: Proxy() if name == 'kernel32' else original(name, *args, **kwargs))
    with pytest.raises(cleanup.InstanceInvalid):
        cleanup.create_test_shortcut(control, instance['instance_id'])
    assert raced == [True]
    assert not list(parent.iterdir())


@pytest.mark.parametrize('phase', ['before_receipt', 'after_receipt', 'partial_write', 'after_write'])
def test_shortcut_crash_preserves_unknown_and_cleans_only_receipted_file(installed, tmp_path, phase):
    control, instance = installed
    link = instance['code'] / 'current/.horizon/test-launch.lnk'
    script = '''
import ctypes,os,sys
from pathlib import Path
from hostrun.cleanup import create_test_shortcut
phase=sys.argv[3]
replace=Path.replace
def replace_receipt(source,target):
    if phase=='before_receipt' and Path(sys.argv[4]).exists(): os._exit(77)
    return replace(source,target)
Path.replace=replace_receipt
native=ctypes.WinDLL
kernel=native('kernel32',use_last_error=True)
def write(*args):
    kernel.WriteFile.argtypes=write.argtypes
    if phase=='after_receipt': os._exit(77)
    if phase=='partial_write':
        args=list(args);args[2]=args[2]//2
    result=kernel.WriteFile(*args)
    os._exit(77)
class Proxy:
    def __getattr__(self,name): return write if name=='WriteFile' else getattr(kernel,name)
ctypes.WinDLL=lambda name,*a,**kw: Proxy() if name=='kernel32' else native(name,*a,**kw)
create_test_shortcut(sys.argv[1],sys.argv[2])
'''
    process = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', script, str(control), instance['instance_id'], phase, str(link)],
                             capture_output=True, timeout=15)
    assert process.returncode == 77, process.stderr
    assert link.exists()
    result = cleanup.cleanup_instance(control, instance['instance_id'], tmp_path / 'reports', confirmation=instance['instance_id'])
    assert result['status'] == ('partial' if phase == 'before_receipt' else 'cleaned'), result
    assert link.exists() == (phase == 'before_receipt')


def test_shortcut_receipt_exception_reclaims_empty_file_and_retries(installed, monkeypatch):
    control, instance = installed
    link = instance['code'] / 'current/.horizon/test-launch.lnk'
    replace = Path.replace
    def fail(source, target):
        if Path(target) == instance['registration'] and link.exists():
            raise OSError('Injected shortcut receipt failure')
        return replace(source, target)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'replace', fail)
        with pytest.raises(OSError): cleanup.create_test_shortcut(control, instance['instance_id'])
    assert not link.exists()
    assert cleanup.create_test_shortcut(control, instance['instance_id'])['status'] == 'created'


@pytest.mark.parametrize('existing', ['unknown', 'same-byte-replacement'])
def test_shortcut_never_adopts_existing_or_same_byte_replacement(installed, tmp_path, existing):
    control, instance = installed
    link = instance['code'] / 'current/.horizon/test-launch.lnk'
    content = b'PERSONAL SHORTCUT'
    if existing == 'same-byte-replacement':
        cleanup.create_test_shortcut(control, instance['instance_id'])
        content = link.read_bytes()
        link.rename(tmp_path / 'original.lnk')
    link.write_bytes(content)
    identity = link.stat().st_ino
    with pytest.raises(cleanup.InstanceInvalid): cleanup.create_test_shortcut(control, instance['instance_id'])
    result = cleanup.cleanup_instance(control, instance['instance_id'], tmp_path / 'reports', confirmation=instance['instance_id'])
    assert result['status'] == 'partial'
    assert link.read_bytes() == content and link.stat().st_ino == identity
