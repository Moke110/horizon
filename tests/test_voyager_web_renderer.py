import json
import pytest
from hostrun.voyager_web_renderer import browser_executable, render_public


def test_browser_cannot_escape_package(tmp_path):
    (tmp_path/'browser-inventory.json').write_text(json.dumps({'executable':'../outside.exe'}))
    with pytest.raises(ValueError,match='invalid_browser_path'):
        browser_executable(tmp_path)


def test_browser_digest_is_verified_before_launch(tmp_path):
    (tmp_path/'browser.exe').write_bytes(b'changed')
    (tmp_path/'browser-inventory.json').write_text(json.dumps({'executable':'browser.exe','sha256':'0'*64}))
    with pytest.raises(ValueError,match='browser_digest_mismatch'):
        browser_executable(tmp_path)


def test_dynamic_capture_rejects_local_url():
    with pytest.raises(ValueError):
        render_public('http://127.0.0.1/',b'<html/>')

def test_browser_rejects_unlisted_adjacent_resource(tmp_path):
    import hashlib
    binary=tmp_path/'chrome.exe';binary.write_bytes(b'cgi test double')
    digest=hashlib.sha256(binary.read_bytes()).hexdigest()
    inventory={'executable':'chrome.exe','sha256':digest,'files':{'chrome.exe':digest},'playwright_version':'1.65.2'}
    (tmp_path/'browser-inventory.json').write_text(json.dumps(inventory))
    (tmp_path/'additional.dll').write_bytes(b'unexpected')
    with pytest.raises(ValueError,match='browser_resource_set_mismatch'):
        browser_executable(tmp_path)
