"""Authenticated provider model discovery; credentials never leave backend state."""
import json
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
from hostrun.base import HostRunError

CATALOG = {
    'deepseek': ('DeepSeek', 'https://api.deepseek.com'),
    'openai': ('OpenAI', 'https://api.openai.com/v1'),
    'groq': ('Groq', 'https://api.groq.com/openai/v1'),
    'mistral': ('Mistral', 'https://api.mistral.ai/v1'),
    'openrouter': ('OpenRouter', 'https://openrouter.ai/api/v1'),
    'custom': ('OpenAI 兼容服务', ''),
}

def providers():
    return [{'id': key, 'label': value[0]} for key, value in CATALOG.items()]

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def discover(provider, api_key, base_url=''):
    if provider not in CATALOG or not isinstance(api_key, str) or not api_key.strip():
        raise HostRunError('请选择提供商并输入 API Key')
    api_key = api_key.strip()
    base = (base_url.strip() if provider == 'custom' else CATALOG[provider][1]).rstrip('/')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1', '::1'))) or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HostRunError('服务地址需为 HTTPS；本机服务可使用 HTTP，地址不得包含凭据或查询参数')
    try:
        request = Request(base + '/models', headers={'Authorization': 'Bearer ' + api_key, 'Accept': 'application/json'})
        with build_opener(NoRedirect()).open(request, timeout=20) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError('oversized')
        payload = json.loads(raw)
        ids = []
        for item in payload['data']:
            name = item.get('id')
            if not isinstance(name, str) or not name or len(name) > 256 or api_key in name:
                continue
            if any(term in name.lower() for term in ('embedding', 'whisper', 'tts', 'moderation', 'dall-e', 'transcribe', 'rerank')):
                continue
            caps = item.get('capabilities', {})
            if caps.get('completion_chat') is False:
                continue
            ids.append(name)
        models = sorted(set(ids))
        if not models:
            raise ValueError('empty')
        return base, models
    except Exception:
        raise HostRunError('无法获取模型列表，请检查 API Key、服务地址和网络后重试；原配置未更改') from None
