import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
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
    ...overrides,
  };
}

function Harness({ value, refresh = async () => value }: { value: UpdateStatus; refresh?: () => Promise<UpdateStatus> }) {
  const [open, setOpen] = useState(false);
  const snapshot = { data: value, error: null, isPending: false };
  return <>
    <UpdateButton snapshot={snapshot} open={open} onOpen={() => setOpen(true)}/>
    <UpdateDialog open={open} snapshot={snapshot} onRefresh={refresh} onClose={() => setOpen(false)}/>
  </>;
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
    expect(writeText).toHaveBeenCalledWith(value.action?.command);
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

  it('labels a bundled install without offering the checkout installer', async () => {
    const value = status({
      action: null,
      canInstall: false,
      checkout: {
        ...status().checkout,
        kind: 'bundle',
        branch: null,
        updateSupported: false,
        reason: 'Install a newer DMG manually.',
      },
    });
    act(() => root.render(<Harness value={value}/>));
    await act(async () => host.querySelector<HTMLButtonElement>('.update-indicator')!.click());
    expect(host.textContent).toContain('Standalone app');
    expect(host.textContent).toContain('Install a newer DMG manually.');
    expect(host.querySelector('.update-command')).toBeNull();
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
