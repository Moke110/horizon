"""Bounded JSON transport and metadata shared by current Retriever sources."""
import hashlib
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


class RejectRedirects(http.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def fetch_json(request, *, timeout=12, allow_redirects=True):
    open_request = http.urlopen if allow_redirects else http.build_opener(RejectRedirects()).open
    try:
        with open_request(request, timeout=timeout) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        code = error.code
        error.close()
        raise SearchFailure("rate_limited" if code in (429, 503) else
                            "authentication" if code in (401, 403) else
                            "quota_exhausted" if code == 402 else "provider_failed") from None
    except TimeoutError:
        raise SearchFailure("timeout") from None
    except URLError as exc:
        raise SearchFailure("timeout" if isinstance(exc.reason, TimeoutError) else "provider_failed") from None
    except (OSError, ValueError):
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
        "quota_exhausted": "可用额度已耗尽，请检查服务商额度。",
        "budget_unknown": "无法确认免费额度或免费额度已耗尽，请检查套餐、按量付费开关和密钥月度上限。",
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


def web_results(source, entries, *, snippet_field=None, engine_field=None, secret=None):
    if not isinstance(entries, list):
        raise SearchFailure("provider_failed")
    results = []
    for item in entries[:MAX_RESULTS]:
        if not isinstance(item, dict):
            continue
        url = web_url(item.get("url"))
        title = item.get("title")
        if not url or len(url) > 2048 or not isinstance(title, str) or not title.strip() or (secret and secret in url):
            continue
        snippet = item.get(snippet_field) if snippet_field else None
        engine = item.get(engine_field) if engine_field else None
        results.append(row(hashlib.sha256(url.encode()).hexdigest(), title.strip(), url,
                           snippet=snippet.strip()[:750] if isinstance(snippet, str) else None,
                           engine=engine.strip()[:100] if isinstance(engine, str) else None))
    if entries and not results:
        raise SearchFailure("provider_failed")
    return {"source": source, "results": redact(results, secret)}
