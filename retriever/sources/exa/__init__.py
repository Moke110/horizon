import json
from urllib.request import Request

from retriever import source_http


NAME = "Exa"
KEY_LABEL = "Exa API Key"


def configuration(home):
    return source_http.credential(home, "exa", "EXA_API_KEY")


def search(home, query):
    key, _ = configuration(home)
    if not key:
        return source_http.failure(NAME, "unconfigured")
    payload = {"query": query, "numResults": source_http.MAX_RESULTS, "type": "lite"}
    request = Request("https://api.exa.ai/search", data=json.dumps(payload).encode(),
                      headers={"x-api-key": key, "Content-Type": "application/json"})
    try:
        data = source_http.fetch_json(request, allow_redirects=False)
        return source_http.web_results("exa", data.get("results"), secret=key)
    except source_http.SearchFailure as exc:
        return source_http.failure(NAME, exc.code)
