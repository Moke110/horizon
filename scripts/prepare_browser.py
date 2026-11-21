"""Install official pinned Chromium into an explicit package directory, inventory all bytes."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil

from hostrun.voyager_web_renderer import chromium_descriptor, file_sha256


def prepare(destination):
    destination=Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('Browser destination must be empty')
    destination.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='horizon-chromium-stage-') as temporary:
        stage=Path(temporary)
        environment=dict(os.environ,PLAYWRIGHT_BROWSERS_PATH=str(stage))
        subprocess.run([sys.executable,'-m','playwright','install','--no-shell','chromium'],env=environment,check=True)
        folders=list(stage.glob('chromium-*'))
        if len(folders)!=1:raise ValueError('Expected one Chromium revision')
        shutil.copytree(folders[0],destination/folders[0].name)
    write_inventory(destination)


def write_inventory(destination):
    candidates=list(destination.glob('chromium-*/chrome-win*/chrome.exe'))
    if len(candidates)!=1:
        raise ValueError('Expected one packaged Chromium executable')
    chromium=chromium_descriptor()
    inventory={'chromium_revision':chromium['revision'],'chromium_version':chromium['browserVersion'],'playwright_version':importlib.metadata.version('playwright'),'executable':candidates[0].relative_to(destination).as_posix(),'sha256':file_sha256(candidates[0]),'files':{}}
    for path in sorted(destination.rglob('*')):
        if path.is_file():
            inventory['files'][path.relative_to(destination).as_posix()]=file_sha256(path)
    (destination/'browser-inventory.json').write_text(json.dumps(inventory,sort_keys=True,indent=2),encoding='utf8')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',required=True)
    prepare(parser.parse_args().out)
