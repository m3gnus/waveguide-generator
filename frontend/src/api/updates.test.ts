import { afterEach, describe, expect, it, vi } from 'vitest';
import { getUpdateChannel, getUpdateStatus, installApplicationUpdate, setUpdateChannel, type UpdateStatus } from './updates';

export function installerStatus(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    schemaVersion: 2, runningVersion: __WG2_VERSION__, channel: 'stable', availability: 'available', freshness: 'fresh',
    cached: false, checking: false, checkedAt: '2026-10-02T06:00:00Z', nextCheckAt: '2026-10-02T12:00:00Z', lastError: null,
    release: { version: '2.0.1', tag: 'v2.0.1', url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1',
      publishedAt: '2026-10-02T06:00:00Z', notes: 'Improved installation.', assetsReady: true,
      installer: { name: 'Waveguide.Generator-2.0.1-macos-arm64.dmg', url: 'https://github.com/m3gnus/waveguide-generator/releases/download/v2.0.1/Waveguide.Generator-2.0.1-macos-arm64.dmg', size: 244_000_000, sha256: 'a'.repeat(64) } },
    checkout: { kind: 'macos', installRoot: '/Applications/Waveguide Generator.app', updateSupported: true, reason: null },
    action: { kind: 'full_installer', version: '2.0.1', tag: 'v2.0.1', name: 'Waveguide.Generator-2.0.1-macos-arm64.dmg', size: 244_000_000 },
    canInstall: true, installState: 'idle', activeVersion: null, downloadedBytes: 0, totalBytes: 0, error: null,
    lastOutcome: null, ...overrides,
  };
}
const accepted = { accepted: true, version: '2.0.1', activeVersion: '2.0.1', installState: 'downloading', downloadedBytes: 0, totalBytes: 244_000_000, error: null };
const reply = (payload: unknown, status = 200) => new Response(JSON.stringify(payload), { status });
afterEach(() => vi.unstubAllGlobals());

