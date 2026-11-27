"""Shared connection probe for active sources."""
from concurrent.futures import ThreadPoolExecutor

from retriever.registry import (
    configuration_revision, discover_sources, list_sources, search_source, set_connection,
)


def test_connection(home, source):
    revision = configuration_revision(home, source)
    try:
        result = search_source(home, source, "graph theory")
    except Exception:
        result = {"error_code": "provider_failed", "message": "连接测试失败，请检查信息源配置后重试。"}
    if result.get("error_code") != "unknown_source":
        set_connection(home, source, result, expected_revision=revision)
    return result


def refresh_sources(home):
    installed, added = discover_sources(home)
    if installed:
        with ThreadPoolExecutor(max_workers=min(4, len(installed))) as pool:
            list(pool.map(lambda source: test_connection(home, source), installed))
    return list_sources(home), added
