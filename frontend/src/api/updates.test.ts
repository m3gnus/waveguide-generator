import { afterEach, describe, expect, it, vi } from 'vitest';
import { getUpdateStatus, installApplicationUpdate } from './updates';

const payload = {
  schemaVersion: 1,
  runningVersion: '2.0.0',
  availability: 'current',
  freshness: 'fresh',
  cached: false,
  release: null,
  checkedAt: '2026-08-22T12:00:00Z',
  nextCheckAt: '2026-08-23T00:00:00Z',
  checkout: {
    kind: 'release',
    branch: 'main',
    head: 'a'.repeat(40),
    atDeclaredTag: true,
    trackedChanges: false,
    aheadCount: 0,
    behindCount: 0,
    updateSupported: true,
    reason: null,
  },
  action: null,
  canInstall: false,
  lastError: null,
  installState: 'idle',
  activeVersion: null,
  downloadedBytes: 0,
  totalBytes: 0,
  error: null,
} as const;

const bundlePayload = {
  ...payload,
  availability: 'available',
  release: {
    version: '2.0.1',
    tag: 'v2.0.1',
    url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1',
    publishedAt: '2026-08-22T12:00:00Z',
    assetsReady: true,
    runtimeId: '222222222222',
    bundleAssets: [{
      name: 'waveguide-generator-app-2.0.1.zip',
      url: 'https://github.com/example/app.zip',
      sha256Url: 'https://github.com/example/app.zip.sha256',
      bytes: 1_500,
      sha256Bytes: 96,
      layer: 'app',
    }, {
      name: 'waveguide-generator-app-2.0.1.manifest.json',
      url: 'https://github.com/example/manifest.json',
      sha256Url: 'https://github.com/example/manifest.json.sha256',
      bytes: 180,
      sha256Bytes: 96,
      layer: 'manifest',
    }, {
      name: 'Waveguide.Generator-2.0.1-macos-arm64.dmg',
      url: 'https://github.com/example/app.dmg',
      sha256Url: 'https://github.com/example/app.dmg.sha256',
      bytes: 9_000,
      sha256Bytes: 96,
      layer: 'installer',
    }],
  },
  checkout: {
    kind: 'bundle',
    branch: null,
    head: null,
    atDeclaredTag: false,
    trackedChanges: false,
    aheadCount: null,
    behindCount: null,
    updateSupported: true,
    installedVersion: '2.0.0',
    runtimeId: '111111111111',
    reason: null,
  },
  action: {
    kind: 'bundle_download',
    assets: [{
      name: 'waveguide-generator-app-2.0.1.zip',
      url: 'https://github.com/example/app.zip',
      sha256Url: 'https://github.com/example/app.zip.sha256',
      bytes: 1_500,
      layer: 'app',
    }],
    downloadBytes: 1_500,
  },
  canInstall: true,
  totalBytes: 1_500,
} as const;

function jsonResponse(value: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => value,
  } as Response;
}

