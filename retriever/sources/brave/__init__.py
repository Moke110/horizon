from urllib.parse import urlencode
from urllib.request import Request

from retriever import source_http


NAME = "Brave Search"
KEY_LABEL = "Brave Search API Key"


def configuration(home):
    return source_http.credential(home, "brave", "BRAVE_API_KEY")


def search(home, query):
    key, _ = configuration(home)
    if not key:
        return source_http.failure(NAME, "unconfigured")
    params = urlencode({"q": query, "count": source_http.MAX_RESULTS, "result_filter": "web"})
    request = Request("https://api.search.brave.com/res/v1/web/search?" + params,
                      headers={"X-Subscription-Token": key, "Accept": "application/json"})
    try:
        data = source_http.fetch_json(request, allow_redirects=False)
        web = data.get("web", {})
        rows = web.get("results", []) if isinstance(web, dict) else None
        return source_http.web_results("brave", rows, snippet_field="description", secret=key)
    except source_http.SearchFailure as exc:
        return source_http.failure(NAME, exc.code)
