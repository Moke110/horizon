"""Explain Voyager selection from retrieved metadata and explicit scope terms."""
import hashlib
import html
import json
import re
import unicodedata
from urllib.parse import urlsplit, urlunsplit


_HAN = r'[㐀-䶿一-鿿]'
_WORDS = re.compile(rf'{_HAN}+|[^\W_]+')
_COMMON_WORDS = frozenset('a an and as at by for from in into is of on or the to with about study research'.split())
_RETRACTION = re.compile(r'^(?:retracted|retraction|withdrawal)(?:\b|:)|^(?:撤稿|撤回)')
_CORRECTION = re.compile(r'^(?:correction|erratum|corrigendum)(?:\b|:)|^(?:更正|勘误)')
_COUNTEREVIDENCE = re.compile(r'\b(?:counterevidence|no evidence|no significant|failed to replicate|negative results?)\b|反证|未发现证据|未能复现|无显著')
_REPOST = re.compile(r'\b(?:originally published (?:at|by|in|on)|republished (?:from|with permission))\b|转载自|来源转载')


def _normalize(text):
    return unicodedata.normalize('NFKC', html.unescape(text)).casefold()


def _terms(text):
    for match in _WORDS.finditer(_normalize(text)):
        term = match.group()
        if re.fullmatch(rf'{_HAN}+', term):
            yield from (term[i:i + 2] for i in range(max(1, len(term) - 1)))
        elif term not in _COMMON_WORDS:
            yield term


def selection_reason(row, query):
    """Describe observed relevance and evidence signals, not factual reliability."""
    title = _normalize(row['title']).strip()
    text = title + ' ' + _normalize(row.get('abstract', ''))
    terms = list(dict.fromkeys(_terms(query['term'])))
    observed = set(_terms(text))
    matched = [term for term in terms if term in observed]
    required = query['must_include']
    if not required and (not terms or len(matched) < max(min(2, len(terms)), (len(terms) + 1) // 2)):
        return None

    source_type = row.get('source_type', '').casefold()
    if (row.get('is_retracted') is True
            or source_type in ('retraction', 'retraction of publication', 'retracted publication')
            or _RETRACTION.search(title)):
        kind = 'retraction'
    elif source_type in ('correction', 'published erratum') or _CORRECTION.search(title):
        kind = 'correction'
    elif _COUNTEREVIDENCE.search(text):
        kind = 'counterevidence'
    else:
        kind = 'scope_match'
    return {'kind': kind, 'matched_terms': (matched or required)[:12],
            'reposted': bool(_REPOST.search(text))}


def candidate_identity(row):
    if row.get('canonical_id'):
        return row['canonical_id']
    try:
        parts = urlsplit(row.get('url', ''))
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            raise ValueError('not a usable source URL')
    except ValueError:
        native = json.dumps([row['source'], row['source_id']])
        return 'source:' + hashlib.sha256(native.encode()).hexdigest()
    # Preserve meaningful query parameters and path spelling. A fragment only
    # locates a part of the same source.
    address = urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or '/', parts.query, ''))
    return 'url:' + hashlib.sha256(address.encode()).hexdigest()


def scope_identity(scope):
    if scope.get('frontier_id'):
        return 'frontier:' + str(scope['frontier_id'])
    constraints = [_normalize(scope['text']).strip()]
    constraints.extend(sorted(_normalize(item).strip() for item in scope.get(field, []))
                       for field in ('must_include', 'exclude'))
    return 'explicit:' + hashlib.sha256(json.dumps(constraints).encode()).hexdigest()


def candidate_change(row):
    reason = row['selection_reason']['kind']
    if reason not in ('correction', 'retraction'):
        return 'discovery'
    title = ' '.join(_normalize(row['title']).split())
    return reason + ':' + hashlib.sha256(title.encode()).hexdigest()
