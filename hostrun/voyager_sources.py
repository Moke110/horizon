"""Bounded academic discovery providers for the Voyager public service."""
from pathlib import Path
import tempfile
from common import file_lock
import hashlib
import json
import socket
import threading
import time
import urllib.error
from email.utils import parsedate_to_datetime
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

def timestamp(value):
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp())

def page(rows, cursor, body):
    return dict(candidates=rows, exhausted=cursor is None, next_cursor=cursor, receipt=hashlib.sha256(body).hexdigest())

def valid_metadata(result):
    limits = {'source': 80, 'source_id': 512, 'title': 2048, 'abstract': 32768, 'url': 4096, 'source_type': 64, 'peer_review_status': 64}
    for row in result['candidates']:
        for key, limit in limits.items():
            value = row.get(key)
            if not isinstance(value, str) or len(value.encode('utf-8')) > limit:
                return False
        if not row['source_id'] or not row['title']:
            return False
        for key in ('published_at', 'discovered_at'):
            if row.get(key) is not None and (type(row[key]) is not int or row[key] < 0):
                return False
    return True

class AcademicTransport:
    _lock = threading.Lock()
    _next = 0.0
    interval = 0.1

    def __init__(self, rate_directory=None):
        self.rate_directory = Path(rate_directory) if rate_directory is not None else Path(tempfile.gettempdir()) / 'horizon-source-rates'

    def _fetch(self, address, query):
        deadline = time.monotonic() + query['timeout_seconds']
        name = type(self).__name__
        while time.monotonic() < deadline:
            with file_lock(self.rate_directory / (name + '.lock'), blocking=False) as acquired:
                if acquired:
                    state = self.rate_directory / (name + '.next')
                    try:
                        cooldown = float(state.read_text())
                    except FileNotFoundError:
                        cooldown = 0
                    except ValueError:
                        return {'error_code': 'provider_failed'}
                    wait = max(0, cooldown - time.time())
                    if wait >= deadline - time.monotonic():
                        return {'error_code': 'rate_limited'}
                    time.sleep(wait)
                    state.write_text(str(time.time() + min(30, query['timeout_seconds']) + self.interval))
                    bounded = dict(query, timeout_seconds=max(0.001, deadline - time.monotonic()))
                    try:
                        return self._fetch_locked(address, bounded)
                    finally:
                        state.write_text(str(time.time() + max(self.interval, type(self)._next - time.monotonic())))
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        return {'error_code': 'timeout'}

    def _fetch_locked(self, address, query):
        deadline = time.monotonic() + query['timeout_seconds']
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._lock.acquire(timeout=remaining):
            return {'error_code': 'timeout'}
        try:
            wait = max(0, type(self)._next - time.monotonic())
            if wait >= deadline - time.monotonic():
                return {'error_code': 'timeout'}
            time.sleep(wait)
            try:
                request = urllib.request.Request(address, headers={'User-Agent': 'Horizon/1.0 academic discovery'})
                with urllib.request.urlopen(request, timeout=min(30, max(0.001, deadline - time.monotonic()))) as response:
                    if response.status != 200:
                        return {'error_code': 'provider_failed'}
                    body = response.read(2 * 1024 * 1024 + 1)
                if len(body) > 2 * 1024 * 1024:
                    return {'error_code': 'provider_failed'}
                result = self.decode(body, query)
                if 'error_code' not in result:
                    result['source_query'] = address
                    if not valid_metadata(result):
                        return {'error_code': 'provider_failed'}
                return result
            except urllib.error.HTTPError as exc:
                retry = exc.headers.get('Retry-After', '0') if exc.headers else '0'
                try:
                    delay = max(0, float(retry))
                except ValueError:
                    try:
                        delay = max(0, parsedate_to_datetime(retry).timestamp() - time.time())
                    except (ValueError, TypeError, OverflowError):
                        delay = 60
                if exc.code in (429, 503):
                    type(self)._next = max(type(self)._next, time.monotonic() + delay)
                exc.close()
                return {'error_code': 'rate_limited' if exc.code in (429, 503) else 'authentication' if exc.code in (401, 403) else 'provider_failed'}
            except TimeoutError:
                return {'error_code': 'timeout'}
            except urllib.error.URLError as exc:
                return {'error_code': 'timeout' if isinstance(exc.reason, (TimeoutError, socket.timeout)) else 'provider_failed'}
            except (ValueError, KeyError, TypeError, AttributeError, ET.ParseError):
                return {'error_code': 'provider_failed'}
            finally:
                type(self)._next = max(type(self)._next, time.monotonic() + self.interval)
        finally:
            self._lock.release()

