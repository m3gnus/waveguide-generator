import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { addWoofer, emptyAssembly, setWooferDrive } from '../stores/assembly';
import { resetAssemblyBuild } from '../stores/assemblyBuild';
import '../stores/assemblySimulation';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { useDriverLibraryStore } from '../stores/driverLibrary';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { ParamPanel } from './ParamPanel';

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
const section = () => host.querySelector<HTMLElement>(`[data-channel-id="${woofer}"]`)!;
const solveDriver = () => host.querySelector<HTMLInputElement>('input[aria-label="Solve driver in BEAT for Woofer 1"]')!;
function enter(label: string, value: string) {
  const input = host.querySelector<HTMLInputElement>(`[aria-label="${label} for Woofer 1"]`)!;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
async function pick(label: string) {
  await act(async () => {
    const search = host.querySelector<HTMLInputElement>('[role="combobox"][aria-label="Find driver for Woofer 1"]')!;
    search.blur(); search.focus();
  });
  const row = [...host.querySelectorAll<HTMLButtonElement>('[role="option"]')].find(option => option.textContent?.includes(label));
  expect(row).toBeDefined();
  act(() => row!.click());
}

describe('a design assembly in the shared Drivers section', () => {
  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    counter = 0; resetDesignStore(); resetAssemblyBuild(); resetSolveOptionsStore();
    workspaceModeStore.setMode('parametric');
    useSolveOptionsStore.getState().setEngine('beat-cpu');
    const added = addWoofer(emptyAssembly(ids), { anchor: 'below' }, ids);
    woofer = added.id;
    useDesignStore.getState().replaceDocument({ ...designForFamily('R-OSSE'), assembly: added.assembly });
    useDriverLibraryStore.setState({ status: 'ready', info: null, saved: [{
      id: 'saved', label: 'Library woofer', based_on: 'database', base: { re_ohm: 5.6, le_mh: 0.9, bl_t_m: 9, mmd_g: 30, cms_m_per_n: 0.0008, rms_kg_per_s: 1.2, sd_cm2: 220 },
      overrides: {}, kind: 'lf', z_ohm: null, xo_min_hz: null,
    }] });
    host = document.createElement('div'); document.body.append(host); root = createRoot(host);
    await act(async () => { root.render(<QueryClientProvider client={new QueryClient()}><ParamPanel tab="simulation"/></QueryClientProvider>); });
  });
  afterEach(() => { act(() => root.unmount()); host.remove(); vi.clearAllMocks(); });

  it('lists the horn and each woofer by name before any build, with the build\'s fixed motion', () => {
    expect(host.querySelector('[data-channel-id="horn-throat"]')?.textContent).toContain('The horn\'s source moves at 1 m/s RMS');
    expect(section().textContent).toContain('Woofer 1');
    expect(solveDriver().checked).toBe(false);
    expect(host.querySelector('select[aria-label^="Motion for"], [aria-label^="Motion axis"], [aria-label^="Max drive"]')).toBeNull();
  });

  it('binds a library driver to the woofer through the design, one Undo step per action', async () => {
    act(() => solveDriver().click());
    expect(drivers()?.[woofer]).toEqual({ exterior_transducer: { version: 1 } });
    expect(host.textContent).toContain('Woofer 1 needs its driver: Enter resistance in the stated units.');
    await pick('Library woofer');
    expect(drivers()?.[woofer].exterior_transducer).toMatchObject({ re_ohm: 5.6, bl_n_per_a: 9, mmd_kg: 0.03, sd_m2: 0.022 });
    expect(drivers()?.[woofer].exterior_transducer).not.toHaveProperty('motion_axis');
    expect(host.querySelector('.field-error')).toBeNull();
    expect(host.textContent).toContain('Drive voltage');
    expect(host.textContent).not.toContain('Max drive voltage');
    act(() => useDesignStore.getState().undo());
    expect(drivers()?.[woofer]).toEqual({ exterior_transducer: { version: 1 } });
    act(() => useDesignStore.getState().undo());
    expect(drivers()).toBeUndefined();
  });

  it('refuses an unfinished driver on BEAT, and names the backends that solve drivers otherwise', () => {
    act(() => solveDriver().click());
    enter('Resistance', '6');
    expect(host.querySelector('[role="alert"]')?.textContent).toContain('Woofer 1 needs its driver: Enter inductance in the stated units.');
    act(() => useSolveOptionsStore.getState().setEngine('metal'));
    expect(host.textContent).toContain('set Advanced backend under Solver to Automatic, BEAT CPU or BEAT Metal');
    expect(host.textContent).toContain('Woofer 1: Enter inductance in the stated units.');
  });

  it('shares one state with the Sources and baffle drive choice', () => {
    act(() => useDesignStore.getState().commitEmbedded({ assembly: setWooferDrive(useDesignStore.getState().design.assembly!, woofer, 'driver') }, 'step'));
    expect(solveDriver().checked).toBe(true);
    act(() => solveDriver().click());
    expect(drivers()).toBeUndefined();
    act(() => useDesignStore.getState().commitEmbedded({ assembly: setWooferDrive(useDesignStore.getState().design.assembly!, woofer, 'rigid') }, 'step'));
    expect(host.querySelector(`[data-channel-id="${woofer}"]`)).toBeNull();
  });
});
