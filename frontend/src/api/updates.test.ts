import { afterEach, describe, expect, it, vi } from 'vitest';
import { getUpdateChannel, getUpdateStatus, installApplicationUpdate, setUpdateChannel } from './updates';

const payload = {
  schemaVersion: 1,
  runningVersion: '2.0.0',
  channel: 'stable',
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
      name: 'update-app-2.0.1.zip',
      url: 'https://github.com/example/app.zip',
      sha256Url: 'https://github.com/example/app.zip.sha256',
      bytes: 1_500,
      sha256Bytes: 96,
      layer: 'app',
    }, {
      name: 'update-app-2.0.1.manifest.json',
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
      name: 'update-app-2.0.1.zip',
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

const APP_DIGEST = 'a'.repeat(64);
const MANIFEST_DIGEST = 'b'.repeat(64);

/**
 * The live shape: no sidecar files, one `sha256` per asset.
 *
 * Taken from a real `/api/updates/status` response for the v0.3.1 standalone
 * app, with the URLs and digests shortened.
 */
const inlineDigestPayload = {
  ...bundlePayload,
  release: {
    ...bundlePayload.release,
    bundleAssets: [{
      name: 'update-app-2.0.1.zip',
      url: 'https://github.com/example/app.zip',
      sha256: APP_DIGEST,
      bytes: 1_500,
      layer: 'app',
    }, {
      name: 'update-app-2.0.1.manifest.json',
      url: 'https://github.com/example/manifest.json',
      sha256: MANIFEST_DIGEST,
      bytes: 180,
      layer: 'manifest',
    }],
  },
  action: {
    kind: 'bundle_download',
    assets: [{
      name: 'update-app-2.0.1.zip',
      url: 'https://github.com/example/app.zip',
      sha256: APP_DIGEST,
      bytes: 1_500,
      layer: 'app',
    }],
    downloadBytes: 1_500,
  },
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

  it("accepts GitHub's own per-asset digest in place of a .sha256 sidecar", async () => {
    // The shape every release cut since the server moved to `digest` actually
    // publishes -- see `UpdateService._paired_asset`. Refusing it rejected the
    // whole status payload, and every packaged install read "status unknown"
    // for its entire life as a result.
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(inlineDigestPayload)));

    await expect(getUpdateStatus()).resolves.toEqual(inlineDigestPayload);
  });

  it('accepts a release that mixes both digest shapes across its assets', async () => {
    // A release published across the changeover carries one of each.
    const mixed = {
      ...inlineDigestPayload,
      release: {
        ...inlineDigestPayload.release,
        bundleAssets: [
          inlineDigestPayload.release.bundleAssets[0],
          bundlePayload.release.bundleAssets[1],
        ],
      },
    };
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(mixed)));

    await expect(getUpdateStatus()).resolves.toEqual(mixed);
  });

  it.each([
    ['a missing channel', { ...payload, channel: undefined }],
    ['an unknown channel', { ...payload, channel: 'nightly' }],
    ['a release tag that disagrees with its pre-release version', {
      ...payload,
      availability: 'available',
      release: {
        version: '2.1.0-beta.1',
        tag: 'v2.1.0',
        url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.1.0',
        publishedAt: '2026-08-22T12:00:00Z',
        assetsReady: true,
      },
    }],
    ['a version carrying build metadata', {
      ...payload,
      availability: 'available',
      release: {
        version: '2.1.0+abc',
        tag: 'v2.1.0+abc',
        url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.1.0+abc',
        publishedAt: '2026-08-22T12:00:00Z',
        assetsReady: true,
      },
    }],
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
    ['release asset with no proof at all', {
      ...inlineDigestPayload,
      release: {
        ...inlineDigestPayload.release,
        bundleAssets: [{ ...inlineDigestPayload.release.bundleAssets[0], sha256: undefined }],
      },
    }],
    ['release asset with a truncated digest', {
      ...inlineDigestPayload,
      release: {
        ...inlineDigestPayload.release,
        bundleAssets: [{ ...inlineDigestPayload.release.bundleAssets[0], sha256: 'a'.repeat(63) }],
      },
    }],
    ['release asset whose digest is not hex', {
      ...inlineDigestPayload,
      release: {
        ...inlineDigestPayload.release,
        bundleAssets: [{ ...inlineDigestPayload.release.bundleAssets[0], sha256: `${'a'.repeat(63)}z` }],
      },
    }],
    ['bundle action asset with no proof at all', {
      ...inlineDigestPayload,
      action: {
        ...inlineDigestPayload.action,
        assets: [{ ...inlineDigestPayload.action.assets[0], sha256: undefined }],
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

describe('the beta channel', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('accepts a pre-release version and its tag', async () => {
    const beta = {
      ...payload,
      channel: 'beta',
      availability: 'available',
      release: {
        version: '2.1.0-beta.1',
        tag: 'v2.1.0-beta.1',
        url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.1.0-beta.1',
        publishedAt: '2026-08-22T12:00:00Z',
        assetsReady: true,
      },
    };
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(beta)));

    await expect(getUpdateStatus()).resolves.toEqual(beta);
  });

  it('accepts a beta install running ahead of the latest stable release', async () => {
    const ahead = { ...payload, availability: 'ahead', runningVersion: '2.1.0-beta.1' };
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(ahead)));

    await expect(getUpdateStatus()).resolves.toEqual(ahead);
  });

  it('reads the stored channel', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ channel: 'beta' }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(getUpdateChannel()).resolves.toBe('beta');
    expect(fetchMock).toHaveBeenCalledWith('/api/updates/channel');
  });

  it('writes the chosen channel and returns what the server stored', async () => {
    const fetchMock = vi.fn(async () => jsonResponse({ channel: 'beta' }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(setUpdateChannel('beta')).resolves.toBe('beta');
    expect(fetchMock).toHaveBeenCalledWith('/api/updates/channel', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: '{"channel":"beta"}',
    });
  });

  it('surfaces the server-supplied reason a channel change was refused', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ detail: 'Unsupported update channel' }, 400)));

    await expect(setUpdateChannel('beta')).rejects.toThrow('Unsupported update channel');
  });

  it.each([
    ['an unknown channel', { channel: 'nightly' }],
    ['no channel at all', {}],
  ] as [string, unknown][])('rejects a channel response naming %s', async (_label, malformed) => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(malformed)));
    await expect(getUpdateChannel()).rejects.toThrow('Update channel response is invalid');
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(malformed)));
    await expect(setUpdateChannel('beta')).rejects.toThrow('Update channel response is invalid');
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
