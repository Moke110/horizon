"""NCBI collections share credentials and an E-utilities request rate."""
import re
import time
from urllib.parse import urlencode
from urllib.request import Request

from common import file_lock
from retriever.source_http import MAX_RESULTS, SearchFailure, credential, failure, fetch_json, redact, row


_ENDPOINT = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
_LABELS = {"pubmed": "PubMed", "pmc": "PubMed Central"}


def configuration(home):
    return credential(home, "ncbi", "NCBI_API_KEY", optional=True)


def _request(home, operation, database, key, **parameters):
    directory = home / "source-rates"
    next_request = directory / "ncbi.next"
    params = {"db": database, "retmode": "json", "tool": "horizon", **parameters}
    if key:
        params["api_key"] = key
    request = Request(_ENDPOINT + operation + ".fcgi?" + urlencode(params))
    delay_deadline = time.monotonic() + 3
    while time.monotonic() < delay_deadline:
        with file_lock(directory / "ncbi.lock", blocking=False) as acquired:
            if acquired:
                try:
                    remaining = float(next_request.read_text(encoding="utf-8")) - time.time()
                except (FileNotFoundError, ValueError):
                    remaining = 0
                if remaining > delay_deadline - time.monotonic():
                    raise SearchFailure("rate_limited")
                time.sleep(max(0, remaining))
                next_request.write_text(str(time.time() + 0.34), encoding="utf-8")
                return fetch_json(request)
        time.sleep(0.05)
    raise SearchFailure("rate_limited")


def search(home, database, query):
    label = _LABELS[database]
    key, _ = configuration(home)
    try:
        found = _request(home, "esearch", database, key, term=query, retmax=MAX_RESULTS)
        ids = found["esearchresult"]["idlist"]
        if not isinstance(ids, list) or not all(isinstance(item, str) and re.fullmatch(r"[0-9]{1,20}", item) for item in ids):
            raise SearchFailure("provider_failed")
        ids = ids[:MAX_RESULTS]
        if not ids:
            return {"source": database, "results": []}
        details = _request(home, "esummary", database, key, id=",".join(ids))["result"]
        results = []
        for uid in ids:
            item = details[uid]
            if database == "pubmed":
                ident = uid
                url = "https://pubmed.ncbi.nlm.nih.gov/" + uid + "/"
            else:
                ident = "PMC" + uid
                url = "https://pmc.ncbi.nlm.nih.gov/articles/" + ident + "/"
            results.append(row(ident, item["title"], url))
        return {"source": database, "results": redact(results, key)}
    except (KeyError, TypeError, OSError):
        return failure(label, "provider_failed")
    except SearchFailure as exc:
        return failure(label, exc.code)
