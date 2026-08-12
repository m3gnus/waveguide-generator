import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobsPreferencesSurface, ResultsPreferencesSurface } from './PreferencesSurface';
import { preferencesStore } from './preferences';

describe('preferences surfaces', () => {
  let host: HTMLDivElement;
  let root: Root;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear(); preferencesStore.resetForTests();
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
  });
  afterEach(() => { act(() => root.unmount()); vi.unstubAllGlobals(); host.remove(); });

  it('renders all smoothing, reference, export, automation, and format controls', async () => {
    await act(async () => { root.render(<ResultsPreferencesSurface/>); await Promise.resolve(); });
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Smoothing"]')?.options).toHaveLength(11);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Map reference"]')?.options).toHaveLength(4);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Results layout count"]')?.options).toHaveLength(5);
    expect(host.querySelector('[aria-label="Export counter"]')).toBeNull();
    expect(host.querySelectorAll('[aria-label^="Manual export:"]')).toHaveLength(11);
    expect(host.querySelectorAll('[aria-label^="Automatic export:"]')).toHaveLength(11);
    expect(host.textContent).toContain('Preferred manual export formats');
    expect(host.textContent).toContain('Automatic export formats');
    expect(host.textContent).toContain('Auto-export completed jobs');
    expect(host.textContent).toContain('Auto-download solve mesh');
  });

  it('edits manual and automatic formats independently and warns about an empty enabled auto list', async () => {
    await act(async () => { root.render(<ResultsPreferencesSurface/>); await Promise.resolve(); });
    const autoToggle = [...host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]
      .find((input) => input.parentElement?.textContent?.includes('Auto-export completed jobs'))!;
    act(() => autoToggle.click());
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('will not write any files');

    act(() => host.querySelector<HTMLInputElement>('[aria-label="Automatic export: Frequency Data CSV"]')!.click());
    expect(preferencesStore.getSnapshot().autoExportFormats).toEqual(['csv']);
    expect(preferencesStore.getSnapshot().exportFormats).toEqual(['csv', 'png']);
    expect(host.querySelector('[role="alert"]')).toBeNull();
  });

  it('renders job naming, version, date-prefix, sort, and rating-filter controls', () => {
    act(() => root.render(<JobsPreferencesSurface/>));
    expect(host.querySelector('[aria-label="Job design name"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Next job version"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Prefix job name with date"]')).not.toBeNull();
    expect(host.textContent).toContain('horn_v01');
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Default task sort"]')?.options).toHaveLength(4);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Minimum rating filter"]')?.options).toHaveLength(6);
  });

  // The gear popovers portal to <body>, so they are queried from the document
  // rather than from the render host. See AnchoredPanel for why.
  it('renders both preference groups as closeable gear popovers', async () => {
    const close = vi.fn();
    await act(async () => { root.render(<ResultsPreferencesSurface popover onClose={close}/>); await Promise.resolve(); });
    expect(document.body.querySelector('[aria-label="Results and export preferences"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Results and export preferences"]')).toBeNull();
    act(() => document.body.querySelector<HTMLButtonElement>('[aria-label="Close results preferences"]')!.click());
    expect(close).toHaveBeenCalledOnce();
    act(() => root.render(<JobsPreferencesSurface popover onClose={close}/>));
    expect(document.body.querySelector('[aria-label="Job preferences"]')).not.toBeNull();
    act(() => document.body.querySelector<HTMLButtonElement>('[aria-label="Close job preferences"]')!.click());
    expect(close).toHaveBeenCalledTimes(2);
  });

  it('closes a gear popover on Escape and on a press outside it', async () => {
    const close = vi.fn();
    await act(async () => { root.render(<ResultsPreferencesSurface popover onClose={close}/>); await Promise.resolve(); });
    act(() => { document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true })); });
    expect(close).toHaveBeenCalledOnce();
    act(() => { document.body.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); });
    expect(close).toHaveBeenCalledTimes(2);
  });

  it('keeps a press inside the popover from closing it', async () => {
    const close = vi.fn();
    await act(async () => { root.render(<ResultsPreferencesSurface popover onClose={close}/>); await Promise.resolve(); });
    const panel = document.body.querySelector('[aria-label="Results and export preferences"]')!;
    act(() => { panel.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true })); });
    expect(close).not.toHaveBeenCalled();
  });
});
