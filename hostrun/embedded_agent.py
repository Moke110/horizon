"""Horizon-owned Pi runtime and model configuration (no external agent)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from common import ensure_dir, read_json
from hostrun.base import HostRunError, load_config


def config_path(home: Path) -> Path:
    return Path(home) / "agent" / "model.json"


def configure(home: Path, *, provider: str, model: str, api_key: str, base_url: str = "") -> dict:
    provider, model, api_key = provider.strip(), model.strip(), api_key.strip()
    if not provider or not model or not api_key:
        raise HostRunError("模型服务、模型名称和 API Key 均不能为空")
    if base_url and not base_url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
        raise HostRunError("模型地址必须使用 HTTPS（本机服务可以使用 HTTP）")
    path = config_path(home)
    ensure_dir(path.parent)
    # Kept out of install/config.json, reports, process arguments and run logs.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"provider": provider, "model": model, "apiKey": api_key,
                   "baseUrl": base_url.strip()}, handle, ensure_ascii=False)
    return {"provider": provider, "model": model, "configured": True}


def runtime(home: Path, *, code: Path | None = None) -> dict:
    cfg = load_config(home)
    code = Path(code or cfg["paths"]["repo"]).resolve()
    bundled = code / "runtime" / "node.exe"
    node = str(bundled) if bundled.is_file() else shutil.which("node") or ""
    entry = code / "agent" / "dist" / "main.js"
    problems = []
    try:
        model = read_json(config_path(home)) or {}
        if not isinstance(model, dict):
            raise ValueError("model configuration must be an object")
    except (ValueError, OSError):
        model = {}
        problems.append("模型配置无法读取，请在 Horizon 内重新配置")
    if not node:
        problems.append("内置 Node.js 运行时缺失；开发环境需要 Node.js >=22.19")
    if not entry.is_file():
        problems.append("内置 Agent 未构建；开发环境请在 agent 目录运行 npm ci 和 npm run build")
    return {"ok": not problems, "node": node, "entry": str(entry),
            "problems": problems, "configured": bool(model.get("apiKey") and model.get("model")),
            "provider": model.get("provider", ""), "model": model.get("model", ""), "models": model.get("models", [model["model"]] if model.get("model") else [])}


def argv(home: Path, task: str, *, kind: str, code: Path | None = None, session_id: str | None = None) -> list[str]:
    facts = runtime(home, code=code)
    if not facts["ok"]:
        raise HostRunError("；".join(facts["problems"]))
    if not facts["configured"]:
        raise HostRunError("请在 Horizon 初始化流程中配置模型 API Key")
    args = [facts["node"], facts["entry"], "--home", str(Path(home).resolve()),
            "--kind", kind, "--task", task]
    if session_id is not None:
        import uuid
        if str(uuid.UUID(session_id)) != session_id:
            raise HostRunError("Invalid session identity")
        args.extend(["--session-id", session_id])
    return args

def discover_and_configure(home, *, provider, api_key, base_url=''):
    from hostrun.model_catalog import discover
    base, models = discover(provider, api_key, base_url)
    previous = read_json(config_path(home)) or {}
    selected = previous.get('model') if previous.get('provider') == provider and previous.get('model') in models else models[0]
    data = {'provider': provider, 'model': selected, 'apiKey': api_key.strip(), 'baseUrl': base, 'models': models}
    _save_model_config(home, data)
    return models

def _save_model_config(home, data):
    import tempfile
    path = config_path(home)
    ensure_dir(path.parent)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='model-', suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)

def refresh_models(home):
    data = read_json(config_path(home)) or {}
    return discover_and_configure(home, provider=data.get('provider', ''), api_key=data.get('apiKey', ''), base_url=data.get('baseUrl', ''))

def select_model(home, model):
    data = read_json(config_path(home)) or {}
    if model not in data.get('models', []):
        raise HostRunError('请先刷新模型列表并选择其中的模型')
    data['model'] = model
    _save_model_config(home, data)
