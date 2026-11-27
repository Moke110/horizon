"""Active Semantic Scholar Academic Graph source."""
from urllib.parse import quote, urlencode
from urllib.request import Request

from retriever.source_http import MAX_RESULTS, SearchFailure, credential, failure, fetch_json, redact, row


NAME = "Semantic Scholar"
KEY_LABEL = "Semantic Scholar API Key（可选）"


def configuration(home):
    return credential(home, "semanticscholar", "SEMANTIC_SCHOLAR_API_KEY", optional=True)


def search(home, query):
    key, _ = configuration(home)
    params = urlencode({"query": query, "limit": MAX_RESULTS, "fields": "paperId,title,url,externalIds,year"})
    request = Request("https://api.semanticscholar.org/graph/v1/paper/search?" + params)
    if key:
        request.add_unredirected_header("X-Api-Key", key)
    try:
        results = []
        data = fetch_json(request)["data"]
        if not isinstance(data, list):
            raise SearchFailure("provider_failed")
        for item in data[:MAX_RESULTS]:
            ident = item["paperId"]
            url = item.get("url") or "https://www.semanticscholar.org/paper/" + quote(ident, safe="")
            results.append(row(ident, item["title"], url, doi=(item.get("externalIds") or {}).get("DOI"),
                               year=item.get("year")))
        return {"source": "semanticscholar", "results": redact(results, key)}
    except (KeyError, TypeError, AttributeError):
        return failure(NAME, "provider_failed")
    except SearchFailure as exc:
        return failure(NAME, exc.code)
