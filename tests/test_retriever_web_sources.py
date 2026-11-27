from io import BytesIO
import json
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import OpenerDirector

import pytest

from hostrun.agent_service import CommandError
from retriever.registry import configure_source, list_sources, search_source, set_connection


QUERY = "SQLite write ahead logging"
URL = "https://www.sqlite.org/wal.html"
TITLE = "Write-Ahead Logging"


@pytest.mark.parametrize("source,configuration,path,shape", [
    ("brave", "fixture-key", "/res/v1/web/search", {"web": {"results": [{"url": URL, "title": TITLE}]}}),
    ("exa", "fixture-key", "/search", {"results": [{"url": URL, "title": TITLE}]}),
    ("searxng", "http://127.0.0.1:8123/prefix", "/prefix/search",
     {"results": [{"url": URL, "title": TITLE, "engine": "bing"}]}),
])
def test_registered_web_sources_produce_traceable_results(tmp_path, monkeypatch, source, configuration, path, shape):
    configure_source(tmp_path, source, configuration)
    card = next(card for card in list_sources(tmp_path) if card["id"] == source)
    assert card["configuration"] == "saved" and configuration not in str(card)

    def request(_opener, req, **kwargs):
        target = urlsplit(req.full_url)
        assert target.path == path
        assert kwargs["timeout"] <= 20
        if source == "brave":
            assert req.get_header("X-subscription-token") == configuration
            assert parse_qs(target.query)["q"] == [QUERY]
        elif source == "exa":
            assert req.get_header("X-api-key") == configuration
            assert json.loads(req.data)["query"] == QUERY
        else:
            assert parse_qs(target.query) == {"q": [QUERY], "format": ["json"]}
        return BytesIO(json.dumps(shape).encode())

    monkeypatch.setattr(OpenerDirector, "open", request)
    result = search_source(tmp_path, source, QUERY)
    assert result["source"] == source
    row, = result["results"]
    assert row["title"] == TITLE and row["url"] == URL and row["source_id"]
    assert configuration not in str(result)
    if source == "searxng":
        assert row["engine"] == "bing"


def test_web_sources_report_missing_key_or_exhausted_quota(tmp_path, monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert search_source(tmp_path, "brave", QUERY)["error_code"] == "unconfigured"
    configure_source(tmp_path, "brave", "private-key")

    def request(_opener, req, **kwargs):
        raise HTTPError(req.full_url, 429, "private-key", None, BytesIO(b"private-key"))

    monkeypatch.setattr(OpenerDirector, "open", request)
    failure = search_source(tmp_path, "brave", QUERY)
    assert failure["error_code"] == "rate_limited"
    assert "额度" in failure["message"]
    assert "private-key" not in str(failure)


def test_searxng_card_rejects_non_http_and_embedded_credentials(tmp_path):
    for invalid in ("file:///tmp/x", "http://user:password@localhost", "https://localhost?q=preloaded"):
        with pytest.raises(CommandError):
            configure_source(tmp_path, "searxng", invalid)


def test_serpapi_credentials_and_stale_status_are_shared_by_engine(tmp_path):
    configure_source(tmp_path, "serpapi_google", "old-key")
    set_connection(tmp_path, "serpapi_google", {"results": []})
    set_connection(tmp_path, "serpapi_bing", {"results": []})
    configure_source(tmp_path, "serpapi_duckduckgo", "new-key")
    cards = {card["id"]: card for card in list_sources(tmp_path)}
    for source in ("serpapi_google", "serpapi_duckduckgo", "serpapi_bing"):
        assert cards[source]["name"].startswith("SerpApi")
        assert cards[source]["configuration"] == "saved"
        assert cards[source]["connection"] == ""
    assert cards["serpapi_bing"]["enabled"]


def test_tavily_uses_existing_free_budget_provider(tmp_path, monkeypatch):
    configure_source(tmp_path, "tavily", "fixture-key")
    requests = []

    def request(self, endpoint, timeout, payload=None):
        requests.append(endpoint)
        assert self.key == "fixture-key"
        if endpoint == "usage":
            return {"account": {"plan_usage": 0, "plan_limit": 10, "paygo_usage": 0,
                                "current_plan": "free", "paygo_limit": 0},
                    "key": {"usage": 0, "limit": 10}}
        assert payload["query"] == QUERY and payload["search_depth"] == "basic"
        return {"results": [{"url": URL, "title": TITLE}], "usage": {"credits": 1}}

    monkeypatch.setattr("hostrun.voyager_tavily.TavilyProvider._request", request)
    result = search_source(tmp_path, "tavily", QUERY)
    assert requests == ["usage", "search"]
    assert result["source"] == "tavily"
    assert result["results"][0]["url"] == URL


@pytest.mark.parametrize("engine", ["google", "duckduckgo", "bing"])
def test_serpapi_reports_actual_provider_and_engine_results(tmp_path, monkeypatch, engine):
    source = "serpapi_" + engine
    configure_source(tmp_path, source, "private-serpapi-key")

    def request(req, **kwargs):
        target = urlsplit(req.full_url)
        assert target.hostname == "serpapi.com" and target.path == "/search.json"
        assert parse_qs(target.query) == {
            "engine": [engine], "q": [QUERY], "api_key": ["private-serpapi-key"],
        }
        return BytesIO(json.dumps({"organic_results": [{
            "title": TITLE, "link": URL, "snippet": "private-serpapi-key",
        }]}).encode())

    monkeypatch.setattr("urllib.request.urlopen", request)
    result = search_source(tmp_path, source, QUERY)
    assert result["source"] == source
    assert result["results"][0]["url"] == URL
    assert "private-serpapi-key" not in str(result)
