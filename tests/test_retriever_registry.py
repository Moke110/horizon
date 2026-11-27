from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit

from hostrun.desktop_worker import DesktopWorker
from hostrun.voyager_doi_sources import OpenAlexProvider
from retriever.registry import configure_source, list_sources, search_source, set_connection, toggle_source


def test_openalex_discovery_exposes_configuration_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "secret-test-value")
    card, = list_sources(tmp_path)
    assert card["id"] == "openalex"
    assert card["name"] == "OpenAlex"
    assert card["description"]
    assert card["configuration"] == "environment"
    assert "secret-test-value" not in str(card)


def test_openalex_search_reports_missing_local_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    result = search_source(tmp_path, "openalex", "graph theory")
    assert result["error_code"] == "unconfigured"
    assert "API Key" in result["message"]
def test_openalex_changed_or_removed_key_clears_only_connection(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    configure_source(tmp_path, "openalex", "first-key")
    set_connection(tmp_path, "openalex", {"results": []})
    toggle_source(tmp_path, "openalex", False)
    configure_source(tmp_path, "openalex", "first-key")
    assert list_sources(tmp_path)[0]["connection"] == "连接成功"
    configure_source(tmp_path, "openalex", "second-key")
    assert list_sources(tmp_path)[0]["connection"] == ""
    set_connection(tmp_path, "openalex", {"results": []})
    assert not list_sources(tmp_path)[0]["enabled"]
    configure_source(tmp_path, "openalex", None)
    card, = list_sources(tmp_path)
    assert card["configuration"] == "missing"
    assert card["connection"] == ""
    assert not card["enabled"]


def test_worker_returns_openalex_provenance_without_credentials(tmp_path, monkeypatch):
    configure_source(tmp_path, "openalex", "private-test-key")
    worker = DesktopWorker.__new__(DesktopWorker)
    worker.home = tmp_path
    worker.service_token = "worker-token"
    calls = []

    def open_response(request, timeout):
        url = urlsplit(request.full_url)
        assert url.netloc == "api.openalex.org"
        assert request.get_header("Authorization") == "Bearer private-test-key"
        assert "private-test-key" not in request.full_url
        calls.append(url.path)
        if url.path == "/rate-limit":
            data = {"rate_limit": {"daily_remaining_usd": 1, "endpoint_costs_usd": {"search": 0.001}}}
        else:
            assert parse_qs(url.query)["search"] == ["graph theory"]
            data = {"results": [{"id": "https://openalex.org/W1", "display_name": "Graphs",
                                 "doi": "https://doi.org/10.1000/abc"}], "meta": {}}
        return BytesIO(json.dumps(data).encode())

    monkeypatch.setattr("urllib.request.urlopen", open_response)
    monkeypatch.setattr(OpenAlexProvider, "_next", 0)
    result = worker._term_request({"type": "retriever_search_request", "source": "openalex", "query": "graph theory"})
    assert calls == ["/rate-limit", "/works"]
    assert result["source"] == "openalex"
    work, = result["results"]
    assert work["source_id"] == "https://openalex.org/W1"
    assert work["url"] == "https://doi.org/10.1000/abc"
    assert "private-test-key" not in json.dumps(result)
