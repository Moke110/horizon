import json
import urllib.error
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest

from retriever.registry import configure_source, list_sources, search_source


class Response(BytesIO):
    status = 200
    headers = {}


def test_public_academic_sources_use_native_query_and_keep_provenance(tmp_path, monkeypatch):
    observed = []

    def respond(request, timeout):
        url = urlsplit(request.full_url)
        observed.append((url.hostname, parse_qs(url.query)))
        if url.hostname == "export.arxiv.org":
            body = ('<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>https://arxiv.org/abs/1901.00001</id>'
                    '<title>Graph study</title><summary>Graphs</summary><published>2019-01-02T00:00:00Z</published>'
                    '</entry></feed>').encode()
        elif url.hostname == "www.ebi.ac.uk":
            body = json.dumps({"resultList": {"result": [
                {"source": "MED", "id": "123", "title": "Exercise study", "firstPublicationDate": "2019-01-02"},
            ]}}).encode()
        else:
            assert url.hostname == "api.crossref.org"
            body = json.dumps({"message": {"items": [
                {"DOI": "10.1234/graph", "title": ["Graph study"], "published": {"date-parts": [[2019]]}},
            ]}}).encode()
        return Response(body)

    monkeypatch.setattr("urllib.request.urlopen", respond)
    cards = {card["id"]: card for card in list_sources(tmp_path)}
    assert all(cards[source]["configuration"] == "public" for source in ("arxiv", "europepmc", "crossref"))
    assert search_source(tmp_path, "arxiv", "ti:graph")["results"][0]["source_id"] == "1901.00001"
    assert search_source(tmp_path, "europepmc", 'TITLE:"physical activity"')["results"][0]["source_id"] == "MED:123"
    assert search_source(tmp_path, "crossref", "graph study")["results"][0]["source_id"] == "10.1234/graph"
    assert observed[0][1]["search_query"] == ["ti:graph"]
    assert observed[1][1]["query"] == ['TITLE:"physical activity"']
    assert observed[2][1]["query.bibliographic"] == ["graph study"]


@pytest.mark.parametrize(("source", "expected_id", "expected_host"), [
    ("pubmed", "123", "pubmed.ncbi.nlm.nih.gov"),
    ("pmc", "PMC123", "pmc.ncbi.nlm.nih.gov"),
])
def test_ncbi_search_uses_correct_collection_and_hides_optional_shared_key(
        tmp_path, monkeypatch, source, expected_id, expected_host):
    calls = []

    def respond(request, timeout):
        params = parse_qs(urlsplit(request.full_url).query)
        assert params["db"] == [source]
        assert params["api_key"] == ["test-ncbi-key"]
        calls.append(urlsplit(request.full_url).path.rsplit("/", 1)[-1])
        if len(calls) == 1:
            assert params["term"] == ["physical activity[Title]"]
            payload = {"esearchresult": {"idlist": ["123"]}}
        else:
            assert params["id"] == ["123"]
            payload = {"result": {"uids": ["123"], "123": {"title": "Physical activity"}}}
        return Response(json.dumps(payload).encode())

    monkeypatch.setattr("urllib.request.urlopen", respond)
    monkeypatch.delenv("NCBI_API_KEY", raising=False)
    cards = {card["id"]: card for card in list_sources(tmp_path)}
    assert cards["pmc"]["configuration"] == cards["pubmed"]["configuration"] == "optional"
    configure_source(tmp_path, "pubmed", "test-ncbi-key")
    cards = {card["id"]: card for card in list_sources(tmp_path)}
    assert cards["pubmed"]["configuration"] == cards["pmc"]["configuration"] == "saved"
    assert "test-ncbi-key" not in json.dumps(cards)
    result = search_source(tmp_path, source, "physical activity[Title]")
    found, = result["results"]
    assert found["source_id"] == expected_id
    assert urlsplit(found["url"]).hostname == expected_host
    assert "test-ncbi-key" not in json.dumps(result)
    assert calls == ["esearch.fcgi", "esummary.fcgi"]


def test_semantic_scholar_distinguishes_empty_and_rate_limited_results(tmp_path, monkeypatch):
    monkeypatch.delenv("SEMANTIC_SCHOLAR_API_KEY", raising=False)

    def respond(request, timeout):
        assert urlsplit(request.full_url).hostname == "api.semanticscholar.org"
        assert request.get_header("X-api-key") is None
        return Response(b'{"total": 0, "data": []}')

    monkeypatch.setattr("urllib.request.urlopen", respond)
    assert search_source(tmp_path, "semanticscholar", "graph neural networks") == {
        "source": "semanticscholar", "results": [],
    }

    def limited(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 429, "quota", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", limited)
    assert search_source(tmp_path, "semanticscholar", "graph neural networks")["error_code"] == "rate_limited"


def test_serpapi_scholar_preserves_provider_identity_and_sanitizes_returned_text(tmp_path, monkeypatch):
    monkeypatch.delenv("SERPAPI_API_KEY", raising=False)
    assert search_source(tmp_path, "serpapi_scholar", "graphs")["error_code"] == "unconfigured"
    configure_source(tmp_path, "serpapi_scholar", "test-scholarly-key")

    def respond(request, timeout):
        url = urlsplit(request.full_url)
        assert url.hostname == "serpapi.com"
        assert parse_qs(url.query)["engine"] == ["google_scholar"]
        return Response(b'{"organic_results":[{"result_id":"graph-1","title":"Graph theory test-scholarly-key","link":"https://example.com/graphs"}]}')

    monkeypatch.setattr("urllib.request.urlopen", respond)
    result = search_source(tmp_path, "serpapi_scholar", "graphs")
    assert result["source"] == "serpapi_scholar"
    assert result["results"][0]["source_id"] == "graph-1"
    assert "test-scholarly-key" not in json.dumps(result)
