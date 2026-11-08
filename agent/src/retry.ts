/** One shared attempt budget: at most two additional HTTP attempts per process execution. */
export function retryDecision(message: string, attempt: number, retryAfter?: string, now = Date.now()) {
  const category = /abort|cancel/i.test(message) ? 'cancelled'
    : /insufficient.quota|quota.exceed|budget|billing|credit/i.test(message) ? 'quota'
    : /401|403|api.key|unauthoriz|authentication/i.test(message) ? 'authentication'
    : /configure|configuration|missing|unknown model/i.test(message) ? 'configuration'
    : /429|\b50[0234]\b|timeout|timed.out|ECONNRESET|ECONNREFUSED|fetch failed|network/i.test(message) ? 'transient'
    : 'permanent';
  let requested = 0;
  if (retryAfter) {
    const seconds = Number(retryAfter);
    requested = Number.isFinite(seconds) ? seconds * 1000 : Date.parse(retryAfter) - now;
    if (!Number.isFinite(requested)) requested = 0;
  }
  return { category, retryable: category === 'transient' && attempt < 3,
    delayMs: Math.max(1000 * 2 ** Math.max(0, attempt - 1), requested) };
}

export function boundedFetch(base: typeof fetch, notify: (event: object) => void): typeof fetch {
  let retries = 0;
  return async (input, init) => {
    const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
    while (true) {
      let response: Response | undefined;
      let failure: unknown;
      try { response = await base(input, init); } catch (error) { failure = error; }
      if (response?.ok) return response;
      const detail = response ? `${response.status} ${await response.clone().text()}` : String(failure);
      const decision = retryDecision(detail, retries + 1, response?.headers.get('retry-after') ?? undefined);
      if ((response?.ok) || !decision.retryable || decision.delayMs > 2_147_483_647 || signal?.aborted) {
        if (failure) throw failure;
        return response!;
      }
      await response?.body?.cancel();
      retries++;
      notify({ type: 'retry', attempt: retries + 1, ...decision });
      await new Promise<void>((resolve, reject) => {
        
        const abort = () => { clearTimeout(timer); reject(signal?.reason ?? new Error('aborted')); };
        const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve(); }, decision.delayMs);
        if (signal?.aborted) abort(); else signal?.addEventListener('abort', abort, { once: true });
      });
    }
  };
}