class EuropePMCProvider(AcademicTransport):
    endpoint = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
    _lock = threading.Lock()
    _next = 0.0

    def search(self, query):
        terms = ['"' + query['term'].replace('"', ' ') + '"']


        window = query['window']
        if window['basis'] == 'published':
            dates = [datetime.fromtimestamp(window[k], timezone.utc).strftime('%Y-%m-%d') for k in ('effective_from_at', 'until_at')]
            terms.append('AND FIRST_PDATE:[' + dates[0] + ' TO ' + dates[1] + ']')
        params = dict(query=' '.join(terms), format='json', resultType='core', pageSize=min(query['limit'], 100), cursorMark=query['cursor'] or '*')
        address = self.endpoint + '?' + urllib.parse.urlencode(params)
        return self._fetch(address, query)

    def decode(self, body, query):
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get('resultList'), dict) or (not isinstance(payload['resultList'].get('result'), list)):
            return {'error_code': 'provider_failed'}
        rows = []
        if len(payload['resultList']['result']) > min(query['limit'], 100):
            return {'error_code': 'provider_failed'}
        for item in payload.get('resultList', {}).get('result', []):
            source = item.get('source')
            ident = item.get('id')
            if not isinstance(source, str) or not source or (not isinstance(ident, str)) or (not ident):
                return {'error_code': 'provider_failed'}
            published = timestamp(item.get('firstPublicationDate'))
            types = (item.get('pubTypeList') or {}).get('pubType', [])
            if not isinstance(types, list) or any(not isinstance(t, str) for t in types):
                return {'error_code': 'provider_failed'}
            source_type = 'preprint' if source == 'PPR' else next(iter(types), 'unknown')
            missing = [name for name, value in (
                ('title', item.get('title')), ('published_at', published),
                ('abstract', item.get('abstractText')), ('peer_review_status', None),
                ('source_type', None if source_type == 'unknown' else source_type),
            ) if not value]
            rows.append(dict(
                source='europepmc', source_id=source + ':' + ident,
                title=item.get('title') or '[missing title]', abstract=item.get('abstractText') or '',
                url='https://europepmc.org/article/' + source + '/' + urllib.parse.quote(ident, safe=''),
                published_at=published, discovered_at=int(time.time()), source_type=source_type,
                peer_review_status='unknown', missing_fields=missing,
            ))
        cursor = payload.get('nextCursorMark')
        if not rows or cursor == query.get('cursor') or len(rows) < min(query['limit'], 100):
            cursor = None
        return page(rows, cursor, body)

class ArxivProvider(AcademicTransport):
    _lock = threading.Lock()
    _next = 0.0
    interval = 3.0

    def search(self, query):
        quote = lambda text: 'all:"' + text.replace('"', ' ') + '"'
        terms = [quote(query['term'])]
        window = query['window']
        if window['basis'] == 'published':
            dates = [datetime.fromtimestamp(window[k], timezone.utc).strftime('%Y%m%d%H%M') for k in ('effective_from_at', 'until_at')]
            terms.append('AND submittedDate:[' + dates[0] + ' TO ' + dates[1] + ']')
        params = dict(search_query=' '.join(terms), start=int(query['cursor'] or 0), max_results=min(query['limit'], 100), sortBy='submittedDate', sortOrder='descending')
        return self._fetch('https://export.arxiv.org/api/query?' + urllib.parse.urlencode(params), query)

    def decode(self, body, query):
        root = ET.fromstring(body)
        if root.tag != '{http://www.w3.org/2005/Atom}feed':
            return {'error_code': 'provider_failed'}
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        rows = []
        for entry in root.findall('a:entry', ns):

            def field(name):
                return ' '.join((entry.findtext('a:' + name, '', ns) or '').split())
            ident = field('id').partition('/abs/')[2]
            if not ident:
                return {'error_code': 'provider_failed'}
            rows.append(dict(source='arxiv', source_id=ident, title=field('title') or '[missing title]', abstract=field('summary'), url='https://arxiv.org/abs/' + ident, published_at=timestamp(field('published')), discovered_at=int(time.time()), source_type='preprint', peer_review_status='unknown', is_preprint=True, missing_fields=[key for key, value in [('title', field('title')), ('abstract', field('summary')), ('published_at', field('published'))] if not value]))
        if len(rows) > min(query['limit'], 100):
            return {'error_code': 'provider_failed'}
        count = len(rows)
        cursor = str(int(query['cursor'] or 0) + count) if count == min(query['limit'], 100) else None
        return page(rows, cursor, body)

def default_providers():
    return {'arxiv': ArxivProvider(), 'europepmc': EuropePMCProvider()}
