import os

from hostrun.embedded_agent import load_source_configuration
from hostrun.voyager_doi_sources import OpenAlexProvider

NAME = "OpenAlex"
KEY_LABEL = "OpenAlex API Key"


def configuration(home):
    saved, _ = load_source_configuration(home)
    if saved.get("openalex"):
        return saved["openalex"], "saved"
    key = os.environ.get("OPENALEX_API_KEY")
    return key, "environment" if key else "missing"


def search(home, query):
    key, _ = configuration(home)
    if not key:
        return {"error_code": "unconfigured", "message": "请在检索器中配置 OpenAlex API Key 后重试。"}
    result = OpenAlexProvider(home / "source-rates", key=key).search(
        {"term": query, "limit": 5, "cursor": None, "timeout_seconds": 20}
    )
    if "error_code" in result:
        return {"error_code": result["error_code"], "message": "OpenAlex 检索失败，请检查凭据、额度或网络。"}
    return {"source": "openalex", "results": [
        {field: row.get(field) for field in ("source_id", "title", "url", "doi", "published_at")}
        for row in result["candidates"]
    ]}
