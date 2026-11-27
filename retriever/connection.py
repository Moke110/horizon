"""Shared connection probe for active sources."""
from retriever.registry import search_source


def test_connection(home, source):
    return search_source(home, source, "graph theory")
