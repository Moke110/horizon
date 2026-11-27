from urllib.parse import urlencode, urlsplit
from urllib.request import Request

from retriever import source_http


NAME = "SearXNG"
KEY_LABEL = "SearXNG 实例 URL"
CONFIG_INPUT_TYPE = "url"


def valid_configuration(value):
    if not value or "\\" in value or any(character.isspace() for character in value):
        return False
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        return False
    return (url.scheme in ("http", "https") and bool(url.hostname) and url.username is None
            and not url.query and not url.fragment and port != 0)


def configuration(home):
    return source_http.credential(home, "searxng", "SEARXNG_URL")


def search(home, query):
    instance, _ = configuration(home)
    if not instance:
        return {"error_code": "unconfigured", "message": "请在检索器中配置 SearXNG 实例 URL。"}
    if not valid_configuration(instance):
        return {"error_code": "invalid_configuration", "message": "请检查 SearXNG 实例 URL。"}
    params = urlencode({"q": query, "format": "json"})
    request = Request(instance.rstrip("/") + "/search?" + params,
                      headers={"Accept": "application/json", "User-Agent": "Horizon Retriever"})
    try:
        data = source_http.fetch_json(request, allow_redirects=False)
        rows = data.get("results")
        if rows == [] and data.get("unresponsive_engines"):
            raise source_http.SearchFailure("provider_failed")
        return source_http.web_results("searxng", rows, snippet_field="content", engine_field="engine")
    except source_http.SearchFailure as exc:
        if exc.code == "authentication":
            return {"error_code": exc.code, "message": "SearXNG 实例拒绝了请求。请确认该实例允许 JSON 搜索。"}
        return source_http.failure(NAME, exc.code)
