"""Registry of independent active search sources."""
import re
from importlib import import_module
from pathlib import Path

from common import file_lock, read_json, write_json
from hostrun.agent_service import CommandError
from hostrun.embedded_agent import load_source_configuration, save_source_key

SOURCE_DIRECTORY = Path(__file__).parent / "sources"
REGISTERED_SOURCES = (
    "openalex", "arxiv", "europepmc", "crossref", "pubmed", "pmc", "semanticscholar", "serpapi_scholar",
    "tavily", "brave", "exa", "serpapi_google", "serpapi_duckduckgo", "serpapi_bing", "searxng",
)


def _installed(source):
    directory = SOURCE_DIRECTORY / source
    return (directory / "__init__.py").is_file() and (directory / "README.md").is_file()


def source_ids():
    return [source for source in REGISTERED_SOURCES if _installed(source)]


def _module(source):
    if source not in REGISTERED_SOURCES or not _installed(source):
        return None
    return import_module(f"retriever.sources.{source}")


def _settings(home):
    path = Path(home) / "retriever-settings.json"
    return read_json(path, {})


def discover_sources(home):
    """Record additions in order and retain local preferences across removal."""
    home = Path(home)
    with file_lock(home / "retriever-settings.lock"):
        settings = _settings(home)
        installed = source_ids()
        next_order = max((state.get("added_order", 0) for state in settings.values()), default=0)
        removed = False
        for source, state in settings.items():
            if source not in installed and "added_order" in state:
                state.pop("added_order")
                state.pop("connection", None)
                state["configuration_revision"] = state.get("configuration_revision", 0) + 1
                removed = True
        added = []
        for source in installed:
            state = settings.setdefault(source, {})
            if "added_order" not in state:
                next_order += 1
                state["added_order"] = next_order
                added.append(source)
        if added or removed:
            write_json(home / "retriever-settings.json", settings)
        return installed, added


def list_sources(home):
    settings = _settings(home)
    installed = source_ids()
    installed.sort(key=lambda source: settings.get(source, {}).get("added_order", 0), reverse=True)
    cards = []
    for source in installed:
        module = _module(source)
        if module is None:
            continue
        _, configuration = module.configuration(home)
        status = settings.get(source, {})
        cards.append({
            "id": source, "name": module.NAME, "configuration": configuration,
            "key_label": module.KEY_LABEL,
            "config_input_type": getattr(module, "CONFIG_INPUT_TYPE", "password"),
            "enabled": status.get("enabled", False),
            "connection": status.get("connection", ""),
            "description": (SOURCE_DIRECTORY / source / "README.md").read_text(encoding="utf-8").strip(),
        })
        if source == "tavily":
            _, monthly_limit = load_source_configuration(home)
            cards[-1]["monthly_limit"] = monthly_limit
    return cards


def configure_source(home, source, key):
    module = _module(source)
    if module is None or not module.KEY_LABEL or (
        key is not None and (not isinstance(key, str) or not re.fullmatch(r"[!-~]{1,1024}", key.strip()))
    ):
        raise CommandError("invalid_source_configuration", "请输入有效的信息源 API Key。")
    key = key.strip() if key is not None else None
    validate = getattr(module, "valid_configuration", None)
    if key is not None and validate is not None and not validate(key):
        raise CommandError("invalid_source_configuration", f"请输入有效的 {module.KEY_LABEL}。")
    config_key = getattr(module, "CONFIG_KEY", source)
    with file_lock(Path(home) / "retriever-settings.lock"):
        saved, _ = load_source_configuration(home)
        if saved.get(config_key) == key:
            return
        settings = _settings(home)
        for provider in source_ids():
            if getattr(_module(provider), "CONFIG_KEY", provider) == config_key:
                _invalidate_connection(settings.setdefault(provider, {}))
        write_json(Path(home) / "retriever-settings.json", settings)
        save_source_key(home, config_key, key)


def _invalidate_connection(status):
    status.pop("connection", None)
    status["configuration_revision"] = status.get("configuration_revision", 0) + 1


def invalidate_connection(home, source):
    with file_lock(Path(home) / "retriever-settings.lock"):
        settings = _settings(home)
        _invalidate_connection(settings.setdefault(source, {}))
        write_json(Path(home) / "retriever-settings.json", settings)


def configuration_revision(home, source):
    with file_lock(Path(home) / "retriever-settings.lock"):
        return _settings(home).get(source, {}).get("configuration_revision", 0)


def set_connection(home, source, result, *, expected_revision=None):
    with file_lock(Path(home) / "retriever-settings.lock"):
        settings = _settings(home)
        status = settings.setdefault(source, {})
        if expected_revision is not None and status.get("configuration_revision", 0) != expected_revision:
            return
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
