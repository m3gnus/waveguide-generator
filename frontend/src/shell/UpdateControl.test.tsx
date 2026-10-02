import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateStatus } from '../api/updates';
import { UpdateButton, UpdateDialog, updatePresentation } from './UpdateControl';

function status(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return { schemaVersion: 2, runningVersion: __WG2_VERSION__, channel: 'stable', availability: 'available',
    freshness: 'fresh', cached: false, checking: false, checkedAt: null, nextCheckAt: null, lastError: null,
    release: { version: '2.0.1', tag: 'v2.0.1', url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1',
      publishedAt: null, notes: 'Installation fixes.', assetsReady: true,
      installer: { name: 'installer.dmg', url: 'https://github.com/example/installer.dmg', size: 244_000_000, sha256: 'a'.repeat(64) } },
    action: { kind: 'full_installer', version: '2.0.1', tag: 'v2.0.1', name: 'installer.dmg', size: 244_000_000 },
    checkout: { kind: 'macos', installRoot: '/Applications/Waveguide Generator.app', updateSupported: true, reason: null },
    canInstall: true, installState: 'idle', activeVersion: null, downloadedBytes: 0, totalBytes: 0, error: null,
    lastOutcome: null, ...overrides };
}
const accepted = { accepted: true, version: '2.0.1', activeVersion: '2.0.1', installState: 'downloading', downloadedBytes: 0, totalBytes: 244_000_000, error: null };
const reply = (value: unknown, code = 200) => new Response(JSON.stringify(value), { status: code });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((settle) => { resolve = settle; }); return { resolve, promise }; }