describe('getUpdateStatus', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('uses the cacheable GET endpoint, manual refresh query, and optional cancellation signal', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(payload));
    vi.stubGlobal('fetch', fetchMock);
    const controller = new AbortController();
    await getUpdateStatus();
    await getUpdateStatus(true);
    await getUpdateStatus(false, controller.signal);
    expect(fetchMock.mock.calls).toEqual([
      ['/api/updates/status'],
      ['/api/updates/status?refresh=true'],
      ['/api/updates/status', { signal: controller.signal }],
    ]);
  });

  it('accepts the complete server-shaped bundle release and projected install action', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(bundlePayload)));

    await expect(getUpdateStatus()).resolves.toEqual(bundlePayload);
  });

  it.each([
    ['unknown availability', { ...payload, availability: 'sometimes' }],
    ['unknown freshness', { ...payload, freshness: 'expired' }],
    ['incomplete checkout', { ...payload, checkout: { kind: 'release' } }],
    ['bundle checkout without installed version', {
      ...bundlePayload,
      checkout: { ...bundlePayload.checkout, installedVersion: undefined },
    }],
    ['repository checkout with bundle-only fields', {
      ...payload,
      checkout: { ...payload.checkout, installedVersion: '2.0.0' },
    }],
    ['bundle release without its asset list', {
      ...bundlePayload,
      release: { ...bundlePayload.release, bundleAssets: undefined },
    }],
    ['release asset without checksum size', {
      ...bundlePayload,
      release: {
        ...bundlePayload.release,
        bundleAssets: [{ ...bundlePayload.release.bundleAssets[0], sha256Bytes: undefined }],
      },
    }],
    ['unknown action', { ...payload, action: { kind: 'surprise' } }],
    ['incomplete bundle action asset', {
      ...bundlePayload,
      action: {
        ...bundlePayload.action,
        assets: [{ ...bundlePayload.action.assets[0], bytes: undefined }],
      },
    }],
    ['bundle action with mismatched byte total', {
      ...bundlePayload,
      action: { ...bundlePayload.action, downloadBytes: 1_499 },
    }],
    ['non-string last error', { ...payload, lastError: 5 }],
    ['non-string progress error', { ...payload, error: { detail: 'bad' } }],
    ['missing active version while downloading', {
      ...payload, installState: 'downloading', activeVersion: null,
    }],
    ['NaN byte progress', { ...payload, downloadedBytes: Number.NaN }],
    ['negative total bytes', { ...payload, totalBytes: -1 }],
    ['progress beyond its total', {
      ...payload, installState: 'downloading', activeVersion: '2.0.1', downloadedBytes: 3, totalBytes: 2,
    }],
  ] as [string, unknown][])('rejects %s', async (_label, malformed) => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(malformed)));

    await expect(getUpdateStatus()).rejects.toThrow('Update status response is invalid');
  });

  it('rejects an invalid or failed response', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({})));
    await expect(getUpdateStatus()).rejects.toThrow('invalid');
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({}, 503)));
    await expect(getUpdateStatus()).rejects.toThrow('(503)');
  });
});

describe('installApplicationUpdate', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('requests installation with a non-simple confirmation header', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ accepted: true, tag: 'v2.0.1' }, 202));
    vi.stubGlobal('fetch', fetchMock);

    await expect(installApplicationUpdate()).resolves.toEqual({ accepted: true, tag: 'v2.0.1' });
    expect(fetchMock).toHaveBeenCalledWith('/api/updates/install', {
      method: 'POST',
      headers: { 'X-WG-Update': 'install' },
    });
  });

  it('accepts complete bundle installation progress from the same mutation endpoint', async () => {
    const bundle = {
      accepted: true,
      version: '2.0.1',
      installState: 'downloading',
      activeVersion: '2.0.1',
      downloadedBytes: 1024,
      totalBytes: 4096,
      error: null,
    };
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(bundle, 202)));

    await expect(installApplicationUpdate()).resolves.toEqual(bundle);
  });

  it.each([
    ['missing error field', {
      accepted: true, version: '2.0.1', installState: 'downloading', activeVersion: '2.0.1', downloadedBytes: 1, totalBytes: 2,
    }],
    ['non-string error', {
      accepted: true, version: '2.0.1', installState: 'failed', activeVersion: '2.0.1', downloadedBytes: 1, totalBytes: 2, error: 5,
    }],
    ['NaN progress', {
      accepted: true, version: '2.0.1', installState: 'downloading', activeVersion: '2.0.1', downloadedBytes: Number.NaN, totalBytes: 2, error: null,
    }],
    ['negative total', {
      accepted: true, version: '2.0.1', installState: 'downloading', activeVersion: '2.0.1', downloadedBytes: 1, totalBytes: -1, error: null,
    }],
    ['a different active version', {
      accepted: true, version: '2.0.1', installState: 'downloading', activeVersion: '2.0.0', downloadedBytes: 1, totalBytes: 2, error: null,
    }],
  ] as [string, unknown][])('rejects bundle install progress with %s', async (_label, malformed) => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(malformed, 202)));

    await expect(installApplicationUpdate()).rejects.toThrow('Update installation response is invalid');
  });
});
