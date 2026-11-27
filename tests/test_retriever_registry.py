from retriever.registry import list_sources, search_source


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
