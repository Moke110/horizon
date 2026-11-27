"""Shared connection probe for active sources."""
from retriever.registry import configuration_revision, search_source, set_connection


def test_connection(home, source):
    revision = configuration_revision(home, source)
    result = search_source(home, source, "graph theory")
    if result.get("error_code") != "unknown_source":
        set_connection(home, source, result, expected_revision=revision)
    return result
