import { act, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { UpdateStatus } from '../api/updates';
import { UpdateButton, UpdateDialog, updatePresentation } from './UpdateControl';

function status(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    schemaVersion: 1,
    runningVersion: '2.0.0',
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

describe('UpdateControl', () => {
  let host: HTMLDivElement;
  let root: Root;
  const writeText = vi.fn(async () => undefined);

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } });
    writeText.mockClear();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
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
});
