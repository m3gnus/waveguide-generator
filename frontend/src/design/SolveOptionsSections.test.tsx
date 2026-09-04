import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { CAPABILITIES_QUERY_KEY } from '../jobs/useCapabilities';
import { defaultPolarUi, resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { DirectivityMapControls, effectiveGridView, FrequencySweepControls, SolveOptionsControls } from './SolveOptionsSections';

/**
 * The solve and directivity controls are not registry-driven, so the registry's
 * "every parameter is documented" gate cannot see them. This covers the same
 * ground for them: every labelled control here answers a hover.
 */
describe('solve and directivity control help', () => {
  let host: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    vi.useFakeTimers();
    resetSolveOptionsStore();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
    vi.useRealTimers();
  });

  const render = (node: React.ReactNode) => act(() => root.render(<QueryClientProvider client={queryClient}>{node}</QueryClientProvider>));
  const hoverText = (element: Element) => {
    act(() => { element.dispatchEvent(new PointerEvent('pointerover', { bubbles: true, pointerType: 'mouse' })); vi.advanceTimersByTime(400); });
    const text = document.querySelector('.help-tip')?.textContent ?? '';
    act(() => { element.dispatchEvent(new PointerEvent('pointerout', { bubbles: true })); });
    return text;
  };

  it('documents every solve option', () => {
    render(<SolveOptionsControls />);
    for (const id of ['solve-engine', 'solve-mode', 'mesh-validation-mode', 'design-solve-frequency-mode', 'solve-verbose']) {
      const control = host.querySelector(`#${id}`)!;
      expect(control, id).not.toBeNull();
      // The hover target is the labelled row, not the input itself.
      const row = control.closest('.select-row, .toggle-row')!;
      expect(hoverText(row).length, `${id} has no hover help`).toBeGreaterThan(40);
    }
  });

  it('keeps the portable axisymmetric path in machine-local solve options', () => {
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      engines: [
        { name: 'metal', available: true, reason: null, version: 'test', fast_paths: [] },
        { name: 'axisym', available: true, reason: null, version: 'test', fast_paths: ['axisymmetric-meridian'] },
      ],
    });
    render(<SolveOptionsControls />);
    const control = host.querySelector<HTMLSelectElement>('#solve-mode')!;
    expect([...control.options].map((option) => option.textContent)).toEqual([
      'Auto (fastest eligible)', 'Full 3D', 'Axisymmetric (meridian)',
    ]);
    act(() => {
      control.value = 'circsym';
      control.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(useSolveOptionsStore.getState().solverMode).toBe('circsym');
    expect(useSolveOptionsStore.getState().options().solver_mode).toBe('circsym');
  });

  it('keeps design and CAD-import sweep ids unique with working labels', () => {
    render(<><SolveOptionsControls/><FrequencySweepControls idPrefix="cad-import" context="imported"/></>);
    const ids = [...host.querySelectorAll<HTMLElement>('[id]')].map((element) => element.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const id of ['design-solve-frequency-mode', 'design-solve-frequency-spacing', 'cad-import-frequency-mode', 'cad-import-frequency-spacing']) {
      expect(host.querySelector(`label[for="${id}"]`)).not.toBeNull();
      expect(host.querySelector(`#${id}`)).not.toBeNull();
    }
    expect(host.textContent).not.toContain("design's sweep start");
  });

  it("lists BEAT's backends as separate engines and greys out the ones this host lacks", () => {
    // BEAT is one solver with four interchangeable execution backends. Offered
    // as a single entry it could only ever run whichever one a probe picked
    // first, so a Mac user with both a GPU and the portable CPU path had no way
    // to ask for the other. Each is its own row now, and a row this machine
    // cannot run is disabled and says why rather than disappearing.
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      engines: [
        { name: 'metal', label: 'Metal — Apple GPU', available: true, reason: null, version: 'test', fast_paths: [] },
        { name: 'beat-cuda', label: 'BEAT · CUDA — NVIDIA GPU', available: false, reason: 'Needs an NVIDIA GPU with a functional CUDA.jl.', version: null, fast_paths: [] },
        { name: 'beat-metal', label: 'BEAT · Metal — Apple GPU', available: true, reason: null, version: '0.1.0', fast_paths: [] },
        { name: 'beat-cpu', label: 'BEAT · CPU — no GPU needed', available: true, reason: null, version: '0.1.0', fast_paths: [] },
      ],
    });
    render(<SolveOptionsControls />);
    const control = host.querySelector<HTMLSelectElement>('#solve-engine')!;
    const options = [...control.options];
    expect(options.map((option) => option.value)).toEqual([
      'auto', 'metal', 'beat-cuda', 'beat-metal', 'beat-cpu',
    ]);
    // The label carries the hardware; the wire name would not.
    expect(options[3].textContent).toBe('BEAT · Metal — Apple GPU · 0.1.0');
    expect(options.filter((option) => option.disabled).map((option) => option.value))
      .toEqual(['beat-cuda']);
    expect(options[2].textContent).toContain('unavailable: Needs an NVIDIA GPU');
  });

  it('replaces forced imported backend and domain controls with ingest facts', () => {
    const ingestRecord = { symmetry: { cut_planes: ['x0', 'y0'] } } as CadReturnIngestRecord;
    render(<SolveOptionsControls mode="cad" ingestRecord={ingestRecord}/>);
    expect(host.querySelector('#solve-engine')).toBeNull();
    expect(host.querySelector('#solve-symmetry')).toBeNull();
    expect(host.textContent).toContain('Metal · full 3-D · free space');
    expect(host.textContent).toContain('x0, y0');
    expect(host.querySelector('#mesh-validation-mode')).not.toBeNull();
    expect(host.querySelector('#cad-solve-frequency-mode')).not.toBeNull();
    expect(host.querySelector('#solve-verbose')).not.toBeNull();
  });

  it('reports widened and unchanged effective display grids through the submission derivation', () => {
    const widened = effectiveGridView(
      { ...structuredClone(defaultPolarUi), angleEnd: 90, enabledAxes: ['horizontal'] },
      { axes: { vertical: { minimum_deg: -180, maximum_deg: 180, symmetry_accepted: false } } },
    );
    expect(widened).toMatchObject({ widened: true });
    expect(widened.summary).toContain('−180° … 180°');
    expect(widened.summary).toContain('H + V');
    expect(widened.detail).toContain('Widened from your settings');

    const unchanged = effectiveGridView(
      structuredClone(defaultPolarUi),
      { axes: { horizontal: { minimum_deg: 0, maximum_deg: 180, symmetry_accepted: true } } },
    );
    expect(unchanged).toMatchObject({ widened: false });
    expect(unchanged.detail).toContain('no widening is required');
  });

  it.each([
    ['equal sweep endpoints', { angleStart: 0, angleEnd: 0 }, 'greater than its start'],
    ['zero angular step', { angleStep: 0 }, 'greater than 0 degrees'],
    ['short measurement distance', { distance: 0.05 }, 'at least 0.1 m'],
    ['no display planes', { enabledAxes: [] }, 'at least one directivity plane'],
    ['more than 721 samples', { angleStart: 0, angleEnd: 180, angleStep: 0.1 }, 'at most 721 angle samples'],
  ])('keeps both parameter modes mounted for %s and shows one adjacent grid error', (_name, invalid, message) => {
    act(() => useSolveOptionsStore.getState().updatePolar(invalid));

    render(<DirectivityMapControls/>);
    expect(host.querySelector('#polar-angle-start')).not.toBeNull();
    expect(host.querySelector('.polar-grid-error')?.textContent).toContain(message);
    expect(host.querySelector('#polar-angle-start')?.getAttribute('aria-invalid')).toBe('true');

    render(<DirectivityMapControls effectiveDerivation={{ axes: { horizontal: { minimum_deg: -180, maximum_deg: 180 } } }}/>);
    expect(host.querySelector('#polar-angle-start')).not.toBeNull();
    expect(host.querySelector('.polar-grid-error')?.textContent).toContain(message);
    expect(host.querySelector('.effective-grid-readout')).toBeNull();
  });

  it('keeps an incomplete numeric draft out of the committed grid', () => {
    render(<DirectivityMapControls/>);
    const distance = host.querySelector<HTMLInputElement>('#polar-distance')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(distance, '');
      distance.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(distance.getAttribute('aria-invalid')).toBe('true');
    expect(useSolveOptionsStore.getState().polar.distance).toBe(defaultPolarUi.distance);
    act(() => { distance.focus(); distance.blur(); });
    expect(distance.value).toBe(String(defaultPolarUi.distance));
  });

  it('describes the CAD sweep in imported-return terms', () => {
    render(<FrequencySweepControls idPrefix="cad-import" context="imported"/>);
    const copy = hoverText(host.querySelector('#cad-import-frequency-mode')!.closest('.select-row')!);
    expect(copy).toContain('imported solve range');
    expect(copy).not.toContain('design');
  });

  it('documents every directivity map control', () => {
    render(<DirectivityMapControls />);
    for (const id of ['polar-angle-start', 'polar-angle-end', 'polar-angle-step', 'polar-distance', 'polar-norm-angle', 'polar-diagonal-angle']) {
      const label = host.querySelector(`label[for="${id}"]`)!;
      expect(label, id).not.toBeNull();
      expect(hoverText(label).length, `${id} has no hover help`).toBeGreaterThan(40);
    }
    expect(hoverText(host.querySelector('.axis-toggles')!)).toContain('planes through the horn axis');
    expect(hoverText(host.querySelector('#polar-spherical-sampling')!.closest('.toggle-row')!)).toContain('balloon');
    const fieldPlaneHelp = hoverText(host.querySelector('#polar-field-plane')!.closest('.toggle-row')!);
    expect(fieldPlaneHelp).toContain('full-3D solve');
    expect(fieldPlaneHelp).toContain('0.1–1 MB');
    expect(fieldPlaneHelp).toContain('CAD-link imports');
  });

  // `.section-body` is a container-query grid whose full-width exceptions select
  // direct children, so a help wrapper around these would silently reflow them.
  it('adds no wrapper element around the grid-positioned rows', () => {
    render(<DirectivityMapControls />);
    // The controls render as a fragment, so in the real panel these sit
    // directly under `.section-body`. Here that host is the test root.
    expect(host.querySelector('.axis-toggles')!.parentElement).toBe(host);
    expect(host.querySelector('.toggle-row')!.parentElement).toBe(host);
  });
});

