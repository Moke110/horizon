"""Install official pinned Chromium into an explicit package directory, inventory all bytes."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil


def prepare(destination):
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Browser destination must be empty')
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='horizon-chromium-stage-') as temporary:
        stage=Path(temporary)
        environment=dict(os.environ,PLAYWRIGHT_BROWSERS_PATH=str(stage))
        subprocess.run([sys.executable,'-m','playwright','install','chromium'],env=environment,check=True)
        folders=list(stage.glob('chromium-*'))
        if len(folders)!=1:raise ValueError('Expected one Chromium revision')
        shutil.copytree(folders[0],destination/folders[0].name)
    candidates=list(destination.glob('chromium-*/chrome-win*/chrome.exe'))
    if len(candidates)!=1:
        raise ValueError('Expected one packaged Chromium executable')
    from playwright._impl._driver import compute_driver_executable
    driver=Path(compute_driver_executable()[1]).parent
    descriptor=json.loads((driver/'browsers.json').read_text())
    chromium=next(item for item in descriptor['browsers'] if item['name']=='chromium')
    inventory={'chromium_revision':chromium['revision'],'chromium_version':chromium['browserVersion'],'playwright_version':importlib.metadata.version('playwright'),'executable':candidates[0].relative_to(destination).as_posix(),'sha256':hashlib.sha256(candidates[0].read_bytes()).hexdigest(),'files':{}}
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            inventory['files'][path.relative_to(destination).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    (destination/'browser-inventory.json').write_text(json.dumps(inventory,sort_keys=True,indent=2),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True)
    prepare(parser.parse_args().out)
