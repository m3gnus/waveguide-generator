import { afterEach, describe, expect, it, vi } from 'vitest';
import { getUpdateStatus, installApplicationUpdate } from './updates';

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
  canInstall: false,
  lastError: null,
  installState: 'idle',
  downloadedBytes: 0,
  totalBytes: 0,
  error: null,
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

  it('requests installation with a non-simple confirmation header', async () => {
    const fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ accepted: true, tag: 'v2.0.1' }),
      { status: 202 },
    ));
    vi.stubGlobal('fetch', fetchMock);

    await expect(installApplicationUpdate()).resolves.toEqual({ accepted: true, tag: 'v2.0.1' });
    expect(fetchMock).toHaveBeenCalledWith('/api/updates/install', {
      method: 'POST',
      headers: { 'X-WG-Update': 'install' },
    });
  });

  it('accepts bundle installation progress from the same mutation endpoint', async () => {
    const bundle = {
      accepted: true,
      version: '2.0.1',
      installState: 'downloading',
      downloadedBytes: 1024,
      totalBytes: 4096,
      error: null,
    };
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(bundle), { status: 202 })));

    await expect(installApplicationUpdate()).resolves.toEqual(bundle);
  });
});
