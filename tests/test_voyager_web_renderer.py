import asyncio
import json
from types import SimpleNamespace

import pytest
from hostrun import voyager_web_renderer
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


@pytest.mark.parametrize(('updates', 'delay', 'deadline', 'expected_end'), [
    ('once', 1.5, 20, 2.25),
    ('once', 10, 20, 10.75),
    ('continuous', 1.5, 3, 3),
    ('never', 15, 20, 12),
])
def test_rendered_text_wait_observes_delayed_content_and_respects_deadline(
        monkeypatch, updates, delay, deadline, expected_end):
    now = 0
    samples = []

    async def sleep(seconds):
        nonlocal now
        now += seconds

    async def inner_text(*, timeout):
        assert 0 < timeout <= (deadline - now) * 1000
        content = 'Loading...' if now < delay else 'Rendered article'
        samples.append(content)
        return f'{content} {now}' if updates == 'continuous' else content

    def locator(selector):
        assert selector == 'body'
        return SimpleNamespace(inner_text=inner_text)

    monkeypatch.setattr(voyager_web_renderer, 'time', SimpleNamespace(monotonic=lambda: now))
    monkeypatch.setattr(voyager_web_renderer, 'asyncio', SimpleNamespace(sleep=sleep))
    asyncio.run(voyager_web_renderer.wait_for_rendered_text(SimpleNamespace(locator=locator), deadline))

    assert samples[0] == 'Loading...'
    assert samples[-1] == ('Loading...' if updates == 'never' else 'Rendered article')
    assert now == expected_end
