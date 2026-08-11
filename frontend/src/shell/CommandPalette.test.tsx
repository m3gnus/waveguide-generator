import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CommandPalette, type PaletteEntry } from './CommandPalette';
import { buildParameterPaletteEntries } from './TopBar';
import { workspaceNavigation } from './Workspace';

const entries: PaletteEntry[] = [
  { id: 'parameter-mouth', kind: 'Parameters', label: 'Mouth radius', keywords: 'R radius', run: vi.fn() },
  { id: 'job-alpha', kind: 'Jobs', label: 'Alpha horn', keywords: 'complete', run: vi.fn() },
  { id: 'settings', kind: 'Commands', label: 'Settings', run: vi.fn() },
];

describe('CommandPalette', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    act(() => root.render(<CommandPalette entries={entries}/>));
  });
  afterEach(() => { act(() => root.unmount()); vi.restoreAllMocks(); host.remove(); });

  it('opens on Ctrl-K and prevents the browser default', () => {
    const shortcut = new KeyboardEvent('keydown', { key: 'k', ctrlKey: true, bubbles: true, cancelable: true });
    act(() => window.dispatchEvent(shortcut));
    expect(shortcut.defaultPrevented).toBe(true);
    expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  });

  it('preserves the original focus target when the shortcut is pressed again', async () => {
    const before = document.createElement('button');
    document.body.append(before);
    before.focus();
    const shortcut = () => new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true, cancelable: true });

    act(() => window.dispatchEvent(shortcut()));
    await act(async () => { await new Promise(requestAnimationFrame); });
    expect(document.activeElement).toBe(host.querySelector('[aria-label="Search commands"]'));

    act(() => window.dispatchEvent(shortcut()));
    act(() => host.querySelector<HTMLElement>('[role="dialog"]')!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })));
    await act(async () => { await new Promise(requestAnimationFrame); });
    expect(document.activeElement).toBe(before);
    before.remove();
  });

  it.each([
    ['radius', 'Parameters', 'Mouth radius'],
    ['alpha', 'Jobs', 'Alpha horn'],
    ['settings', 'Commands', 'Settings'],
  ])('filters %s across the %s group', (query, group, label) => {
    act(() => host.querySelector<HTMLButtonElement>('.command-affordance')!.click());
    const input = host.querySelector<HTMLInputElement>('[aria-label="Search commands"]')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, query);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(host.querySelector(`section[aria-label="${group}"]`)?.textContent).toContain(label);
    expect(host.querySelectorAll('[role="option"]')).toHaveLength(1);
  });

  it('routes a selected parameter to its owning dock panel', async () => {
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const parameter = buildParameterPaletteEntries().find((entry) => entry.id === 'parameter-simulation.f1')!;
    await act(async () => {
      root.render(<CommandPalette entries={[parameter]}/>);
      await Promise.resolve();
    });
    act(() => host.querySelector<HTMLButtonElement>('.command-affordance')!.click());
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[role="option"]')!.click();
      await Promise.resolve();
    });
    expect(activate).toHaveBeenCalledWith('simulation');
  });

  it('hints the modifier the host platform actually has', () => {
    const platform = (value: string) => Object.defineProperty(navigator, 'platform', { value, configurable: true });
    const original = navigator.platform;
    try {
      platform('Win32');
      act(() => root.render(<CommandPalette entries={entries}/>));
      act(() => host.querySelector<HTMLButtonElement>('.command-affordance')!.click());
      expect([...host.querySelectorAll('kbd')].map((key) => key.textContent)).toContain('Ctrl+K');
      expect(host.textContent).not.toContain('⌘');

      platform('MacIntel');
      act(() => root.render(<CommandPalette entries={entries}/>));
      expect([...host.querySelectorAll('kbd')].map((key) => key.textContent)).toContain('⌘K');
    } finally {
      platform(original);
    }
  });

  it('names a parameter symbol once when it doubles as the legacy config key', () => {
    const entries = buildParameterPaletteEntries();
    const doubled = entries.filter((entry) => {
      const parts = entry.detail?.split(' · ') ?? [];
      return parts.length > 1 && new Set(parts).size !== parts.length;
    });
    expect(doubled).toEqual([]);
    // R is the canonical case: symbol and legacy key are both "R".
    expect(entries.find((entry) => entry.label === 'Mouth radius')?.detail).toBe('R');
  });
});
