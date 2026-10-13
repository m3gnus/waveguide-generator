import { describe, expect, it, vi } from 'vitest';
import { deadlineFetch, deadlineSignal } from './deadlineFetch';

describe('REST read deadlines', () => {
  it('preserves lifetime cancellation and request options', async () => {
    const lifetime = new AbortController();
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}'));
    await deadlineFetch(fetcher, '/api/jobs', { signal: lifetime.signal, headers: { Accept: 'application/json' } });
    const init = fetcher.mock.calls[0][1];
    expect(init?.headers).toEqual({ Accept: 'application/json' });
    expect(init?.signal?.aborted).toBe(false);
    lifetime.abort();
    expect(init?.signal?.aborted).toBe(true);
  });

  it('keeps the deadline active after headers while the body is read', async () => {
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => ({
      json: () => new Promise((_resolve, reject) => {
        init!.signal!.addEventListener('abort', () => reject(init!.signal!.reason), { once: true });
      }),
    } as Response));
    const response = await deadlineFetch(fetcher, '/api/results/job', undefined, 10);
    await expect(response.json()).rejects.toMatchObject({ name: 'TimeoutError' });
  });

  it('preserves an already cancelled lifetime', async () => {
    const lifetime = new AbortController();
    lifetime.abort('unmounted');
    const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      expect(init?.signal?.aborted).toBe(true);
      expect(init?.signal?.reason).toBe('unmounted');
      return new Response('{}');
    });
    await deadlineFetch(fetcher, '/api/jobs', { signal: lifetime.signal });
  });

  it('inherits a Request lifetime unless options explicitly replace it', async () => {
    const lifetime = new AbortController();
    const request = new Request('http://localhost/api/jobs', { signal: lifetime.signal });
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}'));
    await deadlineFetch(fetcher, request);
    await deadlineFetch(fetcher, request, { signal: null });
    lifetime.abort();
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(fetcher.mock.calls[1][1]?.signal?.aborted).toBe(false);
  });

  it('leaves mutation reconciliation and keepalive unchanged', async () => {
    const init = { method: 'POST', keepalive: true, body: '{}' };
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}'));
    await deadlineFetch(fetcher, '/api/solve', init);
    expect(fetcher).toHaveBeenCalledWith('/api/solve', init);
  });
});

describe.each(['any', 'timeout', 'both'] as const)('WebView fallback without %s', (missing) => {
  function withoutHelpers(run: () => void) {
    const any = Object.getOwnPropertyDescriptor(AbortSignal, 'any')!;
    const timeout = Object.getOwnPropertyDescriptor(AbortSignal, 'timeout')!;
    if (missing !== 'timeout') Object.defineProperty(AbortSignal, 'any', { value: undefined, configurable: true });
    if (missing !== 'any') Object.defineProperty(AbortSignal, 'timeout', { value: undefined, configurable: true });
    vi.useFakeTimers();
    try { run(); } finally {
      Object.defineProperty(AbortSignal, 'any', any);
      Object.defineProperty(AbortSignal, 'timeout', timeout);
      vi.useRealTimers();
    }
  }

  it('reaches fetch with a composed signal on legacy WebViews', () => withoutHelpers(() => {
    const lifetime = new AbortController();
    const fetcher = vi.fn(async (_input: RequestInfo | URL, _init?: RequestInit) => new Response('{}'));
    void deadlineFetch(fetcher, '/api/jobs', { signal: lifetime.signal });
    expect(fetcher).toHaveBeenCalledWith('/api/jobs', { signal: expect.any(AbortSignal) });
    lifetime.abort('unmounted');
    expect(fetcher.mock.calls[0][1]?.signal?.reason).toBe('unmounted');
    expect(vi.getTimerCount()).toBe(0);
  }));

  it('preserves the lifetime reason and clears the timer and listener', () => withoutHelpers(() => {
    const lifetime = new AbortController();
    const remove = vi.spyOn(lifetime.signal, 'removeEventListener');
    const composed = deadlineSignal(lifetime.signal, 10);
    const reason = new Error('component unmounted');
    lifetime.abort(reason);
    expect(composed.reason).toBe(reason);
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(20);
    expect(composed.reason).toBe(reason);
  }));

  it('expires after headers while the body still waits and removes the lifetime listener', () => withoutHelpers(() => {
    const lifetime = new AbortController();
    const remove = vi.spyOn(lifetime.signal, 'removeEventListener');
    const composed = deadlineSignal(lifetime.signal, 10);
    const bodyAbort = vi.fn();
    composed.addEventListener('abort', bodyAbort);
    vi.advanceTimersByTime(9);
    expect(bodyAbort).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(bodyAbort).toHaveBeenCalledOnce();
    expect(composed.reason.name).toBe('TimeoutError');
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
    const firstReason = composed.reason;
    lifetime.abort(new Error('late cancellation'));
    expect(composed.reason).toBe(firstReason);
  }));

  it('does not install listeners or timers for an already cancelled lifetime', () => withoutHelpers(() => {
    const lifetime = new AbortController();
    lifetime.abort('already unmounted');
    const add = vi.spyOn(lifetime.signal, 'addEventListener');
    const composed = deadlineSignal(lifetime.signal, 10);
    expect(composed.reason).toBe('already unmounted');
    expect(add).not.toHaveBeenCalled();
    expect(vi.getTimerCount()).toBe(0);
  }));
});
