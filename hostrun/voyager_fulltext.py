"""Resolve and snapshot open academic full texts for reserved Voyager candidates."""
import hashlib
import json
import os
import re
import tempfile
import time
from http.client import HTTPException
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlunsplit

from common import file_lock
from hostrun.agent_service import CommandError
from hostrun.embedded_agent import load_source_configuration
from hostrun.voyager_subscriptions import fetch_public
from hostrun.voyager_web import public_address

BODY_LIMIT = 10 * 1024 * 1024
BODY_ACCEPT = 'application/pdf,application/xml,text/xml,text/html,application/xhtml+xml'


def doi_of(candidate):
    value = candidate.get('doi', '').strip().lower().removeprefix('https://doi.org/')
    return value if re.fullmatch(r'10\.\d{4,9}/[^\s]+', value) else ''


def native_url(candidate):
    source, identifier = candidate['source'], candidate['source_id']
    if source == 'arxiv' and re.fullmatch(r'(?:\d{4}\.\d{4,5}|[A-Za-z.-]+/\d{7})(?:v\d+)?', identifier):
        return 'https://arxiv.org/pdf/' + identifier
    pmcid = candidate.get('pmcid') or identifier.removeprefix('PMC:')
    if source == 'europepmc' and re.fullmatch(r'PMC[0-9]{1,20}', pmcid):
        return 'https://www.ebi.ac.uk/europepmc/webservices/rest/' + pmcid + '/fullTextXML'
    return None


def audit_url(address):
    query = parse_qs(address.query)
    kept = {}
    if address.hostname == 'journals.plos.org':
        identifier = query.get('id', [''])[0]
        if re.fullmatch(r'10\.1371/journal\.[a-z.]+\.[0-9]+', identifier):
            kept['id'] = identifier
            if query.get('type') == ['manuscript']:
                kept['type'] = 'manuscript'
    elif address.hostname == 'pmc.ncbi.nlm.nih.gov' and address.path == '/api/oai/v1/mh/':
        identifier = query.get('identifier', [''])[0]
        if (query.get('verb') == ['GetRecord'] and query.get('metadataPrefix') == ['pmc']
                and re.fullmatch(r'oai:pubmedcentral\.nih\.gov:[0-9]{1,20}', identifier)):
            kept = {'verb': 'GetRecord', 'identifier': identifier, 'metadataPrefix': 'pmc'}
    return urlunsplit((address.scheme, address.netloc, address.path, urlencode(kept), ''))


def bounded_text(lines):
    parts = [re.sub(r'\s+', ' ', line).strip() for line in lines if line.strip()]
    text = '\n\n'.join(parts)
    if not text or len(parts) > 4096 or len(text.encode('utf8')) > 2 * 1024 * 1024:
        return None
    return text


def interpret(content_type, raw, url):
    if content_type in ('application/pdf', 'application/octet-stream') and raw.startswith(b'%PDF-'):
        return {'kind': 'pdf', 'original': raw}
    if content_type in ('application/xml', 'text/xml', 'application/jats+xml'):
        from lxml import etree
        try:
            document = etree.fromstring(raw, parser=etree.XMLParser(
                load_dtd=False, resolve_entities=False, no_network=True, recover=False))
        except etree.XMLSyntaxError:
            return None
        if etree.QName(document).localname == 'OAI-PMH':
            records = document.xpath('./*[local-name()="GetRecord"]/*[local-name()="record"]/*[local-name()="metadata"]/*[local-name()="article"]')
            if len(records) != 1:
                return None
            document = records[0]
        elif etree.QName(document).localname != 'article':
            return None
        nodes = document.xpath('./*[local-name()="body"]//*[local-name()="title" or local-name()="p"]')
        text = bounded_text(' '.join(node.itertext()) for node in nodes)
        return {'kind': 'xml', 'original': raw, 'text': text} if text else None
    if content_type in ('text/html', 'application/xhtml+xml'):
        from lxml import etree, html
        import trafilatura
        try:
            document = html.fromstring(raw)
        except (ValueError, etree.ParserError):
            return None
        if document.xpath('//input[translate(@type,"PASSWORD","password")="password"]') or re.search(
                rb'"isAccessibleForFree"\s*:\s*(?:false|"false")', raw, re.I):
            return None
        # Require positive evidence of an article body, not an abstract page.
        if not document.xpath(
                '//article[.//p and .//section] | //*[@itemprop="articleBody"] | '
                '//*[contains(concat(" ",normalize-space(@class)," ")," article-body ")]'):
            return None
        extracted = trafilatura.extract(raw, url=url, include_comments=False, favor_precision=True)
        text = bounded_text((extracted or '').splitlines())
        if text and len(text) >= 300:
            return {'kind': 'html', 'original': raw, 'text': text}
    return None


