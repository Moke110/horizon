"""Persistent Voyager feed and bounded section subscriptions."""
import calendar
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit, unquote

import feedparser
from hostrun.managed_database import database_connection


def public_address(url):
    parts = urlsplit(url)
    if (len(url.encode('utf-8')) > 4096 or parts.scheme != 'https' or not parts.hostname or parts.username
            or parts.password or parts.port not in (None, 443) or parts.fragment):
        raise ValueError('invalid_public_https_url')
    return parts


def fetch_public(url, headers):
    parts = public_address(url)
    addresses = socket.getaddrinfo(parts.hostname, 443, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global for row in addresses):
        raise ValueError('nonpublic_address')
    family, kind, protocol, _, destination = addresses[0]
    with socket.socket(family, kind, protocol) as stream:
        stream.settimeout(12)
        stream.connect(destination)
        with ssl.create_default_context().wrap_socket(stream, server_hostname=parts.hostname) as secure:
            connection = http.client.HTTPSConnection(parts.hostname, timeout=12)
            connection.sock = secure
            try:
                connection.request('GET', urlunsplit(('', '', parts.path or '/', parts.query,'')),
                                   headers={'User-Agent':'Horizon/1.0 feed discovery', **headers})
                response = connection.getresponse()
                body = response.read(2*1024*1024+1)
                if len(body) > 2*1024*1024:
                    raise ValueError('response_too_large')
                return response.status, dict(response.getheaders()), body
            finally:
                connection.close()


class Subscriptions:
    def __init__(self, home, *, fetch=fetch_public):
        self.home = home
        self.fetch = fetch
        with database_connection(home) as db:
            db.execute('CREATE TABLE IF NOT EXISTS voyager_subscriptions (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS voyager_subscription_items (subscription TEXT, id TEXT, digest TEXT, version INTEGER, PRIMARY KEY(subscription,id))')
            db.execute('CREATE TABLE IF NOT EXISTS voyager_subscription_versions (subscription TEXT NOT NULL, found_at INTEGER NOT NULL, entry TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS voyager_subscription_versions_by_source ON voyager_subscription_versions(subscription,found_at)')
            db.commit()

    def configure(self, kind, url):
        if kind not in ('feed','section'):
            raise ValueError('invalid_subscription_type')
        public_address(url)
        identity = hashlib.sha256((kind+' '+url).encode()).hexdigest()[:32]
        value = dict(id=identity, kind=kind, url=url, version=0, last_success=None,
                     gap=None, changes=[], etag=None, last_modified=None)
        with database_connection(self.home) as db:
            db.execute('INSERT OR IGNORE INTO voyager_subscriptions VALUES (?,?)',(identity,json.dumps(value)))
            db.commit()
        return self.get(identity)

    def list(self):
        with database_connection(self.home) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT state FROM voyager_subscriptions ORDER BY rowid DESC LIMIT 100')]

    def summaries(self):
        return [dict(id=row['id'],kind=row['kind'],url=row['url'],last_success=row['last_success'],
                     gap=row['gap'],change_count=len(row['changes']),
                     changes=[{k:item[k] for k in ('title','url','version')} for item in row['changes'][:10]])
                for row in self.list()]

    def get(self, identity):
        with database_connection(self.home) as db:
            row = db.execute('SELECT state FROM voyager_subscriptions WHERE id=?',(identity,)).fetchone()
        if not row:
            raise ValueError('unknown_subscription')
        return json.loads(row[0])

    def refresh(self, identity):
        before = self.get(identity)
        headers = {}
        for field, header in [('etag','If-None-Match'),('last_modified','If-Modified-Since')]:
            if before['kind']=='feed' and before[field]:
                headers[header] = before[field]
        try:
            status, response_headers, body = self.fetch(before['url'],headers)
            received = {k.lower():v for k,v in response_headers.items()}
            if status == 304 and headers:
                entries = []
            elif status == 200:
                entries = discover(before,body) if before['kind']=='feed' else section_entries(before['url'], body, self.fetch)
            else:
                raise ValueError('http_'+str(status))
            gap = None
        except (OSError, ValueError, http.client.HTTPException) as exc:
            reason = str(exc)
            gap = reason if reason in ('invalid_public_https_url','nonpublic_address','response_too_large','malformed_feed','too_many_items') or (reason.startswith('http_') and reason[5:].isdecimal()) else 'source_unavailable'
        with database_connection(self.home) as db:
            db.execute('BEGIN IMMEDIATE')
            current = json.loads(db.execute('SELECT state FROM voyager_subscriptions WHERE id=?',(identity,)).fetchone()[0])
            if current['version'] != before['version']:
                return current
            current.update(version=current['version']+1, gap=gap, changes=[])
            if gap is None:
                current['last_success'] = int(time.time())
                if status == 200:
                    current.update(etag=received.get('etag'), last_modified=received.get('last-modified'))
                for entry in entries:
                    digest = hashlib.sha256(json.dumps(entry,sort_keys=True).encode()).hexdigest()
                    old = db.execute('SELECT digest,version FROM voyager_subscription_items WHERE subscription=? AND id=?',(identity,entry['id'])).fetchone()
                    if old and old[0]==digest:
                        continue
                    version = old[1]+1 if old else 1
                    db.execute('INSERT OR REPLACE INTO voyager_subscription_items VALUES (?,?,?,?)',(identity,entry['id'],digest,version))
                    revision = dict(entry,version=version)
                    current['changes'].append(revision)
                    db.execute('INSERT INTO voyager_subscription_versions VALUES (?,?,?)',
                               (identity,current['last_success'],json.dumps(revision)))
            db.execute('UPDATE voyager_subscriptions SET state=? WHERE id=?',(json.dumps(current),identity))
            db.commit()
            return current


