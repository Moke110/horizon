"""Bounded JSON transport and metadata shared by current Retriever sources."""
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib import request as http

from hostrun.embedded_agent import load_source_configuration


MAX_RESULTS = 5
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class SearchFailure(Exception):
    def __init__(self, code):
        self.code = code


def credential(home, config_key, environment, *, optional=False):
    saved, _ = load_source_configuration(home)
    if saved.get(config_key):
        return saved[config_key], "saved"
    key = os.environ.get(environment)
    return key, "environment" if key else "optional" if optional else "missing"


def fetch_json(request, *, timeout=12):
    try:
        with http.urlopen(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        code = error.code
        error.close()
        raise SearchFailure("rate_limited" if code in (429, 503) else
                            "authentication" if code in (401, 403) else "provider_failed") from None
    except (OSError, URLError):
        raise SearchFailure("provider_failed") from None
    if len(body) > MAX_RESPONSE_BYTES:
        raise SearchFailure("provider_failed")
    try:
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError
    except (ValueError, UnicodeError):
        raise SearchFailure("provider_failed") from None
    return payload


def failure(label, code):
    detail = {
        "unconfigured": "请在检索器中配置 API Key。",
        "authentication": "凭据未通过验证，请检查 API Key 或访问权限。",
        "rate_limited": "请求受限，请稍后重试或检查可用额度。",
        "timeout": "请求超时，请稍后重试。",
    }.get(code, "检索服务暂时无法返回结果，请检查网络或稍后重试。")
    return {"error_code": code, "message": label + "：" + detail}


def web_url(value):
    if isinstance(value, str):
        try:
            url = urlsplit(value)
            if url.scheme in ("https", "http") and url.hostname and not (url.username or url.password):
                return value
        except ValueError:
            pass
    return None


def row(ident, title, url, **metadata):
    result = {"source_id": str(ident)[:512], "title": str(title or "[missing title]")[:500], "url": web_url(url)}
    result.update({name: value for name, value in metadata.items() if value})
    return result


def redact(rows, key):
    if key:
        return [{field: value.replace(key, "[redacted]") if isinstance(value, str) else value
                 for field, value in record.items()} for record in rows]
    return rows