class FulltextResolver:
    def __init__(self, fetch=fetch_public, *, seconds=45, cancelled=None):
        self.fetch = fetch
        self.deadline = time.monotonic() + seconds
        self.cancelled = cancelled or (lambda: False)
        self.attempts = []

    def request(self, url, headers, byte_limit):
        remaining = self.deadline - time.monotonic()
        if self.cancelled() or remaining <= 0:
            raise TimeoutError
        return self.fetch(url, headers, byte_limit=byte_limit, timeout=min(12, remaining))

    def body(self, stage, url):
        for _ in range(5):
            attempt = {'stage': stage}
            self.attempts.append(attempt)
            try:
                address = public_address(url)
                attempt['url'] = audit_url(address)
                status, headers, raw = self.request(url, {'Accept': BODY_ACCEPT}, BODY_LIMIT)
                received = {key.lower(): value for key, value in headers.items()}
                attempt['http_status'] = status
                if status in (301, 302, 303, 307, 308) and received.get('location'):
                    attempt['outcome'] = 'redirected'
                    url = urljoin(url, received['location'])
                    continue
                if status != 200:
                    attempt['outcome'] = 'unavailable'
                    return None
                mime = received.get('content-type', '').partition(';')[0].strip().lower()
                capture = interpret(mime, raw, attempt['url'])
                attempt['outcome'] = 'received' if capture else 'body_not_verified'
                if capture:
                    capture['url'] = attempt['url']
                return capture
            except (OSError, ValueError, HTTPException):
                attempt['outcome'] = 'unavailable'
                return None
        self.attempts.append({'stage': stage, 'outcome': 'redirect_limit'})
        return None

    def locate(self, source, doi, credentials):
        attempt = {'stage': source}
        self.attempts.append(attempt)
        if source == 'unpaywall' and not credentials.get('unpaywall'):
            attempt['outcome'] = 'unconfigured'
            return []
        headers = {'Accept': 'application/json'}
        if source == 'openalex':
            address = 'https://api.openalex.org/works/https://doi.org/' + quote(doi, safe='/')
            if credentials.get('openalex'):
                headers['Authorization'] = 'Bearer ' + credentials['openalex']
        else:
            address = 'https://api.unpaywall.org/v2/' + quote(doi, safe='/')
            address += '?' + urlencode({'email': credentials['unpaywall']})
        # Locator URLs are never recorded or redirected: they may carry contact data.
        try:
            status, _, body = self.request(address, headers, 2 * 1024 * 1024)
            attempt['http_status'] = status
            payload = json.loads(body) if status == 200 else {}
            location = payload.get('best_oa_location')
            if not isinstance(location, dict) or (source == 'openalex' and location.get('is_oa') is not True):
                attempt['outcome'] = 'no_open_location'
                return []
            attempt['outcome'] = 'location_found'
            names = ('pdf_url', 'landing_page_url') if source == 'openalex' else ('url_for_pdf', 'url_for_landing_page', 'url')
            values = (location.get(name) for name in names)
            return list(dict.fromkeys(url for url in values if isinstance(url, str) and not
                any(value.casefold() in unquote(url).casefold() for value in credentials.values() if value)))
        except (OSError, ValueError, AttributeError, HTTPException):
            attempt['outcome'] = 'unavailable'
            return []

    def captures(self, candidate, credentials):
        seen = set()

        def fetch(stage, url):
            if url and url not in seen:
                seen.add(url)
                return self.body(stage, url)
            return None

        first_url = native_url(candidate)
        native = [first_url, *candidate.get('native_fulltext_urls', [])]
        if first_url and candidate['source'] == 'europepmc':
            pmcid = first_url.split('/')[-2]
            native.append('https://pmc.ncbi.nlm.nih.gov/api/oai/v1/mh/?' + urlencode({
                'verb': 'GetRecord', 'identifier': 'oai:pubmedcentral.nih.gov:' + pmcid[3:],
                'metadataPrefix': 'pmc',
            }))
        for url in native:
            capture = fetch('native', url)
            if capture:
                yield capture
        doi = doi_of(candidate)
        if doi:
            for source in ('openalex', 'unpaywall'):
                for url in self.locate(source, doi, credentials):
                    capture = fetch(source + '_open_location', url)
                    if capture:
                        yield capture
        capture = fetch('landing', candidate['url'])
        if capture:
            yield capture