describe('full-installer update control', () => {
  let host: HTMLDivElement; let root: Root; let client: QueryClient;
  const refresh = vi.fn(async () => status());
  const close = vi.fn();
  const fetch = vi.fn();
  beforeEach(() => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    refresh.mockClear(); close.mockClear(); fetch.mockReset();
    fetch.mockResolvedValue(reply(accepted, 202)); vi.stubGlobal('fetch', fetch);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); client.clear(); vi.unstubAllGlobals(); vi.useRealTimers(); });
  function render(value = status(), options: { open?: boolean; activeJobs?: number } = {}) {
    const snapshot = { data: value, error: null, isPending: false };
    act(() => root.render(<QueryClientProvider client={client}>
      <UpdateButton snapshot={snapshot} open={options.open ?? true} onOpen={() => undefined}/>
      <UpdateDialog open={options.open ?? true} snapshot={snapshot} onRefresh={refresh} onClose={close} activeJobs={options.activeJobs}/>
    </QueryClientProvider>));
  }
  function button(label: string) { return [...host.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === label)!; }
  async function click(label: string) { await act(async () => button(label).click()); }

  it('shows download size and notes before any download starts', async () => {
    render();
    await act(async () => new Promise<void>((resolve) => requestAnimationFrame(() => resolve())));
    expect(host.textContent).toContain('v2.0.1 available (244.0 MB)'); expect(host.textContent).toContain('Installation fixes.');
    expect(button('Install and restart')).toBeDefined(); expect(fetch).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain('runtimeId'); expect(host.textContent).not.toContain('app layer');
    expect(document.activeElement).toBe(button('Install and restart'));
  });
  it('starts the signed installer request only on click and reports progress', async () => {
    render(); await click('Install and restart');
    expect(fetch).toHaveBeenCalledWith('/api/updates/install', { method: 'POST', headers: { 'X-WG-Update': 'install' } });
    expect(host.textContent).toContain('0.0 MB of 244.0 MB'); expect(button('Downloading…').disabled).toBe(true);
    expect(button('Stable').disabled).toBe(true); expect(button('Beta').disabled).toBe(true);
  });
  it('requires a second confirmation before stopping active solves', async () => {
    render(status(), { activeJobs: 2 }); await click('Install and restart');
    expect(host.textContent).toContain('2 active solves will be stopped'); expect(fetch).not.toHaveBeenCalled();
    await click('Stop solves, install and restart'); expect(fetch).toHaveBeenCalledTimes(1);
  });
  it('does not install after cancelling the restart confirmation', async () => {
    render(status(), { activeJobs: 1 }); await click('Install and restart'); await click('Close');
    expect(close).toHaveBeenCalledTimes(1); expect(fetch).not.toHaveBeenCalled();
  });
  it('honestly reports download or handoff failure without claiming an install', async () => {
    fetch.mockResolvedValue(reply({ detail: 'The download signature is invalid.' }, 409));
    render(); await click('Install and restart');
    expect(host.textContent).toContain('signature is invalid'); expect(button('Install and restart').disabled).toBe(false);
    expect(host.textContent).not.toContain('Updated to');
  });
  it('ignores an installation reply after the dialog closes', async () => {
    const pending = deferred<Response>(); fetch.mockReturnValue(pending.promise); render();
    await act(async () => button('Install and restart').click()); await click('Close'); render(status(), { open: false });
    await act(async () => pending.resolve(reply(accepted, 202))); render();
    expect(button('Install and restart')).toBeDefined(); expect(host.querySelector('progress')).toBeNull();
  });
  it('keeps the saved channel when the server refuses a change', async () => {
    fetch.mockResolvedValue(reply({ detail: 'A download is active.' }, 409)); render(); await click('Beta');
    expect(button('Stable').getAttribute('aria-pressed')).toBe('true'); expect(host.textContent).toContain('download is active');
  });
  it('saves Beta on the server and waits to offer a newer stable when switching back', async () => {
    fetch.mockResolvedValue(reply({ channel: 'beta' })); render(); await click('Beta');
    expect(button('Beta').getAttribute('aria-pressed')).toBe('true'); expect(host.textContent).toContain('Returning to Stable waits');
  });
  it('polls through verification and ready without another download request', async () => {
    vi.useFakeTimers(); render(); await click('Install and restart');
    const verifying = status({ installState: 'verifying', activeVersion: '2.0.1', downloadedBytes: 244_000_000, totalBytes: 244_000_000 });
    fetch.mockResolvedValue(reply(verifying)); await act(async () => vi.advanceTimersByTimeAsync(400));
    expect(host.textContent).toContain('Verifying the download');
    fetch.mockResolvedValue(reply({ ...verifying, installState: 'ready' })); await act(async () => vi.advanceTimersByTimeAsync(400));
    expect(button('Restarting…').disabled).toBe(true);
    expect(fetch.mock.calls.filter(([url]) => url === '/api/updates/install')).toHaveLength(1);
  });
  it('aborts progress polling when closed', async () => {
    vi.useFakeTimers(); render(); await click('Install and restart');
    const pending = deferred<Response>(); fetch.mockReturnValue(pending.promise);
    await act(async () => vi.advanceTimersByTimeAsync(400));
    const signal = (fetch.mock.calls.at(-1)![1] as RequestInit).signal!;
    render(status(), { open: false }); expect(signal.aborted).toBe(true);
    await act(async () => pending.resolve(reply(status())));
  });
  it.each([
    [{ from: '2.0.0', to: '2.0.1', result: 'installed', when: '', log: '' } as const, 'Updated to v2.0.1.'],
    [{ from: '2.0.0', to: '2.0.1', result: 'failed', when: '', log: 'install.log' } as const, 'Update failed. Review the installation log.'],
    [{ from: '2.0.0', to: '2.0.1', result: 'failed', previousKept: true, when: '', log: 'install.log' } as const, 'The previous version was kept.'],
    [{ from: '2.0.0', to: '2.0.1', result: 'rollback_incomplete', when: '', log: 'install.log', backupPath: 'saved.previous' } as const, 'recovery is incomplete'],
  ])('shows the recorded outcome %# without inventing recovery', (lastOutcome, text) => {
    render(status({ lastOutcome })); expect(host.textContent).toContain(text);
    if (lastOutcome.result === 'failed' && !('previousKept' in lastOutcome)) expect(host.textContent).not.toContain('previous version was kept');
  });
  it('keeps unsupported installations notify-only', () => {
    render(status({ canInstall: false, action: null, checkout: { kind: 'portable', installRoot: null, updateSupported: false, reason: 'Download and install the full release.' } }));
    expect(button('Install and restart')).toBeUndefined(); expect(host.textContent).toContain('Download and install');
  });
  it('shows the release on an unsupported architecture without inventing a download size', () => {
    const value = status({ canInstall: false, action: null, checkout: { kind: 'unsupported', installRoot: null,
      updateSupported: false, reason: 'No installer is available for this architecture.' } });
    value.release!.installer = null;
    render(value);
    expect(host.textContent).toContain('v2.0.1 available');
    expect(host.textContent).not.toContain('0.0 MB');
    expect(button('Install and restart')).toBeUndefined();
    expect(host.querySelector('a')?.getAttribute('href')).toBe(value.release!.url);
  });
  it('closes on Escape and exposes a modal accessible name', async () => {
    render(); expect(host.querySelector('[role="dialog"]')?.getAttribute('aria-labelledby')).toBe('update-dialog-title');
    await act(async () => document.activeElement?.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    expect(close).toHaveBeenCalledTimes(1);
  });
  it('asks for reload when the frontend and running app differ', () => {
    render(status({ runningVersion: '2.0.0' })); expect(button('Reload WG')).toBeDefined(); expect(button('Install and restart')).toBeUndefined();
  });
  it('reports stale results and failed checks explicitly', () => {
    const stale = updatePresentation({ data: status({ freshness: 'stale', lastError: 'Network unavailable.' }), error: null, isPending: false });
    expect(stale.state).toBe('available'); expect(stale.detail).toBe('Network unavailable.');
    const failed = updatePresentation({ data: status({ availability: 'unknown', action: null, canInstall: false, release: null, lastError: 'No check result.' }), error: null, isPending: false });
    expect(failed.state).toBe('failed'); expect(failed.detail).toBe('No check result.');
    expect(updatePresentation({ data: status({ availability: 'unknown', checking: true }), error: null, isPending: false }).state).toBe('checking');
  });
});
