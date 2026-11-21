"""Voyager discovery of DOI records."""
import os
import threading
import math
import re
from email.utils import parsedate_to_datetime
import json
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from hostrun.voyager_sources import AcademicTransport, page


class CrossrefProvider(AcademicTransport):
    _lock = threading.Lock()
    endpoint = 'https://api.crossref.org/works'
    interval = 1.0
    _score_cursor_prefix = 'score:'

    def track_response_limits(self, headers):
        interval = headers.get('X-Rate-Limit-Interval', '')
        match = re.fullmatch(r'([0-9.]+)s', interval)
        limit = headers.get('X-Rate-Limit-Limit', '')
        if match and limit.isdecimal() and int(limit):
            spacing = float(match[1]) / int(limit)
            type(self)._next = max(type(self)._next, time.monotonic() + spacing)

    def search(self, query):
        cursor = query['cursor']
        # A stored cursor from an older run must continue with its original ordering.
        sort_by_score = not cursor or cursor.startswith(self._score_cursor_prefix)
        params = {'query.bibliographic': query['term'],
                  'cursor': cursor.removeprefix(self._score_cursor_prefix) if cursor else '*',
                  'rows': min(query['limit'], 20)}
        if sort_by_score:
            params['sort'] = 'score'
        if query['window']['basis'] == 'published':
            window = query['window']
            end = datetime.fromtimestamp(window['until_at'] - 1, timezone.utc).date()
            bounds = ['until-pub-date:' + str(end)]
            if query['phase'] != 'foundation':
                start = datetime.fromtimestamp(window['effective_from_at'], timezone.utc).date()
                bounds.append('from-pub-date:' + str(start))
            params['filter'] = ','.join(bounds)
        result = self._fetch(self.endpoint + '?' + urlencode(params), query)
        if sort_by_score and result.get('next_cursor'):
            result['next_cursor'] = self._score_cursor_prefix + result['next_cursor']
        return result

    def decode(self, body, query):
        message = json.loads(body)['message']
        rows = []
        for item in message['items']:
            doi = item['DOI'].lower()
            parts = item.get('published', {}).get('date-parts', [[]])[0]
            published = int(datetime(*(parts + [1, 1])[:3], tzinfo=timezone.utc).timestamp()) if parts else None
            rows.append(dict(source='crossref', source_id=doi, doi=doi,
                             title=next(iter(item.get('title', [])), '[missing title]'),
                             url='https://doi.org/' + quote(doi, safe='/'), abstract=item.get('abstract', ''),
                             source_type=item.get('type', 'unknown'), peer_review_status='unknown',
                             reference_ids=[r['DOI'].lower() for r in item.get('reference', []) if isinstance(r.get('DOI'), str) and len(r['DOI'].encode()) <= 512][:100],
                             reference_count=item.get('reference-count'), cited_by_count=item.get('is-referenced-by-count'),
                             published_at=published, discovered_at=int(time.time()),
                             missing_fields=[k for k in ('title','abstract','published') if not item.get(k)]))
        cursor = message.get('next-cursor') if len(rows) == min(query['limit'], 20) else None
        return page(rows, cursor, body)

class OpenAlexProvider(AcademicTransport):
    _lock = threading.Lock()
    interval = 1.0
    endpoint = 'https://api.openalex.org'

    def __init__(self, rate_directory=None, *, key=None):
        super().__init__(rate_directory)
        self.key = key

    def _key(self):
        return self.key if self.key is not None else os.environ.get('OPENALEX_API_KEY', '')

    def search(self, query):
        if not self._key():
            return {'error_code': 'unconfigured'}
        return self._fetch(self.endpoint, query)

    def _fetch_locked(self, address, query):
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
        key = self._key()
        if not key:
            return {'error_code': 'unconfigured'}
        deadline = time.monotonic() + query['timeout_seconds']

        def read(path):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError
            request = Request(self.endpoint + path)
            request.add_unredirected_header('Authorization', 'Bearer ' + key)
            with urlopen(request, timeout=min(30, remaining)) as response:
                body = response.read(2 * 1024 * 1024 + 1)
            if len(body) > 2 * 1024 * 1024:
                raise ValueError('Response too large')
            return body

        checking_account = True
        try:
            account = json.loads(read('/rate-limit'))['rate_limit']
            remaining = float(account['daily_remaining_usd'])
            cost = float(account['endpoint_costs_usd']['search'])
            if not math.isfinite(remaining) or not math.isfinite(cost) or cost <= 0 or remaining < cost:
                return {'error_code': 'budget_unknown'}
            checking_account = False
            params = {'search': query['term'], 'per_page': min(query['limit'], 20),
                      'cursor': query['cursor'] or '*'}
            window = query.get('window', {})
            if window.get('basis') == 'published':
                end = datetime.fromtimestamp(window['until_at'] - 1, timezone.utc).date()
                bounds = ['to_publication_date:' + str(end)]
                if query['phase'] != 'foundation':
                    start = datetime.fromtimestamp(window['effective_from_at'], timezone.utc).date()
                    bounds.append('from_publication_date:' + str(start))
                params['filter'] = ','.join(bounds)
            path = '/works?' + urlencode(params)
            body = read(path)
            payload = json.loads(body)
            rows = []
            for item in payload['results']:
                doi = (item.get('doi') or '').removeprefix('https://doi.org/').lower()
                date = item.get('publication_date')
                index = item.get('abstract_inverted_index') or {}
                words = sorted((pos, word) for word, offsets in index.items() for pos in offsets)
                abstract = ' '.join(word for _, word in words)
                rows.append(dict(source='openalex', source_id=item['id'], doi=doi, abstract=abstract,
                                 url='https://doi.org/' + quote(doi, safe='/') if doi else item['id'],
                                 title=item.get('display_name') or '[missing title]',
                                 published_at=int(datetime.fromisoformat(date).replace(tzinfo=timezone.utc).timestamp()) if date else None,
                                 discovered_at=int(time.time()), source_type=item.get('type') or 'unknown',
                                 peer_review_status='unknown',
                                 is_retracted=item.get('is_retracted') if type(item.get('is_retracted')) is bool else None,
                                 reference_ids=[v for v in item.get('referenced_works', []) if isinstance(v,str) and len(v.encode()) <= 512][:100],
                                 reference_count=item.get('referenced_works_count'), cited_by_count=item.get('cited_by_count'),
                                 missing_fields=[k for k in ('doi','publication_date','display_name') if not item.get(k)]))
            cursor = payload['meta'].get('next_cursor') if rows else None
            result = page(rows, cursor, body)
            result['source_query'] = self.endpoint + path
            from hostrun.voyager_service import VoyagerService
            from hostrun.agent_service import CommandError
            try:
                VoyagerService._bounded_page(result)
            except CommandError:
                return {'error_code': 'provider_failed'}
            return result
        except HTTPError as exc:
            if exc.code in (429, 503):
                retry = exc.headers.get('Retry-After', '60')
                try:
                    delay = float(retry)
                except ValueError:
                    try:
                        delay = parsedate_to_datetime(retry).timestamp() - time.time()
                    except (ValueError, TypeError, OverflowError):
                        delay = 60
                type(self)._next = max(type(self)._next, time.monotonic() + delay)
            exc.close()
            return {'error_code': 'authentication' if exc.code in (401,403) else 'rate_limited' if exc.code == 429 else 'provider_failed'}
        except (TimeoutError, URLError):
            return {'error_code': 'timeout'}
        except (KeyError, TypeError, ValueError):
            return {'error_code': 'budget_unknown' if checking_account else 'provider_failed'}
