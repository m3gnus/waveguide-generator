import { afterEach, describe, expect, it, vi } from 'vitest';
import { getUpdateStatus } from './updates';

const payload = {
  schemaVersion: 1,
  runningVersion: '2.0.0',
  availability: 'current',
  freshness: 'fresh',
  cached: false,
  release: null,
  checkedAt: null,
  nextCheckAt: null,
  checkout: { kind: 'release' },
  action: null,
  lastError: null,
};

describe('getUpdateStatus', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uses the cacheable GET endpoint and an explicit manual-refresh query', async () => {
    const fetchMock = vi.fn(async (_input: RequestInfo | URL) => new Response(JSON.stringify(payload), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    await getUpdateStatus();
    await getUpdateStatus(true);
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      '/api/updates/status',
      '/api/updates/status?refresh=true',
    ]);
  });

  it('rejects an invalid or failed response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })));
    await expect(getUpdateStatus()).rejects.toThrow('invalid');
    vi.stubGlobal('fetch', vi.fn(async () => new Response('offline', { status: 503 })));
    await expect(getUpdateStatus()).rejects.toThrow('(503)');
  });
});
