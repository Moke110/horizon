"""Horizon-owned Pi runtime and model configuration (no external agent)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from common import ensure_dir, file_lock, read_json
from hostrun.base import HostRunError, load_config


class ModelConfigurationMissing(HostRunError):
    """No usable model credentials were configured; no provider request ran."""


def config_path(home: Path) -> Path:
    return Path(home) / "agent" / "model.json"


def load_model_config(home):
    data = _model_snapshot(home)[0]
    return {key: value for key, value in data.items() if key != 'voyagerKeys'}


def load_source_keys(home):
    return _model_snapshot(home)[0].get('voyagerKeys', {})


def save_source_key(home, source, key):
    snapshot = _model_snapshot(home)
    data = dict(snapshot[0])
    source_keys = dict(data.get('voyagerKeys', {}))
    if key is None:
        source_keys.pop(source, None)
    else:
        source_keys[source] = key
    if source_keys:
        data['voyagerKeys'] = source_keys
    else:
        data.pop('voyagerKeys', None)
    if data != snapshot[0]:
        _save_model_config(home, data, expected=snapshot)


def _read_legacy_credentials(path, *, repair=False):
    try:
        data = read_json(path) or {}
        if not isinstance(data, dict):
            raise ValueError("model configuration must be an object")
        return data
    except ValueError:
        if repair:
            return {}
        raise


def _model_snapshot(home, *, repair_legacy=False):
    from hostrun.cleanup import load_managed_model_config
    managed, data, identity = load_managed_model_config(home)
    data = (data or {}) if managed else _read_legacy_credentials(config_path(home), repair=repair_legacy)
    if not isinstance(data, dict):
        raise ValueError("model configuration must be an object")
    return data, identity


def configure(home: Path, *, provider: str, model: str, api_key: str, base_url: str = "") -> dict:
    provider, model, api_key = provider.strip(), model.strip(), api_key.strip()
    if not provider or not model or not api_key:
        raise HostRunError("模型服务、模型名称和 API Key 均不能为空")
    if base_url and not base_url.startswith(("https://", "http://localhost:", "http://127.0.0.1:")):
        raise HostRunError("模型地址必须使用 HTTPS（本机服务可以使用 HTTP）")
    snapshot = _model_snapshot(home, repair_legacy=True)
    data = dict(snapshot[0])
    data.pop('models', None)
    data.update(provider=provider, model=model, apiKey=api_key, baseUrl=base_url.strip())
    _save_model_config(home, data, expected=snapshot, repair_legacy=True)
    return {"provider": provider, "model": model, "configured": True}


def runtime(home: Path, *, code: Path | None = None) -> dict:
    cfg = load_config(home)
    code = Path(code or cfg["paths"]["repo"]).resolve()
    bundled = code / "runtime" / "node.exe"
    node = str(bundled) if bundled.is_file() else shutil.which("node") or ""
    entry = code / "agent" / "dist" / "main.js"
    problems = []
    try:
        model = load_model_config(home)
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
        raise ModelConfigurationMissing("请在 Horizon 初始化流程中配置模型 API Key")
    args = [facts["node"], facts["entry"], "--home", str(Path(home).resolve()),
            "--kind", kind, "--task", task]
    if session_id is not None:
        import uuid
        if str(uuid.UUID(session_id)) != session_id:
            raise HostRunError("Invalid session identity")
        args.extend(["--session-id", session_id])
    return args

def discover_and_configure(home, *, provider, api_key, base_url='', expected=None):
    from hostrun.model_catalog import discover
    snapshot = expected if expected is not None else _model_snapshot(home)
    previous = snapshot[0]
    base, models = discover(provider, api_key, base_url)
    selected = previous.get('model') if previous.get('provider') == provider and previous.get('model') in models else models[0]
    data = dict(previous, provider=provider, model=selected, apiKey=api_key.strip(), baseUrl=base, models=models)
    _save_model_config(home, data, expected=snapshot)
    return models

def _save_model_config(home, data, *, expected=None, repair_legacy=False):
    from hostrun.cleanup import save_managed_model_config
    if save_managed_model_config(home, data, expected=expected):
        return
    import tempfile
    path = config_path(home)
    ensure_dir(path.parent)
    with file_lock(path.with_suffix('.lock')):
        if expected is not None and _read_legacy_credentials(path, repair=repair_legacy) != expected[0]:
            raise HostRunError('凭据配置已更改，请重试')
        fd, name = tempfile.mkstemp(dir=path.parent, prefix='model-', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

def refresh_models(home):
    snapshot = _model_snapshot(home)
    data = snapshot[0]
    return discover_and_configure(home, provider=data.get('provider', ''), api_key=data.get('apiKey', ''), base_url=data.get('baseUrl', ''), expected=snapshot)

def select_model(home, model):
    snapshot = _model_snapshot(home)
    data = dict(snapshot[0])
    if model not in data.get('models', []):
        raise HostRunError('请先刷新模型列表并选择其中的模型')
    data['model'] = model
    _save_model_config(home, data, expected=snapshot)