/**
 * The rail reads the persisted directivity rig on every render, so a stored
 * payload the store did not examine became a render-time exception rather than
 * a wrong-looking field: `enabledAxes.includes` on a non-array threw, and the
 * Simulation tab went blank with no route back through the interface.
 */
describe('a corrupt stored rig still renders the rail', () => {
  let host: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    localStorage.clear();
    resetSolveOptionsStore();
    localStorage.setItem('waveguide-v2-solve-options', JSON.stringify({
      state: { polar: { enabledAxes: null, distance: 'far', angleStep: 0 }, frequencyListText: null },
      version: 0,
    }));
    await useSolveOptionsStore.persist.rehydrate();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });
  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    queryClient.clear();
    resetSolveOptionsStore();
  });

  it('falls back to the defaults instead of throwing out of render', () => {
    act(() => root.render(<QueryClientProvider client={queryClient}><DirectivityMapControls /></QueryClientProvider>));
    const checked = [...host.querySelectorAll<HTMLInputElement>('.axis-toggles input')].filter((box) => box.checked);
    expect(checked).toHaveLength(defaultPolarUi.enabledAxes.length);
    expect(host.querySelector<HTMLInputElement>('#polar-distance')!.value).toBe(String(defaultPolarUi.distance));
    expect(host.querySelector<HTMLInputElement>('#polar-angle-step')!.value).toBe(String(defaultPolarUi.angleStep));
  });
});