def discover(subscription, body):
    feed = feedparser.parse(body)
    if feed.bozo or not feed.version:
        raise ValueError('malformed_feed')
    if len(feed.entries)>500:
        raise ValueError('too_many_items')
    entries = {}
    for item in feed.entries:
        url = urljoin(subscription['url'],item.get('link',''))
        title = item.get('title','').strip()
        if not title or not item.get('link'):
            continue
        try:
            public_address(url)
        except ValueError:
            continue
        identity = item.get('id') or url
        entries.setdefault(identity, dict(id=identity,url=url,title=bounded_title(title),
                            summary=item.get('summary','')[:4096],
                            published_at=calendar.timegm(item.published_parsed) if item.get('published_parsed') else None))
    return list(entries.values())


def bounded_title(title):
    return title.encode('utf-8')[:2048].decode('utf-8', errors='ignore')


class SectionLinks(HTMLParser):
    def __init__(self, base, root):
        super().__init__()
        self.base = base
        self.root = public_address(root)
        self.entries = []
        self.next_url = None
        self.anchor = None

    def handle_starttag(self, tag, attributes):
        if tag != 'a':
            return
        attrs = dict(attributes)
        self.anchor = None
        href = attrs.get('href')
        if not href:
            return
        url = urljoin(self.base, href).split('#')[0]
        try:
            link = public_address(url)
        except ValueError:
            return
        prefix = self.root.path.rstrip('/') + '/'
        path = unquote(link.path)
        if (link.hostname != self.root.hostname or not (path == self.root.path or path.startswith(prefix))
                or any(segment in ('.','..') for segment in path.split('/')) or '%' in path or chr(92) in path):
            return
        if 'next' in (attrs.get('rel') or '').split():
            self.next_url = url
        elif url != self.base:
            self.anchor = (url, [])

    def handle_data(self, text):
        if self.anchor:
            self.anchor[1].append(text)

    def handle_endtag(self, tag):
        if tag=='a' and self.anchor:
            url, pieces = self.anchor
            title=' '.join(''.join(pieces).split())
            if title:
                self.entries.append(dict(id=url,url=url,title=bounded_title(title),summary=''))
            self.anchor=None


def section_entries(root, body, fetch):
    current=root
    visited=set()
    entries={}
    for page_number in range(3):
        visited.add(current)
        links=SectionLinks(current,root)
        links.feed(body.decode('utf-8',errors='replace'))
        for entry in links.entries:
            entries.setdefault(entry['id'],entry)
        if len(entries)>500:
            raise ValueError('too_many_items')
        current=links.next_url
        if not current or current in visited or page_number==2:
            break
        status, _, body=fetch(current,{})
        if status!=200:
            raise ValueError('http_'+str(status))
    return list(entries.values())

class SubscriptionProvider:
    def __init__(self, subscriptions, identity):
        self.subscriptions = subscriptions
        self.identity = identity

    def search(self, query):
        state = self.subscriptions.get(self.identity)
        if state['last_success'] is None or state['gap']:
            return {'error_code':'provider_failed'}
        offset = int(query['cursor'] or 0)
        size = min(100,query['limit'])
        with database_connection(self.subscriptions.home) as db:
            records = db.execute('SELECT rowid,found_at,entry FROM voyager_subscription_versions WHERE subscription=? AND rowid>? ORDER BY rowid LIMIT ?',
                                 (self.identity,offset,size+1)).fetchall()
        rows=[]
        for seq, found, raw in records[:size]:
            entry=json.loads(raw)
            identity=hashlib.sha256(entry['id'].encode()).hexdigest() + '.v' + str(entry['version'])
            text=(entry['title']+' '+entry['summary']).casefold()
            if query['term'].casefold() not in text:
                continue
            rows.append(dict(source=query['source'],source_id=identity,url=entry['url'],title=entry['title'],
                             abstract=entry['summary'],published_at=entry.get('published_at'),discovered_at=found,
                             source_type=state['kind'],peer_review_status='unknown',
                             missing_fields=[] if entry.get('published_at') is not None else ['published_at']))
        cursor=str(records[size-1][0]) if len(records)>size else None
        return dict(candidates=rows,exhausted=cursor is None,next_cursor=cursor,receipt='subscription:'+self.identity+':'+str(offset))
