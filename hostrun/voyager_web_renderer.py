"""Package-only Chromium execution for public dynamic document capture."""
import asyncio
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from urllib.parse import urldefrag, urlsplit


def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def chromium_descriptor():
    import playwright

    path = Path(playwright.__file__).parent / 'driver' / 'package' / 'browsers.json'
    descriptor = json.loads(path.read_text(encoding='utf8'))
    return next(browser for browser in descriptor['browsers'] if browser['name'] == 'chromium')


def browser_executable(root):
    root = Path(root).resolve()
    inventory = json.loads((root / 'browser-inventory.json').read_text(encoding='utf8'))
    relative = Path(inventory['executable'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('invalid_browser_path')
    executable = root / relative
    if executable.resolve().is_relative_to(root) is False:
        raise ValueError('invalid_browser_path')
    if file_sha256(executable) != inventory['sha256']:
        raise ValueError('browser_digest_mismatch')
    files = inventory.get('files',{})
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if actual != set(files) | {'browser-inventory.json'} or relative.as_posix() not in files:
        raise ValueError('browser_resource_set_mismatch')
    for name,digest in files.items():
        resource=root/name
        if not resource.resolve().is_relative_to(root) or not resource.is_file() or file_sha256(resource)!=digest:
            raise ValueError('browser_resource_digest_mismatch')
    from importlib.metadata import version
    if version('playwright') != inventory['playwright_version']:
        raise ValueError('browser_driver_version_mismatch')
    chromium = chromium_descriptor()
    if (inventory.get('chromium_version'), inventory.get('chromium_revision')) != (
            chromium['browserVersion'], chromium['revision']):
        raise ValueError('browser_version_mismatch')
    return executable


def render_public(url, html_bytes, cancel=None):
    from hostrun.voyager_subscriptions import public_address
    public_address(url)
    if len(html_bytes)>2*1024*1024:
        raise ValueError('response_too_large')
    root=Path(__file__).resolve().parents[1] / 'runtime' / 'browser'
    executable=browser_executable(root)
    if cancel is not None and cancel():
        raise ValueError('cancelled')
    temporary = tempfile.TemporaryDirectory(prefix='horizon-web-')
    try:
        folder=Path(temporary.name)
        (folder/'input.html').write_bytes(html_bytes)
        scratch = folder / 'browser-temp'
        scratch.mkdir()
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() not in {'PLAYWRIGHT_NODEJS_PATH', 'PLAYWRIGHT_BROWSERS_PATH'}}
        environment.update(TEMP=str(scratch), TMP=str(scratch), TMPDIR=str(scratch),
                           PLAYWRIGHT_BROWSERS_PATH=str(root))
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        options['env'] = environment
        worker=subprocess.Popen([sys.executable,'-B','-X','utf8','-m','hostrun.voyager_web_renderer','--worker',url,str(executable),str(folder)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)
        deadline=time.monotonic()+30
        try:
            while worker.poll() is None:
                if time.monotonic()>deadline or (cancel is not None and cancel()):
                    raise ValueError('cancelled_or_timeout')
                time.sleep(.05)
            if worker.returncode:
                raise ValueError('dynamic_capture_failed')
            output=folder/'result.html'
            if output.stat().st_size>2*1024*1024:
                raise ValueError('response_too_large')
            return output.read_bytes()
        finally:
            if worker.poll() is None:
                worker.kill()
            worker.wait(timeout=5)
    finally:
        # The Job terminates children asynchronously as the worker exits.
        deadline = time.monotonic() + 5
        while True:
            try:
                temporary.cleanup()
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.05)


async def wait_for_rendered_text(page, deadline):
    """Let client-side text settle without waiting for unrelated network traffic."""
    started = time.monotonic()
    deadline = min(deadline, started + 12)
    latest_text = None
    changed = started
    observed_update = False
    body = page.locator('body')
    while (remaining := deadline - time.monotonic()) > 0:
        # Evaluate via a locator so the first sample can wait for the body.
        text = await body.inner_text(timeout=max(1, int(remaining * 1000)))
        now = time.monotonic()
        if text != latest_text:
            observed_update = latest_text is not None
            latest_text = text
            changed = now
        # The first sample can be a stable loading shell or header and footer.
        if observed_update and text.strip() and now - started >= 2 and now - changed >= .75:
            return
        await asyncio.sleep(max(0, min(.25, deadline - now)))


async def capture_worker(url, executable, folder):
    # Child owns a nested kill-on-close job; cancellation kills its browser descendants.
    from hostrun.desktop_processes import install
    if os.name!='nt':
        raise ValueError('unsupported_capture_containment')
    install()
    from playwright.async_api import TimeoutError as PlaywrightTimeout, async_playwright
    from hostrun.voyager_subscriptions import fetch_public
    folder=Path(folder)
    count=0
    async with async_playwright() as driver:
        browser=await driver.chromium.launch(executable_path=executable,headless=True,chromium_sandbox=True,args=['--disable-background-networking','--disable-quic','--disable-features=WebRtcHideLocalIpsWithMdns','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        try:
            if browser.version != chromium_descriptor()['browserVersion']:
                raise ValueError('browser_version_mismatch')
            context=await browser.new_context(service_workers='block',accept_downloads=False)
            await context.route_web_socket('**/*',lambda socket:socket.close())
            await context.add_init_script("Object.defineProperty(globalThis,'Worker',{value:undefined});Object.defineProperty(globalThis,'SharedWorker',{value:undefined});Object.defineProperty(globalThis,'RTCPeerConnection',{value:undefined});")
            page=await context.new_page()
            fetch_slots = asyncio.Semaphore(6)
            source_host = urlsplit(url).hostname

            async def route_request(route):
                nonlocal count
                count+=1
                if (count > 40 or route.request.method != 'GET' or
                        route.request.resource_type in {'image', 'font', 'media'}):
                    await route.abort()
                    return
                try:
                    if route.request.is_navigation_request() and route.request.frame == page.main_frame:
                        if route.request.url != url:
                            await route.abort()
                            return
                        await route.fulfill(status=200,content_type='text/html',body=(folder/'input.html').read_bytes())
                    else:
                        # A slow third-party CSS import can block even inline scripts.
                        stylesheet = route.request.resource_type == 'stylesheet'
                        third_party = urlsplit(route.request.url).hostname != source_host
                        timeout = 3 if stylesheet and third_party else 12
                        async with fetch_slots:
                            status,headers,body=await asyncio.to_thread(fetch_public,route.request.url,{},timeout=timeout)
                        if status!=200:
                            await route.abort()
                            return
                        await route.fulfill(status=status,content_type=headers.get('Content-Type','application/octet-stream'),body=body)
                except Exception:
                    await route.abort()
            await context.route('**/*',route_request)
            navigation_deadline = time.monotonic() + 20
            try:
                await page.goto(url, wait_until='domcontentloaded', timeout=15000)
            except PlaywrightTimeout:
                # A committed page can still yield text while another script loads.
                if urldefrag(page.url).url != url:
                    raise
            await wait_for_rendered_text(page, navigation_deadline)
            if urldefrag(page.url).url != url:
                raise ValueError('dynamic_source_changed')
            content=(await page.content()).encode('utf8')
            if len(content)>2*1024*1024:raise ValueError('response_too_large')
            (folder/'result.html').write_bytes(content)
        finally:
            await browser.close()


if __name__=='__main__':
    if len(sys.argv)!=5 or sys.argv[1]!='--worker':raise SystemExit(2)
    asyncio.run(capture_worker(*sys.argv[2:]))
