"""Package-only Chromium execution for public dynamic document capture."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


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
    with tempfile.TemporaryDirectory(prefix='horizon-web-') as folder:
        folder=Path(folder)
        (folder/'input.html').write_bytes(html_bytes)
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
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


def capture_worker(url, executable, folder):
    # Child owns a nested kill-on-close job; cancellation kills its browser descendants.
    from hostrun.desktop_processes import install
    if os.name!='nt':
        raise ValueError('unsupported_capture_containment')
    install()
    from playwright.sync_api import sync_playwright
    from hostrun.voyager_subscriptions import fetch_public
    folder=Path(folder)
    count=0
    with sync_playwright() as driver:
        browser=driver.chromium.launch(executable_path=executable,headless=True,chromium_sandbox=True,args=['--disable-background-networking','--disable-quic','--disable-features=WebRtcHideLocalIpsWithMdns','--force-webrtc-ip-handling-policy=disable_non_proxied_udp'])
        try:
            context=browser.new_context(service_workers='block',accept_downloads=False)
            context.route_web_socket('**/*',lambda socket:socket.close())
            context.add_init_script("Object.defineProperty(globalThis,'Worker',{value:undefined});Object.defineProperty(globalThis,'SharedWorker',{value:undefined});Object.defineProperty(globalThis,'RTCPeerConnection',{value:undefined});")
            page=context.new_page()
            def route_request(route):
                nonlocal count
                count+=1
                if count>40 or route.request.method!='GET':
                    route.abort();return
                try:
                    if route.request.url==url and route.request.is_navigation_request():
                        route.fulfill(status=200,content_type='text/html',body=(folder/'input.html').read_bytes())
                    else:
                        status,headers,body=fetch_public(route.request.url,{})
                        if status!=200:
                            route.abort();return
                        route.fulfill(status=status,content_type=headers.get('Content-Type','application/octet-stream'),body=body)
                except Exception:
                    route.abort()
            context.route('**/*',route_request)
            page.goto(url,wait_until='networkidle',timeout=15000)
            content=page.content().encode('utf8')
            if len(content)>2*1024*1024:raise ValueError('response_too_large')
            (folder/'result.html').write_bytes(content)
        finally:
            browser.close()


if __name__=='__main__':
    if len(sys.argv)!=5 or sys.argv[1]!='--worker':raise SystemExit(2)
    capture_worker(*sys.argv[2:])
