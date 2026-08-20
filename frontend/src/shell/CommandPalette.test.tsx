import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parameterRevealRequest } from '../design/ParamPanel';
import { PARAMETER_REGISTRY } from '../design/parameterRegistry';
import { designForFamily } from '../stores/design';
import { workspaceModeStore } from '../stores/workspaceMode';
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
  afterEach(() => {
    act(() => root.unmount());
    parameterRevealRequest.claim('geometry');
    parameterRevealRequest.claim('simulation');
    workspaceModeStore.setMode('parametric');
    vi.restoreAllMocks();
    host.remove();
  });

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
    const section = host.querySelector<HTMLElement>('section[role="group"]');
    expect(section?.getAttribute('aria-labelledby')).toBe(`command-group-${group.toLocaleLowerCase()}`);
    expect(section?.querySelector('h3')?.textContent).toBe(group);
    expect(section?.textContent).toContain(label);
    expect(host.querySelectorAll('[role="option"]')).toHaveLength(1);
  });

  it('keeps the visual option state aligned with the combobox active descendant', () => {
    act(() => host.querySelector<HTMLButtonElement>('.command-affordance')!.click());
    const input = host.querySelector<HTMLInputElement>('[role="combobox"]')!;
    const options = [...host.querySelectorAll<HTMLButtonElement>('[role="option"]')];

    expect(input.getAttribute('aria-activedescendant')).toBe(options[0].id);
    expect(options.map((option) => option.getAttribute('aria-selected'))).toEqual(['true', 'false', 'false']);

    act(() => input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true })));

    expect(input.getAttribute('aria-activedescendant')).toBe(options[1].id);
    expect(options.map((option) => option.getAttribute('aria-selected'))).toEqual(['false', 'true', 'false']);
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

  it('uses CAD descriptors in CAD mode and excludes registry sections the rail hides', () => {
    const design = designForFamily('OSSE');
    const cad = buildParameterPaletteEntries(design.formula, { mode: 'cad', design, cadReturnReady: true });

    expect(cad.find((entry) => entry.id === 'cad-control-cad.frequency.start')?.matches?.('frequencyStartHz')).toBe(true);
    expect(cad.find((entry) => entry.id === 'cad-control-cad.driver.sd_cm2')?.matches?.('Thiele-Small Sd')).toBe(true);
    expect(cad.find((entry) => entry.id === 'cad-control-cad.crossover')?.matches?.('LR4 crossover')).toBe(true);
    expect(cad.some((entry) => entry.id === 'parameter-simulation.f1')).toBe(false);
    expect(cad.some((entry) => entry.id === 'parameter-source.velocity')).toBe(false);
    // Geometry formula sections remain visible in the CAD rail, so the shared
    // section predicate keeps their entries searchable too.
    expect(cad.some((entry) => entry.id === 'parameter-osse.L')).toBe(true);
  });

  it('keeps a CAD-visible formula parameter in its owning CAD workspace mode', () => {
    const design = designForFamily('OSSE');
    const throat = buildParameterPaletteEntries(design.formula, { mode: 'cad', design, cadReturnReady: true })
      .find((entry) => entry.id === 'parameter-osse.a0')!;

    workspaceModeStore.setMode('cad');
    act(() => throat.run());

    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(parameterRevealRequest.claim('geometry')).toMatchObject({
      id: 'osse.a0', target: 'parameter',
    });
  });

  it('adds the semantic Solve domain control to the registry-backed parametric palette', () => {
    const design = designForFamily('OSSE');
    const parametric = buildParameterPaletteEntries(undefined, { mode: 'parametric', design, cadReturnReady: true });
    expect(parametric.slice(0, PARAMETER_REGISTRY.length).map((entry) => entry.id))
      .toEqual(PARAMETER_REGISTRY.map((field) => `parameter-${field.id}`));
    expect(parametric.find((entry) => entry.id === 'parametric-control-solve-domain')).toMatchObject({
      label: 'Solve domain', detail: 'Solve & export mesh',
    });
    expect(parametric.some((entry) => entry.id.startsWith('cad-control-'))).toBe(false);
  });

  it('restores the owning workspace mode before routing a stale palette entry', () => {
    const design = designForFamily('OSSE');
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
    const cadControl = buildParameterPaletteEntries(design.formula, { mode: 'cad', design, cadReturnReady: true })
      .find((entry) => entry.id === 'cad-control-cad.frequency.start')!;

    workspaceModeStore.setMode('parametric');
    act(() => cadControl.run());
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(activate).toHaveBeenLastCalledWith('simulation');
    expect(parameterRevealRequest.claim('simulation')).toMatchObject({
      id: 'cad.frequency.start', target: 'control', query: 'Sweep start',
    });

    const parametricField = buildParameterPaletteEntries(design.formula, { mode: 'parametric', design })
      .find((entry) => entry.id === 'parameter-simulation.f1')!;
    workspaceModeStore.setMode('cad');
    act(() => parametricField.run());
    expect(workspaceModeStore.getSnapshot().mode).toBe('parametric');
    expect(parameterRevealRequest.claim('simulation')).toMatchObject({
      id: 'simulation.f1', target: 'parameter',
    });
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
