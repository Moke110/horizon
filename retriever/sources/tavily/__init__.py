import os

from hostrun.embedded_agent import load_source_configuration
from hostrun.voyager_tavily import TavilyProvider
from retriever import source_http


NAME = "Tavily"
KEY_LABEL = "Tavily API Key"


def configuration(home):
    return source_http.credential(home, "tavily", "TAVILY_API_KEY")


def search(home, query):
    saved, monthly_limit = load_source_configuration(home)
    key = saved.get("tavily") or os.environ.get("TAVILY_API_KEY")
    if not key:
        return source_http.failure(NAME, "unconfigured")
    data = TavilyProvider(home, key=key, monthly_limit=monthly_limit).search(
        {"term": query, "limit": source_http.MAX_RESULTS, "timeout_seconds": 20}
    )
    if "error_code" in data:
        return source_http.failure(NAME, data["error_code"])
    try:
        return source_http.web_results("tavily", data["candidates"], snippet_field="abstract", secret=key)
    except source_http.SearchFailure as exc:
        return source_http.failure(NAME, exc.code)