describe('full-installer API', () => {
  it('reads status, manual refresh and abort signals without requesting installation', async () => {
    const fetch = vi.fn(async () => reply(installerStatus())); vi.stubGlobal('fetch', fetch);
    const controller = new AbortController();
    await expect(getUpdateStatus()).resolves.toEqual(installerStatus());
    await getUpdateStatus(true, controller.signal);
    expect(fetch.mock.calls).toEqual([['/api/updates/status', { signal: undefined }], ['/api/updates/status?refresh=true', { signal: controller.signal }]]);
  });
  it('accepts checking and notify-only states', async () => {
    const value = installerStatus({ availability: 'unknown', checking: true, release: null, action: null, canInstall: false,
      checkout: { kind: 'source', installRoot: null, updateSupported: false, reason: 'Download the installer to update.' } });
    vi.stubGlobal('fetch', vi.fn(async () => reply(value)));
    await expect(getUpdateStatus()).resolves.toEqual(value);
  });
  it('accepts a release without an installer on an unsupported architecture', async () => {
    const value = installerStatus({ action: null, canInstall: false,
      checkout: { kind: 'unsupported', installRoot: null, updateSupported: false, reason: 'No installer is available for this architecture.' } });
    value.release!.installer = null;
    vi.stubGlobal('fetch', vi.fn(async () => reply(value)));
    await expect(getUpdateStatus()).resolves.toEqual(value);
  });
  it('accepts a refusal before a download has an active version', async () => {
    const value = installerStatus({ installState: 'failed', activeVersion: null, error: 'The update request was cancelled.' });
    vi.stubGlobal('fetch', vi.fn(async () => reply(value)));
    await expect(getUpdateStatus()).resolves.toEqual(value);
  });
  it('accepts pre-releases and explicit recovery outcomes', async () => {
    const value = installerStatus(); value.channel = 'beta';
    value.release!.version = '2.1.0-rc.1'; value.release!.tag = 'v2.1.0-rc.1';
    value.release!.url = 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.1.0-rc.1';
    value.action!.version = value.release!.version; value.action!.tag = value.release!.tag;
    value.lastOutcome = { from: '2.0.0', to: '2.0.1', result: 'rollback_incomplete', when: '2026-10-02', log: 'install.log', backupPath: 'saved.previous' };
    vi.stubGlobal('fetch', vi.fn(async () => reply(value)));
    await expect(getUpdateStatus()).resolves.toEqual(value);
  });
  it.each([
    ['legacy schema', (v: Record<string, unknown>) => { v.schemaVersion = 1; }],
    ['wrong channel', (v: Record<string, unknown>) => { v.channel = 'nightly'; }],
    ['missing checking', (v: Record<string, unknown>) => { delete v.checking; }],
    ['missing available release', (v: Record<string, unknown>) => { v.release = null; v.action = null; v.canInstall = false; }],
    ['fractional bytes', (v: Record<string, unknown>) => { v.totalBytes = 1.5; }],
    ['negative bytes', (v: Record<string, unknown>) => { v.downloadedBytes = -1; }],
    ['overflow progress', (v: Record<string, unknown>) => { v.downloadedBytes = 2; v.totalBytes = 1; }],
    ['unknown install state', (v: Record<string, unknown>) => { v.installState = 'complete'; }],
    ['idle active version', (v: Record<string, unknown>) => { v.activeVersion = '2.0.1'; }],
    ['unsigned action', (v: Record<string, unknown>) => { (v.release as UpdateStatus['release'])!.assetsReady = false; }],
    ['mismatched action', (v: Record<string, unknown>) => { (v.action as UpdateStatus['action'])!.tag = 'v2.0.2'; }],
    ['wrong download size', (v: Record<string, unknown>) => { (v.action as UpdateStatus['action'])!.size += 1; }],
    ['unsafe release link', (v: Record<string, unknown>) => { (v.release as UpdateStatus['release'])!.url = 'javascript:alert(1)'; }],
    ['missing digest', (v: Record<string, unknown>) => { (v.release as UpdateStatus['release'])!.installer!.sha256 = ''; }],
    ['unsupported installation', (v: Record<string, unknown>) => { (v.checkout as UpdateStatus['checkout']).updateSupported = false; }],
    ['invented outcome', (v: Record<string, unknown>) => { v.lastOutcome = { result: 'installed' }; }],
  ])('refuses %s', async (_name, change) => {
    const value = structuredClone(installerStatus()) as unknown as Record<string, unknown>; change(value);
    vi.stubGlobal('fetch', vi.fn(async () => reply(value)));
    await expect(getUpdateStatus()).rejects.toThrow('invalid');
  });
  it('starts only an explicit confirmed installation request', async () => {
    const fetch = vi.fn(async () => reply(accepted, 202)); vi.stubGlobal('fetch', fetch);
    await expect(installApplicationUpdate()).resolves.toEqual(accepted);
    expect(fetch).toHaveBeenCalledWith('/api/updates/install', { method: 'POST', headers: { 'X-WG-Update': 'install' } });
  });
  it.each([{ ...accepted, accepted: false }, { ...accepted, activeVersion: '2.0.2' }, { ...accepted, downloadedBytes: -1 }, { accepted: true, tag: 'v2.0.1' }])('rejects incomplete accepted response %#', async (value) => {
    vi.stubGlobal('fetch', vi.fn(async () => reply(value, 202))); await expect(installApplicationUpdate()).rejects.toThrow('invalid');
  });
  it('reports the server refusal and the HTTP fallback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(reply({ error: { message: 'An update restart is pending.' } }, 409))
      .mockResolvedValueOnce(new Response('offline', { status: 503 })));
    await expect(installApplicationUpdate()).rejects.toThrow('restart is pending');
    await expect(getUpdateStatus()).rejects.toThrow('(503)');
  });
  it('saves the server channel and reports refusal', async () => {
    const fetch = vi.fn().mockResolvedValueOnce(reply({ channel: 'beta' })).mockResolvedValueOnce(reply({ channel: 'beta' }))
      .mockResolvedValueOnce(reply({ detail: 'A download is active.' }, 409)); vi.stubGlobal('fetch', fetch);
    await expect(getUpdateChannel()).resolves.toBe('beta'); await expect(setUpdateChannel('beta')).resolves.toBe('beta');
    expect(fetch).toHaveBeenNthCalledWith(2, '/api/updates/channel', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: '{"channel":"beta"}' });
    await expect(setUpdateChannel('stable')).rejects.toThrow('download is active');
  });
});
