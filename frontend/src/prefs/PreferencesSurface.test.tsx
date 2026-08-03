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

  it('renders all smoothing, reference, theme, naming, automation, and format controls', async () => {
    await act(async () => { root.render(<ResultsPreferencesSurface/>); await Promise.resolve(); });
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Smoothing"]')?.options).toHaveLength(11);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Map reference"]')?.options).toHaveLength(4);
    expect(host.querySelector('[aria-label="Output name"]')).not.toBeNull();
    expect(host.querySelector('[aria-label="Export counter"]')).not.toBeNull();
    expect(host.querySelectorAll('fieldset input[type="checkbox"]')).toHaveLength(11);
    expect(host.textContent).toContain('auto-export on complete');
    expect(host.textContent).toContain('auto-download mesh');
  });

  it('renders persisted job sort and rating-filter controls', () => {
    act(() => root.render(<JobsPreferencesSurface/>));
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Default task sort"]')?.options).toHaveLength(4);
    expect(host.querySelector<HTMLSelectElement>('[aria-label="Minimum rating filter"]')?.options).toHaveLength(6);
  });
});
