"""Interactive access to established Horizon scholarly providers."""
from retriever.source_http import MAX_RESULTS, failure, row


def public_configuration(home):
    return None, "public"


def search_existing(home, provider_type, source, label, query, *, native_expression=False):
    provider = provider_type(home / "source-rates")
    options = {"term": query, "limit": MAX_RESULTS, "cursor": None, "timeout_seconds": 15,
               "window": {"basis": "none"}}
    if native_expression:
        options["search_expression"] = query
    result = provider.search(options)
    if result.get("error_code"):
        return failure(label, result["error_code"])
    return {"source": source, "results": [
        row(item["source_id"], item["title"], item["url"], doi=item.get("doi"),
            published_at=item.get("published_at"), snippet=(item.get("abstract") or "")[:750])
        for item in result["candidates"]
    ]}
