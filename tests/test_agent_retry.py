"""Public retry policy contract, independent of provider internals."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_retry_policy_classifies_and_bounds_delays():
    script = """
    import { retryDecision } from './agent/dist/retry.js';
    console.log(JSON.stringify([
      retryDecision('429 rate limit', 1, '3', 0),
      retryDecision('timeout', 2),
      retryDecision('timeout', 3),
      retryDecision('401 invalid API key', 1),
      retryDecision('429 insufficient_quota', 1),
      retryDecision('aborted', 1),
      retryDecision('400 bad request', 1)
    ]));
    """
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    values = json.loads(result.stdout)
    assert values[0] == {'category': 'transient', 'retryable': True, 'delayMs': 3000}
    assert values[1]['delayMs'] == 2000
    assert values[2]['retryable'] is False
    assert [v['category'] for v in values[3:]] == ['authentication', 'quota', 'cancelled', 'permanent']
    assert all(not v['retryable'] for v in values[3:])


def test_retry_fetch_exhaustion_permanent_and_abort():
    script = """
    import { boundedFetch } from './agent/dist/retry.js';
    const events = []; let requests = 0;
    const retry = boundedFetch(async () => { requests++; return new Response('busy', {status:503}); }, e => events.push(e));
    const response = await retry('http://localhost');
    let permanent = 0;
    await boundedFetch(async () => { permanent++; return new Response('invalid key', {status:401}); }, () => {})('http://localhost');
    const controller = new AbortController(); let cancelled = 0;
    try {
      await boundedFetch(async () => { cancelled++; return new Response('busy', {status:429}); }, () => controller.abort())('http://localhost', {signal:controller.signal});
    } catch {}
    console.log(JSON.stringify({requests, status:response.status, permanent, cancelled, attempts:events.map(e=>e.attempt)}));
    """
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'requests': 3, 'status': 503, 'permanent': 1, 'cancelled': 1, 'attempts': [2, 3]}


def test_retry_fetch_real_http_retry_after_and_no_nested_attempts():
    script = """
    import http from 'node:http';
    import { boundedFetch } from './agent/dist/retry.js';
    let requests = 0; const events = [];
    const server = http.createServer((req,res) => {
      requests++; res.writeHead(requests < 3 ? 429 : 200, {'Retry-After':'0'}); res.end(requests < 3 ? 'rate limit' : 'ok');
    });
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
    try {
      const response = await boundedFetch(fetch, event => events.push(event))(`http://127.0.0.1:${server.address().port}`);
      console.log(JSON.stringify({requests, body:await response.text(), attempts:events.map(e=>e.attempt)}));
    } finally { server.closeAllConnections(); server.close(); }
    """
    result = subprocess.run(['node', '--input-type=module', '-e', script], cwd=ROOT, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'requests':3, 'body':'ok', 'attempts':[2,3]}
