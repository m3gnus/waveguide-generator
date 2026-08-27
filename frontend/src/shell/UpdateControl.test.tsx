import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateStatus } from '../api/updates';
import { UpdateButton, UpdateDialog, updatePresentation } from './UpdateControl';

function status(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    schemaVersion: 1,
    // The control reads a mismatch between this and the build's own version as
    // "the tab is stale, reload" and stops presenting the release at all, so a
    // literal here quietly rewrites what every case below is testing the next
    // time the product version moves.
    runningVersion: __WG2_VERSION__,
    availability: 'available',
    freshness: 'fresh',
    cached: false,
    release: {
      version: '2.0.1',
      tag: 'v2.0.1',
      url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1',
      publishedAt: '2026-08-11T12:00:00Z',
      assetsReady: true,
    },
    checkedAt: '2026-08-11T12:00:00Z',
    nextCheckAt: '2026-08-12T00:00:00Z',
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
    action: {
      kind: 'copy_command',
      shell: 'Terminal',
      command: "bash '/Applications/WG checkout/installers/macos/install-wg.command' --tag v2.0.1",
    },
    canInstall: true,
    lastError: null,
    installState: 'idle',
    activeVersion: null,
    downloadedBytes: 0,
    totalBytes: 0,
    error: null,
    ...overrides,
  };
}

function bundleStatus(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return status({
    release: {
      version: '2.0.1',
      tag: 'v2.0.1',
      url: 'https://github.com/m3gnus/waveguide-generator/releases/tag/v2.0.1',
      publishedAt: '2026-08-11T12:00:00Z',
      assetsReady: true,
      runtimeId: '222222222222',
      bundleAssets: [{
        name: 'update-app-2.0.1.zip',
        url: 'https://github.com/example/app.zip',
        sha256Url: 'https://github.com/example/app.zip.sha256',
        bytes: 5_500_000,
        sha256Bytes: 96,
        layer: 'app',
      }],
    },
    checkout: {
      ...status().checkout,
      kind: 'bundle',
      branch: null,
      head: null,
      atDeclaredTag: false,
      updateSupported: true,
      installedVersion: __WG2_VERSION__,
      runtimeId: '111111111111',
      reason: null,
    },
    action: {
      kind: 'bundle_download',
      assets: [{
        name: 'update-app-2.0.1.zip',
        url: 'https://github.com/example/app.zip',
        sha256Url: 'https://github.com/example/app.zip.sha256',
        bytes: 5_500_000,
        layer: 'app',
      }],
      downloadBytes: 5_500_000,
    },
    totalBytes: 5_500_000,
    activeVersion: overrides.installState && overrides.installState !== 'idle' ? '2.0.1' : null,
    ...overrides,
  });
}

