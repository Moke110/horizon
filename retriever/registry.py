"""Registry of independent active search sources."""
import importlib
import re
from pathlib import Path

from common import file_lock, read_json, write_json
from hostrun.agent_service import CommandError
from hostrun.embedded_agent import save_source_key

SOURCE_DIRECTORY = Path(__file__).parent / "sources"
REGISTERED_SOURCES = ("openalex",)


def _module(source):
    if source not in REGISTERED_SOURCES or not (SOURCE_DIRECTORY / source / "__init__.py").is_file():
        return None
    return importlib.import_module(f"retriever.sources.{source}")


def _settings(home):
    path = Path(home) / "retriever-settings.json"
    return read_json(path, {})


def list_sources(home):
    settings = _settings(home)
    cards = []
    for source in REGISTERED_SOURCES:
        module = _module(source)
        if module is None:
            continue
        _, configuration = module.configuration(home)
        status = settings.get(source, {})
        cards.append({
            "id": source, "name": module.NAME, "configuration": configuration,
            "key_label": module.KEY_LABEL, "enabled": status.get("enabled", False),
            "connection": status.get("connection", ""),
            "description": (SOURCE_DIRECTORY / source / "README.md").read_text(encoding="utf-8").strip(),
        })
    return cards


def configure_source(home, source, key):
    if _module(source) is None or not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,1024}", key.strip()):
        raise CommandError("invalid_source_configuration", "请输入有效的信息源 API Key。")
    save_source_key(home, source, key.strip())


def set_connection(home, source, result):
    with file_lock(Path(home) / "retriever-settings.lock"):
        settings = _settings(home)
        status = settings.setdefault(source, {})
        if not result.get("error_code") and not status.get("connected_once"):
            status["connected_once"] = True
            status["enabled"] = True
        status["connection"] = result.get("message", "连接成功")
        write_json(Path(home) / "retriever-settings.json", settings)


def toggle_source(home, source, enabled):
    if _module(source) is None or type(enabled) is not bool:
        raise CommandError("invalid_source", "无法更新此信息源。")
    with file_lock(Path(home) / "retriever-settings.lock"):
        settings = _settings(home)
        settings.setdefault(source, {})["enabled"] = enabled
        write_json(Path(home) / "retriever-settings.json", settings)


def search_source(home, source, query):
    module = _module(source)
    if module is None:
        return {"error_code": "unknown_source", "message": "未发现指定信息源。"}
    if not isinstance(query, str) or not query.strip() or len(query) > 512:
        return {"error_code": "invalid_query", "message": "请输入 1–512 个字符的检索式。"}
    return module.search(Path(home), query.strip())
