"""Public web page acquisition and immutable, paragraph-addressable extraction."""
import json
import re
from importlib.metadata import version
from urllib.parse import parse_qsl, urljoin

from hostrun.voyager_subscriptions import fetch_public, public_address as validate_https

MAX_TEXT_BYTES = 2 * 1024 * 1024


def public_address(url):
    address = validate_https(url)
    confidential = {'key','api_key','apikey','token','access_token','signature','password','authorization','sig'}
    for name, _ in parse_qsl(address.query, keep_blank_values=True):
        if name.casefold() in confidential or name.casefold().startswith(('x-amz-', 'x-goog-')):
            raise ValueError('credential_bearing_url')
    return address


def acquire(url, *, dynamic=False, cancel=None):
    import trafilatura
    from lxml import html as lxml_html

    current = url
    for hop in range(5):
        if cancel and cancel.is_set():
            return unavailable('cancelled',current)
        public_address(current)
        status, headers, body = fetch_public(current, {'Accept':'text/html,application/xhtml+xml'})
        headers = {k.lower():v for k,v in headers.items()}
        if status in (301,302,303,307,308) and headers.get('location'):
            if hop==4:
                return unavailable('redirect_limit',current)
            next_url = urljoin(current,headers['location'])
            try:
                public_address(next_url)
            except ValueError:
                return unavailable('redirect_refused',current)
            current = next_url
            continue
        break
    if status != 200:
        return unavailable('login_required' if status==401 else 'payment_required' if status==402 else 'access_denied' if status==403 else 'http_'+str(status),current)
    if headers.get('content-type','').partition(';')[0].strip() not in ('text/html','application/xhtml+xml'):
        return unavailable('not_html',current)
    if cancel and cancel.is_set():
        return unavailable('cancelled',current)
    def extract(raw):
        if not raw.strip():
            return None
        tree=lxml_html.fromstring(raw)
        if tree.xpath('//input[translate(@type,"PASSWORD","password")="password"]') or re.search(rb'"isAccessibleForFree"\s*:\s*(?:false|"false")',raw,re.I):
            return 'restricted'
        extracted=trafilatura.extract(raw,url=current,output_format='json',with_metadata=True,
                                   include_comments=False,include_links=False,favor_precision=True)
        if extracted:
            fields=json.loads(extracted)
            text=(fields.get('text') or '').strip()
            if not text or text==fields.get('title'):
                return None
            if len(text)<300 and re.search(r'(enable javascript|javascript is (required|disabled)|loading[. …]*$)',text,re.I):
                return None
        return extracted
    mode='static'
    article=extract(body)
    if article=='restricted':
        return unavailable('restricted_page',current)
    if dynamic:
        from hostrun.voyager_web_renderer import render_public
        try:
            body=render_public(current,body,cancel=cancel.is_set if cancel is not None else None)
        except (FileNotFoundError,ImportError):
            return unavailable('browser_unavailable',current)
        except ValueError:
            return unavailable('dynamic_capture_failed',current)
        mode='dynamic'
        article=extract(body)
    if not article or article=='restricted':
        return unavailable('restricted_page' if article=='restricted' else 'no_article_body',current)
    fields=json.loads(article)
    lines=[line.strip() for line in (fields.get('text') or '').splitlines() if line.strip()]
    text='\n\n'.join(lines)
    if not text or len(text.encode())>MAX_TEXT_BYTES or len(lines)>4096:
        return unavailable('no_bounded_article_body',current)
    anchors=[]
    offset=0
    for number,line in enumerate(lines,1):
        anchors.append(dict(id='p'+str(number),start=offset,end=offset+len(line)))
        offset+=len(line)+2
    metadata={key:fields[key][:1024] for key in ('title','author','date','sitename') if isinstance(fields.get(key),str)}
    return text,dict(status='available',url=current,mode=mode,metadata=metadata,anchors=anchors),body


def unavailable(reason,url):
    return '',dict(status='unavailable',reason=reason,url=url,anchors=[],metadata={}),b''


def extractor_version():
    return 'trafilatura.'+version('trafilatura')
