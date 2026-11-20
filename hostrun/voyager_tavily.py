"""Free-plan-only Tavily discovery with durable precharged credits."""
import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from pathlib import Path
from common import file_lock, write_json


class RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise urllib.error.URLError('Redirect refused')


class TavilyProvider:
    def __init__(self, home, key=None):
        self.home = Path(home)
        self.key = key if key is not None else os.environ.get('TAVILY_API_KEY', '')

    def _request(self, endpoint, timeout, payload=None):
        request = urllib.request.Request('https://api.tavily.com/' + endpoint,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(RejectRedirects())
        with opener.open(request, timeout=timeout) as response:
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            raise ValueError('Oversized response')
        return json.loads(body)

    def search(self, query):
        deadline = time.monotonic() + query['timeout_seconds']
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0: raise TimeoutError()
            return min(30, value)
        if not self.key:
            return {'error_code': 'unconfigured'}
        with file_lock(self.home / 'tavily-budget.lock', blocking=False) as acquired:
            if not acquired:
                return {'error_code': 'budget_unknown'}
            state_path = self.home / 'tavily-budget.json'
            try:
                usage = self._request('usage', remaining())
                account, key = usage['account'], usage['key']
                numbers = [account['plan_usage'], account['plan_limit'], account['paygo_usage'], account['paygo_limit'], key['usage'], key['limit']]
                if any(type(value) is not int or value < 0 for value in numbers):
                    return {'error_code': 'budget_unknown'}
                if account['current_plan'].lower() not in ('researcher', 'free') or account['paygo_limit'] != 0 or account['paygo_usage'] != 0:
                    return {'error_code': 'budget_unknown'}
                if min(account['plan_limit'] - account['plan_usage'], key['limit'] - key['usage']) < 1:
                    return {'error_code': 'budget_unknown'}
                state = json.loads(state_path.read_text()) if state_path.exists() else {'reserved': 0, 'ceiling': min(account['plan_limit'] - account['plan_usage'], key['limit'] - key['usage'])}
                if type(state.get('reserved')) is not int or type(state.get('ceiling')) is not int or state['reserved'] < 0 or state['ceiling'] < 0 or state['reserved'] >= state['ceiling']:
                    return {'error_code': 'budget_unknown'}
                if state.get('unknown'):
                    return {'error_code': 'budget_unknown'}
                # Persist before sending: an interrupted call cannot receive a free retry.
                state.update(reserved=state['reserved'] + 1, unknown=True)
                write_json(state_path, state)
                payload = {'query': query['term'], 'search_depth': 'basic', 'auto_parameters': False, 'include_usage': True,
                    'include_answer': False, 'include_raw_content': False, 'max_results': min(20, query['limit'])}
                result = self._request('search', remaining(), payload)
                if result.get('usage', {}).get('credits') != 1:
                    return {'error_code': 'budget_unknown'}
                rows = []
                for item in result['results']:
                    url = item['url']
                    rows.append({'source': 'tavily', 'source_id': hashlib.sha256(url.encode()).hexdigest(),
                        'title': item['title'], 'url': url, 'abstract': item.get('content', ''),
                        'source_type': 'search_metadata', 'peer_review_status': 'unknown',
                        'published_at': None, 'discovered_at': int(time.time()), 'missing_fields': ['published_at']})
                from hostrun.voyager_sources import valid_metadata
                if len(rows) > payload['max_results'] or not valid_metadata({'candidates': rows}):
                    return {'error_code': 'provider_failed'}
                state.update(unknown=False, receipt=hashlib.sha256(json.dumps(result).encode()).hexdigest(), credits=1)
                write_json(state_path, state)
                return {'candidates': rows, 'exhausted': True, 'next_cursor': None, 'receipt': state['receipt'], 'source_query': json.dumps(payload)}
            except urllib.error.HTTPError as exc:
                code = 'authentication' if exc.code in (401,403) else 'rate_limited' if exc.code == 429 else 'provider_failed'
                exc.close()
                return {'error_code': code}
            except TimeoutError:
                return {'error_code': 'timeout'}
            except Exception:
                return {'error_code': 'budget_unknown'}
