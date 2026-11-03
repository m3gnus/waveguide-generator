import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addWoofer, emptyAssembly } from '../stores/assembly';
import { resetAssemblyBuild } from '../stores/assemblyBuild';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { useDriverLibraryStore } from '../stores/driverLibrary';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { WooferDrivers } from './WooferDrivers';

vi.mock('../api/drivers', () => ({
  searchDrivers: vi.fn(async () => ({ items: [], hiddenIncomplete: 0, matchesByKind: {} })),
  getDriver: vi.fn(),
  getDriverLibrary: vi.fn(),
}));

let counter = 0;
const ids = () => `00000000-0000-4000-8000-${String(counter += 1).padStart(12, '0')}`;
let host: HTMLDivElement;
let root: Root;
let woofer: string;

const drivers = () => useDesignStore.getState().design.assembly?.drivers;
const select = () => host.querySelector<HTMLSelectElement>('select[aria-label="Drive for Woofer 1"]')!;
function choose(value: string) {
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!.call(select(), value);
    select().dispatchEvent(new Event('change', { bubbles: true }));
  });
}
function enter(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`[aria-label="${label} for Woofer 1"]`)!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function pick(label: string) {
  await act(async () => {
    const search = host.querySelector<HTMLInputElement>('[role="combobox"]')!;
    search.blur(); search.focus();
  });
  const row = [...host.querySelectorAll<HTMLButtonElement>('[role="option"]')].find(option => option.textContent?.includes(label));
  expect(row).toBeDefined();
  act(() => row!.click());
}

describe('woofer drivers on a live design', () => {
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    counter = 0; resetDesignStore(); resetAssemblyBuild();
    useSolveOptionsStore.getState().setEngine('beat-cpu');
    const added = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    woofer = added.id;
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: added.assembly });
    useDriverLibraryStore.setState({ status: 'ready', info: null, saved: [{
      id: 'saved', label: 'Library woofer', based_on: 'database', base: { re_ohm: 5.6, le_mh: 0.9, bl_t_m: 9, mmd_g: 30, cms_m_per_n: 0.0008, rms_kg_per_s: 1.2, sd_cm2: 220 },
      overrides: {}, kind: 'lf', z_ohm: null, xo_min_hz: null,
    }] });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    act(() => root.render(<WooferDrivers/>));
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.clearAllMocks(); });

  it('lists each woofer by name at fixed velocity, before any build', () => {
    expect(host.textContent).toContain('Woofer 1');
    expect(select().value).toBe('velocity');
    expect([...select().options].map(option => option.textContent))
      .toEqual(['Fixed velocity (1 m/s RMS)', 'Driver (T/S)', 'Rigid (part of the baffle)']);
    expect(host.querySelector('[role="combobox"]')).toBeNull();
  });

  it('binds a library driver to the woofer through the design, one Undo step per action', async () => {
    choose('driver');
    expect(drivers()?.[woofer]).toEqual({ exterior_transducer: { version: 1 } });
    // The shared check names the woofer and what is missing, before any solve.
    expect(host.textContent).toContain('Enter resistance in the stated units.');
    await pick('Library woofer');
    expect(drivers()?.[woofer].exterior_transducer).toMatchObject({ re_ohm: 5.6, bl_n_per_a: 9, mmd_kg: 0.03, sd_m2: 0.022 });
    expect(drivers()?.[woofer].exterior_transducer).not.toHaveProperty('motion_axis');
    expect(host.querySelector('.field-error')).toBeNull();
    expect(host.textContent).toContain('Drive voltage');
    act(() => useDesignStore.getState().undo());
    expect(drivers()?.[woofer]).toEqual({ exterior_transducer: { version: 1 } });
    act(() => useDesignStore.getState().undo());
    expect(drivers()).toBeUndefined();
  });

  it('refuses an unfinished driver on BEAT, and says which engine solves drivers otherwise', () => {
    choose('driver');
    enter('Resistance', '6');
    expect(host.querySelector('.field-error')?.textContent).toBe('Enter inductance in the stated units.');
    act(() => useSolveOptionsStore.getState().setEngine('metal'));
    expect(host.textContent).toContain('set Advanced backend under Solver to Automatic, BEAT CPU or BEAT Metal');
  });

  it('keeps a rigid woofer\'s driver for later and clears it at fixed velocity', () => {
    choose('driver');
    enter('Resistance', '6');
    choose('rigid');
    expect(useDesignStore.getState().design.assembly?.woofers?.[0].drive).toBe('rigid');
    expect(drivers()?.[woofer].exterior_transducer).toMatchObject({ re_ohm: 6 });
    expect(host.querySelector('[aria-label="Resistance for Woofer 1"]')).toBeNull();
    choose('driver');
    expect(drivers()?.[woofer].exterior_transducer).toMatchObject({ re_ohm: 6 });
    choose('velocity');
    expect(useDesignStore.getState().design.assembly?.woofers?.[0].drive).toBe('prescribed');
    expect(drivers()).toBeUndefined();
  });
});
