import { describe, expect, it, vi } from 'vitest';
import { deadlineFetch } from './deadlineFetch';

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
