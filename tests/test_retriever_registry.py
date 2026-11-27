from io import BytesIO
import json
from urllib.parse import parse_qs, urlsplit

from hostrun.desktop_worker import DesktopWorker
from hostrun.voyager_doi_sources import OpenAlexProvider
from retriever.registry import configure_source, list_sources, search_source, set_connection, toggle_source


def test_openalex_discovery_exposes_configuration_without_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "secret-test-value")
    card = next(card for card in list_sources(tmp_path) if card["id"] == "openalex")
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
    card = next(card for card in list_sources(tmp_path) if card["id"] == "openalex")
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


def test_refresh_tracks_new_sources_and_probes_disabled_and_failed_cards(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from retriever import registry
    from retriever.connection import refresh_sources

    definitions = tmp_path / "sources"
    home = tmp_path / "profile"
    home.mkdir()
    monkeypatch.setattr(registry, "SOURCE_DIRECTORY", definitions)
    monkeypatch.setattr(registry, "REGISTERED_SOURCES", ("openalex", "faulty", "another"))
    probes = []
    modules = {}

    def add_source(source):
        directory = definitions / source
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "__init__.py").touch()
        (directory / "README.md").write_text(source + " description", encoding="utf-8")

        def configuration(home):
            from hostrun.embedded_agent import load_source_configuration
            key = load_source_configuration(home)[0].get(source)
            return key, "saved" if key else "missing"

        def search(home, query):
            probes.append(source)
            if source == "faulty":
                raise RuntimeError("provider internal error")
            if source == "openalex" and not configuration(home)[0]:
                return {"error_code": "unconfigured", "message": "Missing key"}
            return {"source": source, "results": []}

        modules[source] = SimpleNamespace(NAME=source, KEY_LABEL="API Key",
                                          configuration=configuration, search=search)
        return directory

    monkeypatch.setattr(registry, "import_module", lambda name: modules[name.rsplit(".", 1)[-1]])
    old_directory = add_source("openalex")
    cards, added = refresh_sources(home)
    assert added == ["openalex"]
    assert [card["id"] for card in cards] == ["openalex"]
    assert cards[0]["connection"] == "Missing key"
    assert not cards[0]["enabled"]

    configure_source(home, "openalex", "saved-key")
    cards, added = refresh_sources(home)
    assert added == []
    assert cards[0]["enabled"]
    toggle_source(home, "openalex", False)

    add_source("faulty")
    add_source("another")
    probes.clear()
    cards, added = refresh_sources(home)
    assert added == ["faulty", "another"]
    assert [card["id"] for card in cards] == ["another", "faulty", "openalex"]
    assert sorted(probes) == ["another", "faulty", "openalex"]
    assert cards[0]["enabled"] and cards[0]["connection"] == "连接成功"
    assert "失败" in cards[1]["connection"]
    assert not cards[2]["enabled"] and cards[2]["configuration"] == "saved"
    assert cards == list_sources(home)

    (old_directory / "__init__.py").unlink()
    cards, added = refresh_sources(home)
    assert added == []
    assert [card["id"] for card in cards] == ["another", "faulty"]
    assert search_source(home, "openalex", "graph theory")["error_code"] == "unknown_source"

    add_source("openalex")
    cards, added = refresh_sources(home)
    assert added == ["openalex"]
    assert cards[0]["id"] == "openalex" and not cards[0]["enabled"]
