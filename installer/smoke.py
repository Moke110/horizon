"""Check a built/installed distribution without credentials or network requests."""
import json
import os
from pathlib import Path
import subprocess
import sys


def check(package: Path) -> dict:
    package = package.resolve()
    env = {key: value for key, value in os.environ.items()
           if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP"}}
    env["PATH"] = ""
    commands = [
        [str(package / "runtime/python.exe"), "-B", "-X", "utf8", "-c", "import yaml, fitz, hostrun.cli; print('business runtime ready')"],
        [str(package / "runtime/node.exe"), str(package / "agent/dist/main.js"), "--check"],
        [str(package / "agent/node_modules/@larksuite/cli/bin/lark-cli.exe"), "--version"],
    ]
    results = []
    for command in commands:
        proc = subprocess.run(command, cwd=package, env=env, capture_output=True,
                              text=True, encoding="utf-8", errors="replace", timeout=30)
        if proc.returncode:
            raise RuntimeError(f"Runtime check failed: {command[0]}: {proc.stdout}\n{proc.stderr}")
        results.append(proc.stdout.strip())
    return {"ok": True, "checks": results, "live_model_tested": False, "live_feishu_tested": False}


if __name__ == "__main__":
    print(json.dumps(check(Path(sys.argv[1])), ensure_ascii=False))