function Harness({ value, refresh = async () => value }: { value: UpdateStatus; refresh?: () => Promise<UpdateStatus> }) {
  const [open, setOpen] = useState(false);
  const [client] = useState(() => new QueryClient());
  const snapshot = { data: value, error: null, isPending: false };
  return <QueryClientProvider client={client}>
    <UpdateButton snapshot={snapshot} open={open} onOpen={() => setOpen(true)}/>
    <UpdateDialog open={open} snapshot={snapshot} onRefresh={refresh} onClose={() => setOpen(false)}/>
  </QueryClientProvider>;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

describe('UpdateControl', () => {
  let host: HTMLDivElement;
  let root: Root;
  const writeText = vi.fn(async () => undefined);

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    writeText.mockClear();
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ accepted: true, tag: 'v2.0.1' }),
      { status: 202 },
    )));
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('renders an explicit responsive update alert and copies the exact command', async () => {
    const value = status();
    act(() => root.render(<Harness value={value}/>));
    const opener = host.querySelector<HTMLButtonElement>('.update-indicator')!;
    expect(opener.classList.contains('available')).toBe(true);
    expect(opener.textContent).toContain('update available');
    expect(opener.querySelector('.update-compact')?.textContent).toBe('Update');

    await act(async () => opener.click());
    const dialog = host.querySelector<HTMLElement>('[role="dialog"]')!;
    expect(dialog.textContent).toContain('Waveguide Generator 2.0.1 is available');
    expect(dialog.querySelector('pre')?.textContent).toContain('/Applications/WG checkout');
    const copy = [...dialog.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Copy update command')!;
    await act(async () => copy.click());
    expect(writeText).toHaveBeenCalledWith(
      value.action?.kind === 'copy_command' ? value.action.command : undefined,
    );
    expect(dialog.textContent).toContain('Update command copied');
  });

  it('blocks the easy action for a modified checkout while retaining release availability', async () => {
    const value = status({
      action: null,
      canInstall: false,
      checkout: {
        ...status().checkout,
        kind: 'modified',
        trackedChanges: true,
        updateSupported: false,
        reason: 'Commit or stash tracked changes first.',
      },
    });
    act(() => root.render(<Harness value={value}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    expect(host.textContent).toContain('WG will not suggest an update command');
    expect(host.querySelector('.update-command')).toBeNull();
  });

  it('offers a bundled download without a command fallback or copy button', async () => {
    const value = bundleStatus();
    act(() => root.render(<Harness value={value}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    expect(host.textContent).toContain('Download size 5.5 MB');
    expect(host.textContent).toContain('stays open while it downloads and verifies');
    expect(host.textContent).toContain('then closes and restarts to install it');
    expect(host.textContent).toContain('Install update');
    expect(host.textContent).not.toContain('fallback');
    expect(host.textContent).not.toContain('Copy update command');
    expect(host.querySelector('pre')).toBeNull();
  });

  it.each([
    ['downloading', 2_000_000, 'Downloading 2.0 of 5.5 MB'],
    ['verifying', 5_500_000, 'Verifying downloaded update'],
    ['ready', 5_500_000, 'Update ready — WG will close and restart'],
    ['failed', 3_000_000, 'Update failed: disk full'],
  ] as const)('renders bundle install state %s', async (installState, downloadedBytes, expected) => {
    const value = bundleStatus({
      installState,
      downloadedBytes,
      error: installState === 'failed' ? 'disk full' : null,
    });
    act(() => root.render(<Harness value={value}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());

    expect(host.textContent).toContain(expected);
    expect(host.textContent).not.toContain('Copy update command');
    const installButtons = [...host.querySelectorAll<HTMLButtonElement>('.bundle-update button')];
    expect(installButtons).toHaveLength(1);
    if (installState === 'downloading' || installState === 'verifying' || installState === 'ready') {
      expect(installButtons[0].disabled).toBe(true);
    }
  });

  it('hands a ready release to the in-app installer', async () => {
    act(() => root.render(<Harness value={status()}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    const install = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Install update')!;

    await act(async () => install.click());

    expect(fetch).toHaveBeenCalledWith('/api/updates/install', {
      method: 'POST',
      headers: { 'X-WG-Update': 'install' },
    });
    expect(host.textContent).toContain('WG will close and restart');
  });

  it('starts a bundle download and switches to progress', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({
      accepted: true,
      version: '2.0.1',
      installState: 'downloading',
      activeVersion: '2.0.1',
      downloadedBytes: 1_000_000,
      totalBytes: 5_500_000,
      error: null,
    }), { status: 202 })));
    act(() => root.render(<Harness value={bundleStatus()}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    const install = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Install update')!;

    await act(async () => install.click());

    expect(host.textContent).toContain('Downloading 1.0 of 5.5 MB');
  });

  it('moves a bundle install from download through verification to ready and then stops polling', async () => {
    vi.useFakeTimers();
    const statuses = [
      bundleStatus({ installState: 'verifying', downloadedBytes: 5_500_000 }),
      bundleStatus({ installState: 'ready', downloadedBytes: 5_500_000 }),
    ];
    const fetchMock = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        return new Response(JSON.stringify({
          accepted: true,
          version: '2.0.1',
          installState: 'downloading',
          activeVersion: '2.0.1',
          downloadedBytes: 1_000_000,
          totalBytes: 5_500_000,
          error: null,
        }), { status: 202 });
      }
      return new Response(JSON.stringify(statuses.shift()), { status: 200 });
    });
    vi.stubGlobal('fetch', fetchMock);
    act(() => root.render(<Harness value={bundleStatus()}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    const install = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Install update')!;

    await act(async () => install.click());
    expect(host.textContent).toContain('Downloading 1.0 of 5.5 MB');
    await act(async () => vi.advanceTimersByTimeAsync(400));
    expect(host.textContent).toContain('Verifying downloaded update');
    await act(async () => vi.advanceTimersByTimeAsync(400));
    expect(host.textContent).toContain('Update ready — WG will close and restart');

    const callsAtReady = fetchMock.mock.calls.length;
    await act(async () => vi.advanceTimersByTimeAsync(2_000));
    expect(fetchMock).toHaveBeenCalledTimes(callsAtReady);
  });

  it.each([
    ['downloading', 'failed', 'Update failed: disk full'],
    ['verifying', 'ready', 'Update ready — WG will close and restart'],
  ] as const)('keeps polling through two unchanged %s samples until %s', async (activeState, terminalState, expected) => {
    vi.useFakeTimers();
    const downloadedBytes = activeState === 'downloading' ? 2_000_000 : 5_500_000;
    const statuses = [
      bundleStatus({ installState: activeState, downloadedBytes }),
      bundleStatus({ installState: activeState, downloadedBytes }),
      bundleStatus({
        installState: terminalState,
        downloadedBytes,
        error: terminalState === 'failed' ? 'disk full' : null,
      }),
    ];
    const fetchMock = vi.fn(async () => new Response(JSON.stringify(statuses.shift()), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    act(() => root.render(<Harness value={bundleStatus({ installState: activeState, downloadedBytes })}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());

    await act(async () => vi.advanceTimersByTimeAsync(400));
    await act(async () => vi.advanceTimersByTimeAsync(400));
    await act(async () => vi.advanceTimersByTimeAsync(400));

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(host.textContent).toContain(expected);
  });

  it('recovers from a transient progress request failure on the next poll', async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response('offline', { status: 503 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(bundleStatus({
        installState: 'ready',
        downloadedBytes: 5_500_000,
      })), { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    act(() => root.render(<Harness value={bundleStatus({
      installState: 'verifying',
      downloadedBytes: 5_500_000,
    })}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());

    await act(async () => vi.advanceTimersByTimeAsync(400));
    expect(host.textContent).toContain('Could not read update progress');
    await act(async () => vi.advanceTimersByTimeAsync(400));

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(host.textContent).toContain('Update ready — WG will close and restart');
    expect(host.textContent).not.toContain('Could not read update progress');
  });

  it('aborts an in-flight progress request when the dialog closes', async () => {
    vi.useFakeTimers();
    let polledSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) => {
      polledSignal = init?.signal as AbortSignal;
      return new Promise<Response>((_resolve, reject) => {
        polledSignal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
      });
    });
    vi.stubGlobal('fetch', fetchMock);
    act(() => root.render(<Harness value={bundleStatus({
      installState: 'downloading',
      downloadedBytes: 2_000_000,
    })}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    act(() => vi.advanceTimersByTime(400));
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(polledSignal?.aborted).toBe(false);

    await act(async () => {
      host.querySelector<HTMLButtonElement>('.dialog-close')!.click();
      await Promise.resolve();
    });

    expect(polledSignal?.aborted).toBe(true);
  });

  it('prioritizes frontend/backend skew over release status', () => {
    const presentation = updatePresentation({
      data: status({ runningVersion: '2.0.1', availability: 'current' }),
      error: null,
      isPending: false,
    });
    expect(presentation.state).toBe('reload');
    expect(presentation.announcement).toContain('Reload this page');
  });

  it('supports keyboard dismissal and restores focus to the version button', async () => {
    act(() => root.render(<Harness value={status()}/>));
    const opener = host.querySelector<HTMLButtonElement>('.update-indicator')!;
    opener.focus();
    await act(async () => opener.click());
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
    act(() => document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true })));
    expect(host.querySelector('[role="dialog"]')).toBeNull();
    expect(document.activeElement).toBe(opener);
  });

  it('discards refresh feedback when the dialog closes before the request finishes', async () => {
    const pending = deferred<UpdateStatus>();
    act(() => root.render(<Harness value={status()} refresh={() => pending.promise}/>));
    const opener = host.querySelector<HTMLButtonElement>('.update-indicator')!;
    await act(async () => opener.click());
    const check = [...host.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent === 'Check again')!;
    act(() => check.click());
    const close = host.querySelector<HTMLButtonElement>('.dialog-close')!;
    act(() => close.click());

    await act(async () => {
      pending.resolve(status({ availability: 'current' }));
      await pending.promise;
      await Promise.resolve();
    });
    await act(async () => opener.click());

    expect(host.textContent).not.toContain('Update status refreshed');
    expect(host.querySelector('[aria-busy="true"]')).toBeNull();
  });
});
