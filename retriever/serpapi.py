"""Shared SerpApi credentials and normalized organic results."""
from urllib.parse import urlencode
from urllib.request import Request

from retriever.source_http import (
    MAX_RESULTS, SearchFailure, credential, failure, fetch_json, redact, row, web_url,
)


_LABELS = {
    "google_scholar": ("serpapi_scholar", "SerpApi · Google Scholar"),
    "google": ("serpapi_google", "SerpApi · Google"),
    "duckduckgo": ("serpapi_duckduckgo", "SerpApi · DuckDuckGo"),
    "bing": ("serpapi_bing", "SerpApi · Bing"),
}


def configuration(home):
    return credential(home, "serpapi", "SERPAPI_API_KEY")


def search(home, engine, query):
    source, label = _LABELS[engine]
    key, _ = configuration(home)
    if not key:
        return failure(label, "unconfigured")
    params = urlencode({"engine": engine, "q": query, "api_key": key})
    request = Request("https://serpapi.com/search.json?" + params)
    try:
        payload = fetch_json(request)
        if payload.get("error"):
            detail = str(payload["error"]).lower()
            if "no results" in detail or "hasn't returned any results" in detail:
                return {"source": source, "results": []}
            code = ("rate_limited" if "limit" in detail or "quota" in detail or "out of searches" in detail
                    else "authentication" if "api key" in detail else "provider_failed")
            return failure(label, code)
        if not isinstance(entries := payload.get("organic_results", []), list):
            raise SearchFailure("provider_failed")
        results = []
        for item in entries[:MAX_RESULTS]:
            url = web_url(item.get("link"))
            if url:
                results.append(row(item.get("result_id") or url, item.get("title"), url,
                                   snippet=str(item.get("snippet") or "")[:750]))
        return {"source": source, "results": redact(results, key)}
    except (AttributeError, TypeError):
        return failure(label, "provider_failed")
    except SearchFailure as exc:
        return failure(label, exc.code)