class FulltextService:
    def __init__(self, voyager, materials, *, fetch=fetch_public):
        self.voyager = voyager
        self.materials = materials
        self.fetch = fetch

    def acquire(self, command, *, token, cancelled=None):
        request_id = command.get('request_id')
        if not isinstance(request_id, str) or len(request_id) > 64:
            raise CommandError('invalid_command', '全文获取缺少有效检索标识')
        identity = hashlib.sha256((str(self.voyager.home.resolve())+'\0'+request_id).encode()).hexdigest()
        lock = Path(tempfile.gettempdir())/'horizon-voyager-executions'/(identity+'.lock')
        with file_lock(lock, blocking=False) as acquired:
            if not acquired:
                raise CommandError('execution_busy', '检索或全文获取正在运行')
            return self._acquire(command, token=token, cancelled=cancelled)

    def _acquire(self, command, *, token, cancelled):
        request_id = command['request_id']
        operation = command.get('operation_id')
        if not isinstance(operation, str) or not operation or len(operation) > 256:
            raise CommandError('invalid_command', '全文获取缺少有效操作标识')
        receipt_id = 'academic:' + hashlib.sha256(operation.encode()).hexdigest()
        already_reserved = any(work['work_id'] == operation for work in
            self.voyager.get(request_id, token=token).get('fulltext_work', []))
        if not already_reserved and self.materials.academic_disposition(
                request_id, command.get('candidate_source'), command.get('candidate_id'), token=token):
            raise CommandError('invalid_transition', '此资料已放弃或已有补充全文')
        reservation = self.voyager.reserve_fulltext(command, token=token)
        current = self.voyager.get(request_id, token=token)
        item = next((work for work in current.get('fulltext_work', []) if work['work_id'] == operation), None)
        if item is None or item['status'] != 'reserved':
            return self.voyager.view(request_id, token=token)
        candidate = next(row for row in reservation['candidates'] if
            (row['source'], row['source_id']) == (item['candidate_source'], item['candidate_id']))
        segment = reservation['segments'][-1]
        credentials, _ = load_source_configuration(self.voyager.home)
        openalex = credentials.get('openalex') or os.environ.get('OPENALEX_API_KEY')
        credentials = {name: secret for name, secret in (
            ('openalex', openalex), ('unpaywall', credentials.get('unpaywall'))) if secret}
        seconds = min(45, segment['limits']['seconds'] - segment['used']['seconds'])
        resolver = FulltextResolver(self.fetch, seconds=seconds, cancelled=cancelled)
        previous = self.materials.academic_receipt(receipt_id, token=token)
        if previous is None and already_reserved:
            # A previous process might have reached a remote source. Keep its charged reservation visible.
            return self.voyager.view(request_id, token=token)
        if previous is None:
            for capture in resolver.captures(candidate, credentials):
                if cancelled and cancelled():
                    return self.voyager.view(request_id, token=token)
                if capture['kind'] == 'pdf':
                    remaining = resolver.deadline - time.monotonic()
                    if remaining <= 0:
                        continue
                    capture.update(self.materials.extract_academic_pdf(capture['original'], timeout=min(25, remaining)))
                    if not capture.get('text') or capture['pdf']['status'] != 'extracted_unverified':
                        resolver.attempts[-1]['outcome'] = 'pdf_text_unavailable'
                        continue
                break
            else:
                capture = None
            if cancelled and cancelled():
                return self.voyager.view(request_id, token=token)
            previous = self.materials.register_academic(receipt_id, request_id, candidate,
                capture, resolver.attempts, token=token)
        status = 'succeeded' if previous['academic']['status'] == 'available' else 'failed'
        latest = self.voyager.get(request_id, token=token)
        self.voyager.record_fulltext(dict(schema_version=1, operation_id=receipt_id,
            request_id=request_id, expected_version=latest['version'], work_id=operation,
            status=status, evidence_ref='material:' + previous['material_id']), token=token)
        return self.voyager.view(request_id, token=token)
