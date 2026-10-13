import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateStatus } from '../api/updates';
import { UPDATE_QUERY_KEY, UpdateButton, UpdateDialog, updatePresentation, useUpdateStatus } from './UpdateControl';

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
    vi.stubGlobal('ClipboardItem', undefined);
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); client.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); vi.useRealTimers(); });
  function render(value = status(), options: { open?: boolean; activeJobs?: number } = {}) {
    const snapshot = { data: value, error: null, isPending: false };
    act(() => root.render(<QueryClientProvider client={client}>
      <UpdateButton snapshot={snapshot} open={options.open ?? true} onOpen={() => undefined}/>
      <UpdateDialog open={options.open ?? true} snapshot={snapshot} onRefresh={refresh} onClose={close} activeJobs={options.activeJobs}/>
    </QueryClientProvider>));
  }
  function button(label: string) { return [...host.querySelectorAll<HTMLButtonElement>('button')].find((b) => b.textContent === label)!; }
  async function click(label: string) { await act(async () => button(label).click()); }

  function clipboard(writeText = vi.fn().mockResolvedValue(undefined)) {
    vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: { writeText } } }));
    return writeText;
  }

  it('copies the displayed update state, recovery outcome and freshly fetched logs, then confirms', async () => {
    const writeText = clipboard();
    const lastOutcome = { from: '2.0.0', to: '2.0.1', result: 'rollback_incomplete', when: '', log: 'install.log', backupPath: 'saved.previous' } as const;
    const value = status({ lastOutcome, installState: 'failed', error: 'Download was truncated.' });
    const diagnostics = { tailBytes: 32768, logs: { 'install.log': 'Installer recovery failed.' } };
    fetch.mockResolvedValue(reply(diagnostics)); render(value);
    await click('Copy update diagnostics');
    expect(fetch.mock.calls).toEqual([['/api/updates/diagnostics', { signal: expect.any(AbortSignal) }]]);
    const copied = JSON.parse(writeText.mock.calls[0][0]);
    expect(copied).toMatchObject({ frontendVersion: __WG2_VERSION__, status: { installState: 'failed', error: value.error, lastOutcome },
      progress: null, statusError: null, ...diagnostics });
    expect(copied.status.checkout.installRoot).toBeUndefined();
    expect(host.textContent).toContain('Update diagnostics copied.');
    expect(button('Copy update diagnostics').disabled).toBe(false);
  });
  it('copies available diagnostics when the update status could not be read', async () => {
    const writeText = clipboard(); fetch.mockImplementation((url: string) => Promise.resolve(reply(url === '/api/updates/channel'
      ? { channel: 'stable' } : { tailBytes: 32768, logs: { 'install.log': null } })));
    await act(async () => root.render(<QueryClientProvider client={client}>
      <UpdateDialog open snapshot={{ data: undefined, error: new Error('Status unavailable.'), isPending: false }} onRefresh={refresh} onClose={close}/>
    </QueryClientProvider>));
    await click('Copy update diagnostics');
    expect(JSON.parse(writeText.mock.calls[0][0])).toMatchObject({ status: null, statusError: 'Status unavailable.', logs: { 'install.log': null } });
  });
  it('starts a pending ClipboardItem write within the click before the logs arrive', async () => {
    class PendingItem {
      constructor(public data: Record<string, Promise<Blob>>) {}
    }
    vi.stubGlobal('ClipboardItem', PendingItem);
    const writeText = clipboard();
    let inClick = false;
    const write = vi.fn(async (items: PendingItem[]) => {
      expect(inClick).toBe(true);
      const blob = await items[0].data['text/plain'];
      expect(blob.type).toBe('text/plain');
      const text = await new Promise<string>((resolve) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result as string); reader.readAsText(blob);
      });
      expect(JSON.parse(text).logs).toEqual({ 'install.log': 'Pending logs.' });
    });
    Object.assign(navigator.clipboard, { write });
    const pending = deferred<Response>(); fetch.mockReturnValue(pending.promise); render();
    act(() => { inClick = true; button('Copy update diagnostics').click(); inClick = false; });
    expect(write).toHaveBeenCalledTimes(1); expect(writeText).not.toHaveBeenCalled();
    expect(host.textContent).not.toContain('Update diagnostics copied.');
    await act(async () => {
      pending.resolve(reply({ tailBytes: 32768, logs: { 'install.log': 'Pending logs.' } }));
      await write.mock.results[0].value;
    });
    expect(host.textContent).toContain('Update diagnostics copied.'); expect(writeText).not.toHaveBeenCalled();
  });
  it('uses writeText when ClipboardItem is unavailable', async () => {
    const writeText = clipboard(); const write = vi.fn(); Object.assign(navigator.clipboard, { write });
    fetch.mockResolvedValue(reply({ tailBytes: 32768, logs: {} })); render(); await click('Copy update diagnostics');
    expect(write).not.toHaveBeenCalled(); expect(writeText).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('Update diagnostics copied.');
  });
  it('falls back to writeText when a ClipboardItem write is refused', async () => {
    vi.stubGlobal('ClipboardItem', class { constructor(public data: Record<string, Promise<Blob>>) {} });
    const writeText = clipboard(); const write = vi.fn().mockRejectedValue(new Error('Pending items unsupported.'));
    Object.assign(navigator.clipboard, { write });
    fetch.mockResolvedValue(reply({ tailBytes: 32768, logs: {} })); render(); await click('Copy update diagnostics');
    expect(write).toHaveBeenCalledTimes(1); expect(writeText).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain('Update diagnostics copied.');
  });
  it.each(['HTTP', 'network'])('copies the displayed status with a %s log-fetch error', async (failure) => {
    const writeText = clipboard();
    if (failure === 'HTTP') fetch.mockResolvedValue(reply({ detail: 'Logs unavailable.' }, 503));
    else fetch.mockRejectedValue(new Error('Logs unavailable.'));
    render(); await click('Copy update diagnostics');
    expect(JSON.parse(writeText.mock.calls[0][0])).toMatchObject({ status: { channel: 'stable' }, logs: { error: 'Logs unavailable.' } });
    expect(host.textContent).toContain('Update diagnostics copied.');
  });
  it.each(['clipboard', 'unavailable'])('reports a %s failure without confirming a copy', async (failure) => {
    const writeText = clipboard();
    if (failure === 'unavailable') vi.stubGlobal('navigator', Object.create(navigator, { clipboard: { value: undefined } }));
    else if (failure === 'clipboard') writeText.mockRejectedValue(new Error('Clipboard permission denied.'));
    fetch.mockResolvedValue(reply({ tailBytes: 32768, logs: { 'install.log': null } }));
    render(); await click('Copy update diagnostics');
    expect(host.textContent).toContain(failure === 'clipboard' ? 'Clipboard permission denied.' : 'Clipboard access is unavailable');
    expect(host.textContent).not.toContain('Update diagnostics copied.');
    expect(button('Copy update diagnostics').disabled).toBe(false);
    if (failure !== 'clipboard') expect(writeText).not.toHaveBeenCalled();
  });
  it('keeps previous feedback and action controls available while copying', async () => {
    const writeText = clipboard(); fetch.mockResolvedValueOnce(reply({ detail: 'A download is active.' }, 409));
    render(); await click('Beta'); expect(host.textContent).toContain('A download is active.');
    const pending = deferred<Response>(); fetch.mockReturnValue(pending.promise); await click('Copy update diagnostics');
    expect(host.textContent).toContain('A download is active.');
    expect(button('Stable').disabled).toBe(false); expect(button('Beta').disabled).toBe(false);
    expect(button('Install and restart').disabled).toBe(false); expect(button('Copying…').disabled).toBe(true);
    await act(async () => pending.resolve(reply({ tailBytes: 32768, logs: {} })));
    expect(writeText).toHaveBeenCalledTimes(1); expect(host.textContent).not.toContain('A download is active.');
    expect(host.textContent).toContain('Update diagnostics copied.');
  });
  it('does not invalidate an installation request when copying starts', async () => {
    const writeText = clipboard(); const pending = deferred<Response>();
    fetch.mockReturnValueOnce(pending.promise).mockResolvedValueOnce(reply({ tailBytes: 32768, logs: {} }));
    render(); await click('Install and restart'); await click('Copy update diagnostics');
    expect(button('Starting…').disabled).toBe(true); expect(writeText).toHaveBeenCalledTimes(1);
    await act(async () => pending.resolve(reply(accepted, 202)));
    expect(button('Downloading…').disabled).toBe(true);
  });
  it.each(['writeText', 'ClipboardItem'])('ignores delayed diagnostics after closing and reopening with %s', async (method) => {
    const writeText = clipboard(); const pending = deferred<Response>(); fetch.mockReturnValue(pending.promise);
    if (method === 'ClipboardItem') {
      vi.stubGlobal('ClipboardItem', class { constructor(public data: Record<string, Promise<Blob>>) {} });
      Object.assign(navigator.clipboard, { write: vi.fn(async (items: { data: Record<string, Promise<Blob>> }[]) => {
        await items[0].data['text/plain'];
      }) });
    }
    render(); await click('Copy update diagnostics');
    expect(button('Copying…').disabled).toBe(true);
    await click('Close'); render(status(), { open: false }); render();
    await act(async () => pending.resolve(reply({ tailBytes: 32768, logs: { 'install.log': null } })));
    expect(writeText).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('Update diagnostics copied.');
    expect(button('Copy update diagnostics').disabled).toBe(false);
  });
  it('does not start a fallback write after closing while ClipboardItem is pending', async () => {
    vi.stubGlobal('ClipboardItem', class { constructor(public data: Record<string, Promise<Blob>>) {} });
    const writeText = clipboard(); const pending = deferred<void>();
    Object.assign(navigator.clipboard, { write: vi.fn(async () => {
      await pending.promise; throw new Error('Pending write refused.');
    }) });
    fetch.mockResolvedValue(reply({ tailBytes: 32768, logs: {} })); render(); await click('Copy update diagnostics');
    await click('Close'); render(status(), { open: false }); render();
    await act(async () => pending.resolve());
    expect(writeText).not.toHaveBeenCalled(); expect(host.textContent).not.toContain('Update diagnostics copied.');
    expect(button('Copy update diagnostics').disabled).toBe(false);
  });
  it('does not show a stale confirmation when a clipboard write finishes after closing', async () => {
    const pending = deferred<void>(); clipboard(vi.fn(() => pending.promise));
    fetch.mockResolvedValue(reply({ tailBytes: 32768, logs: { 'install.log': null } }));
    render(); await click('Copy update diagnostics');
    await click('Close'); render(status(), { open: false }); render();
    await act(async () => pending.resolve());
    expect(host.textContent).not.toContain('Update diagnostics copied.');
    expect(button('Copy update diagnostics').disabled).toBe(false);
  });
  it('keeps copying diagnostics available while an installer download is running', async () => {
    vi.useFakeTimers(); const writeText = clipboard(); render(); await click('Install and restart');
    fetch.mockResolvedValue(reply({ tailBytes: 32768, logs: { 'install.log': null } }));
    expect(button('Copy update diagnostics').disabled).toBe(false); await click('Copy update diagnostics');
    expect(JSON.parse(writeText.mock.calls[0][0]).progress).toEqual(accepted);
    expect(fetch.mock.calls.filter(([url]) => url === '/api/updates/install')).toHaveLength(1);
  });
  it('guides failed downloads to Install and restart, clearing the old error when retry starts', async () => {
    render(status({ installState: 'failed', activeVersion: '2.0.1', error: 'Download was truncated.' }));
    expect(host.textContent).toContain('Choose Install and restart to download and verify the installer again.');
    await click('Install and restart');
    expect(host.textContent).not.toContain('Download was truncated.');
    expect(host.textContent).not.toContain('Choose Install and restart');
    expect(button('Downloading…').disabled).toBe(true);
    expect(fetch.mock.calls).toEqual([['/api/updates/install', { method: 'POST', headers: { 'X-WG-Update': 'install' } }]]);
  });
  it('does not suggest installation when a failed download no longer has an installable update', () => {
    render(status({ installState: 'failed', error: 'Download failed.', canInstall: false, action: null }));
    expect(host.textContent).toContain('Check again for an installable update');
    expect(button('Install and restart')).toBeUndefined();
    expect(button('Check again')).toBeDefined();
  });
  it('offers download recovery when progress polling reports failure', async () => {
    vi.useFakeTimers(); render(); await click('Install and restart');
    fetch.mockResolvedValue(reply(status({ installState: 'failed', activeVersion: '2.0.1', error: 'Download interrupted.' })));
    await act(async () => vi.advanceTimersByTimeAsync(400));
    expect(host.textContent).toContain('Download interrupted.');
    expect(host.textContent).toContain('Choose Install and restart to download and verify the installer again.');
    expect(button('Install and restart').disabled).toBe(false);
    expect(button('Stable').disabled).toBe(false);
  });

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
    fetch.mockResolvedValueOnce(reply({ channel: 'beta' })).mockResolvedValueOnce(reply(status({ channel: 'beta' }))); render(); await click('Beta');
    render(status({ channel: 'beta' }));
    expect(button('Beta').getAttribute('aria-pressed')).toBe('true'); expect(host.textContent).toContain('Returning to Stable waits');
  });
  it('follows authoritative channel changes from another window and clears the old restart confirmation', async () => {
    render(status(), { activeJobs: 1 }); await click('Install and restart');
    expect(button('Stop solves, install and restart')).toBeDefined();
    render(status({ channel: 'beta' }), { activeJobs: 1 });
    expect(button('Beta').getAttribute('aria-pressed')).toBe('true');
    expect(button('Stable').getAttribute('aria-pressed')).toBe('false');
    expect(button('Stop solves, install and restart')).toBeUndefined();
    expect(button('Install and restart')).toBeDefined(); expect(fetch).not.toHaveBeenCalled();
  });
  it('withholds a cached install offer if the channel is saved but its status refresh fails', async () => {
    fetch.mockResolvedValueOnce(reply({ channel: 'beta' })).mockRejectedValueOnce(new Error('The channel status is offline.'));
    render(); await click('Beta');
    expect(button('Beta').getAttribute('aria-pressed')).toBe('true');
    expect(button('Install and restart')).toBeUndefined();
    expect(button('Check again')).toBeDefined();
    expect(host.textContent).toContain('Check the selected channel before installing.');
    expect(host.textContent).toContain('channel status is offline');
    expect(fetch.mock.calls.filter(([url]) => url === '/api/updates/install')).toHaveLength(0);
    render(status({ channel: 'beta' }));
    expect(button('Install and restart')).toBeUndefined();
    refresh.mockResolvedValueOnce(status({ channel: 'beta' })); await click('Check again');
    expect(button('Install and restart')).toBeDefined();
  });
  it('withholds the old offer until the confirmed channel reaches the displayed status', async () => {
    fetch.mockResolvedValueOnce(reply({ channel: 'beta' })).mockResolvedValueOnce(reply(status({ channel: 'beta' })));
    render(); await click('Beta');
    expect(button('Install and restart')).toBeUndefined();
    render(status({ channel: 'beta' }));
    expect(button('Install and restart')).toBeDefined();
  });
  it('cancels an older live query so its delayed Stable response cannot replace the new Beta offer', async () => {
    const old = deferred<Response>();
    let oldSignal: AbortSignal | undefined;
    let statusCalls = 0;
    fetch.mockImplementation((url: string, options?: RequestInit) => {
      if (url === '/api/updates/channel') return Promise.resolve(reply({ channel: 'beta' }));
      if (url === '/api/updates/status' && statusCalls++ === 0) {
        oldSignal = options?.signal as AbortSignal;
        return old.promise;
      }
      return Promise.resolve(reply(status({ channel: 'beta' })));
    });
    client.setQueryData(UPDATE_QUERY_KEY, status(), { updatedAt: 0 });
    function LiveDialog() {
      const snapshot = useUpdateStatus();
      return <UpdateDialog open snapshot={snapshot} onRefresh={snapshot.refresh} onClose={close}/>;
    }
    await act(async () => root.render(<QueryClientProvider client={client}><LiveDialog/></QueryClientProvider>));
    await click('Beta');
    await act(async () => new Promise((resolve) => setTimeout(resolve, 0)));
    expect(oldSignal?.aborted).toBe(true);
    expect(button('Beta').getAttribute('aria-pressed')).toBe('true');
    await act(async () => old.resolve(reply(status())));
    expect(client.getQueryData<UpdateStatus>(UPDATE_QUERY_KEY)?.channel).toBe('beta');
    expect(button('Beta').getAttribute('aria-pressed')).toBe('true');
    expect(button('Install and restart')).toBeDefined();
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
    if (lastOutcome.result === 'rollback_incomplete') {
      expect(host.textContent).toContain('Review the installation log before changing the installation.');
      expect(host.textContent).toContain('Recovery location: saved.previous.');
      expect(host.textContent).toContain('Log: install.log.');
    }
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
