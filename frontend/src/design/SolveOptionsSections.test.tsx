import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { CAPABILITIES_QUERY_KEY } from '../jobs/useCapabilities';
import type { ImportedSolvePlan } from '../jobs/actions';
import type { ImportedSolvePlanSnapshot } from '../jobs/useImportedSolvePlan';
import { defaultPolarUi, resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { useDesignStore } from '../stores/design';
import { accuracyExplainer, DirectivityMapControls, effectiveGridView, FrequencySweepControls, runPolarFromJob, SolveOptionsControls } from './SolveOptionsSections';

// The server's per-engine verdict on one CAD return (POST
// /api/solve/imported-plan). A test hands the selector one directly; with no
// return prepared it is empty, which is what the real hook answers.
const importedPlan = vi.hoisted(() => ({
  current: { plan: null, error: null, isPending: false } as ImportedSolvePlanSnapshot,
}));
vi.mock('../jobs/useImportedSolvePlan', () => ({
  useImportedSolvePlan: (enabled: boolean): ImportedSolvePlanSnapshot => (
    enabled ? importedPlan.current : { plan: null, error: null, isPending: false }
  ),
}));

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
    importedPlan.current = { plan: null, error: null, isPending: false };
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
    for (const id of ['solve-accuracy', 'solve-engine', 'mesh-validation-mode', 'design-solve-frequency-mode', 'solve-verbose']) {
      const control = host.querySelector(`#${id}`)!;
      expect(control, id).not.toBeNull();
      // The hover target is the labelled row, not the input itself.
      const row = control.closest('.select-row, .toggle-row')!;
      expect(hoverText(row).length, `${id} has no hover help`).toBeGreaterThan(40);
    }
  });

  it('uses one accuracy control and store in parametric and CAD Link modes', () => {
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      engines: [
        { name: 'beat-cpu', available: true, reason: null, version: 'test', fast_paths: [], formulations: ['full-3d'] },
      ],
      engineSelection: {
        default: 'auto', resolvedDefault: 'beat-cpu', full3dOrder: ['beat-cpu'],
      },
      cpuPreparationInFlight: false,
    });
    render(<SolveOptionsControls mode="parametric" />);
    const select = host.querySelector<HTMLSelectElement>('#solve-accuracy')!;
    expect(select.value).toBe('fast');
    expect(hoverText(select.closest('.select-row')!)).toBe('Fast: BEAT CPU. Good for locating resonances.');
    act(() => { select.value = 'accurate'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(useSolveOptionsStore.getState().options().accuracy).toBe('accurate');
    expect(hoverText(select.closest('.select-row')!)).toBe('Real-k Burton–Miller. Avoids artificial wavenumber damping; accuracy still depends on mesh, integration and physical assumptions. Runs via BEAT CPU — slower; no GPU backend ready.');
    expect(host.textContent).toContain('Accurate will use BEAT CPU');
    render(<SolveOptionsControls mode="cad" />);
    expect(host.querySelector<HTMLSelectElement>('#solve-accuracy')?.value).toBe('accurate');
    expect(host.querySelector('#cad-solve-engine')).not.toBeNull();
    expect(hoverText(host.querySelector('#solve-accuracy')!.closest('.select-row')!)).toContain('Prepare the CAD return');
  });

  it.each([
    {
      name: 'Apple Silicon', ready: ['metal', 'beat-metal', 'beat-cpu', 'bempp'], resolvedDefault: 'metal',
      parametricFast: 'Fast: Metal, complex-k (numerical shift 0.005). Good for locating resonances; sharp chamber resonances may look milder.',
      cadFastEngine: 'metal', accurateEngine: 'beat-metal', cadAccurateEngine: 'beat-metal',
    },
    {
      name: 'Windows with CUDA', ready: ['beat-cuda', 'beat-cpu', 'bempp'], resolvedDefault: 'beat-cuda',
      parametricFast: 'Fast: BEAT CUDA. Good for locating resonances.',
      cadFastEngine: 'beat-cpu', accurateEngine: 'beat-cuda', cadAccurateEngine: 'beat-cuda',
    },
    {
      name: 'Windows CPU-only', ready: ['bempp', 'beat-cpu'], resolvedDefault: 'bempp',
      parametricFast: 'Fast: BEMPP, complex-k (numerical shift 0.005). Good for locating resonances; sharp chamber resonances may look milder.',
      cadFastEngine: 'beat-cpu', accurateEngine: 'beat-cpu', cadAccurateEngine: 'beat-cpu',
    },
    {
      name: 'Linux with ROCm', ready: ['beat-rocm', 'beat-cpu', 'bempp'], resolvedDefault: 'beat-rocm',
      parametricFast: 'Fast: BEAT ROCm. Good for locating resonances.',
      cadFastEngine: 'beat-cpu', accurateEngine: 'beat-rocm', cadAccurateEngine: 'beat-rocm',
    },
    {
      name: 'no BEAT backend', ready: ['bempp'], resolvedDefault: 'bempp',
      parametricFast: 'Fast: BEMPP, complex-k (numerical shift 0.005). Good for locating resonances; sharp chamber resonances may look milder.',
      cadFastEngine: null, accurateEngine: null, cadAccurateEngine: null,
    },
  ])('describes the resolved Fast and Accurate engines for $name in both modes', (platform) => {
    const engineOrder = ['metal', 'beat-cuda', 'beat-rocm', 'beat-metal', 'bempp', 'beat-cpu'];
    const capabilityEngines = engineOrder.map((name) => ({
      name,
      available: platform.ready.includes(name),
      reason: platform.ready.includes(name) ? null : 'not ready',
      version: platform.ready.includes(name) ? 'test' : null,
      fast_paths: [],
      formulations: ['full-3d'],
      geometry_sources: name === 'metal' || name === 'beat-cpu' ? ['parametric', 'imported'] : ['parametric'],
    }));
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      engines: capabilityEngines,
      engineSelection: {
        default: 'auto', resolvedDefault: platform.resolvedDefault,
        full3dOrder: engineOrder,
      },
      cpuPreparationInFlight: false,
    });
    const importedPlanFor = (engine: string | null): ImportedSolvePlanSnapshot => {
      const plan: ImportedSolvePlan = {
        ingest_id: 'cad-test', requested: 'auto', engine,
        reason: engine ? 'AUTO selected a ready imported engine' : 'No ready backend supports this CAD return',
        engines: [],
      };
      return { plan, error: null, isPending: false };
    };

    resetSolveOptionsStore();
    importedPlan.current = importedPlanFor(platform.cadFastEngine);
    render(<SolveOptionsControls mode="parametric" />);
    const accuracy = host.querySelector<HTMLSelectElement>('#solve-accuracy')!;
    expect(hoverText(accuracy.closest('.select-row')!)).toBe(platform.parametricFast);
    act(() => {
      importedPlan.current = importedPlanFor(platform.cadAccurateEngine);
      accuracy.value = 'accurate';
      accuracy.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const accurateText = hoverText(accuracy.closest('.select-row')!);
    if (platform.accurateEngine === 'beat-cpu') {
      expect(accurateText).toContain('Runs via BEAT CPU — slower; no GPU backend ready.');
    } else if (platform.accurateEngine) {
      const label = platform.accurateEngine === 'beat-metal' ? 'BEAT Metal'
        : platform.accurateEngine === 'beat-cuda' ? 'BEAT CUDA' : 'BEAT ROCm';
      expect(accurateText).toContain(`Runs via ${label}.`);
    } else {
      expect(accurateText).toContain('No BEAT backend is ready here; Accurate cannot run on this machine.');
    }

    resetSolveOptionsStore();
    importedPlan.current = importedPlanFor(platform.cadFastEngine);
    render(<SolveOptionsControls mode="cad" />);
    const cadAccuracy = host.querySelector<HTMLSelectElement>('#solve-accuracy')!;
    const expectedCadFast = platform.cadFastEngine === null
      ? 'Fast: No backend can solve this CAD return.'
      : platform.cadFastEngine === 'metal'
        ? 'Fast: Metal, complex-k (numerical shift 0.005). Good for locating resonances; sharp chamber resonances may look milder.'
        : 'Fast: BEAT CPU. Good for locating resonances.';
    expect(hoverText(cadAccuracy.closest('.select-row')!)).toBe(expectedCadFast);
    act(() => {
      importedPlan.current = importedPlanFor(platform.cadAccurateEngine);
      cadAccuracy.value = 'accurate';
      cadAccuracy.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const cadAccurateText = hoverText(cadAccuracy.closest('.select-row')!);
    if (platform.cadAccurateEngine === 'beat-cpu') {
      expect(cadAccurateText).toContain('Runs via BEAT CPU — slower; no GPU backend ready.');
    } else if (platform.cadAccurateEngine) {
      const label = platform.cadAccurateEngine === 'beat-metal' ? 'BEAT Metal'
        : platform.cadAccurateEngine === 'beat-cuda' ? 'BEAT CUDA' : 'BEAT ROCm';
      expect(cadAccurateText).toContain(`Runs via ${label}.`);
    } else {
      expect(cadAccurateText).toContain('No BEAT backend can solve this CAD return');
    }
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

  // CAD Link is a geometry source, not a solve mode: imported geometry is
  // offered the same engine list as a design, and shows the same choice.
  it('offers imported geometry the same engine selector as a design', () => {
    render(<SolveOptionsControls />);
    const parametric = [...host.querySelector<HTMLSelectElement>('#solve-engine')!.options]
      .map((option) => option.value);
    render(<SolveOptionsControls mode="cad" ingestRecord={null} />);
    const control = host.querySelector<HTMLSelectElement>('#cad-solve-engine')!;
    expect([...control.options].map((option) => option.value)).toEqual(parametric);
    expect(control.value).toBe(useSolveOptionsStore.getState().engine);
  });

  it('replaces forced imported backend and domain controls with ingest facts', () => {
    const ingestRecord = { symmetry: { cut_planes: ['x0', 'y0'] } } as CadReturnIngestRecord;
    render(<SolveOptionsControls mode="cad" ingestRecord={ingestRecord}/>);
    expect(host.querySelector('#solve-engine')).toBeNull();
    expect(host.querySelector('#solve-symmetry')).toBeNull();
    // The engine is the same user choice as the parametric workspace's; the
    // formulation and domain stay facts.
    expect(host.querySelector('#cad-solve-engine')).not.toBeNull();
    expect(host.textContent).toContain('Runs on');
    expect(host.textContent).toContain('x0, y0');
    expect(host.querySelector('#mesh-validation-mode')).not.toBeNull();
    expect(host.querySelector('#cad-solve-frequency-mode')).not.toBeNull();
    expect(host.querySelector('#solve-verbose')).not.toBeNull();
  });

  // Per record, the selector shows the server's verdict, not the capability
  // snapshot: BEMPP here is available and declares imported geometry, and is
  // still refused for this return, with the server's own reason beside it.
  it('disables an engine the imported plan refuses for this return, with the server reason', () => {
    const imported = ['parametric', 'imported'];
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      engines: [
        { name: 'metal', label: 'Metal — Apple GPU', available: true, reason: null, version: 'test', fast_paths: [], geometry_sources: imported },
        { name: 'bempp', label: 'BEMPP — CPU', available: true, reason: null, version: 'test', fast_paths: [], geometry_sources: imported },
        { name: 'beat-cpu', label: 'BEAT · CPU — no GPU needed', available: true, reason: null, version: 'test', fast_paths: [], geometry_sources: imported },
      ],
    });
    const refusal = 'this return has 3 open edges off its mirror planes. BEMPP holds the pressure at zero on a free rim.';
    importedPlan.current = {
      plan: {
        ingest_id: 'wgi_test', requested: 'auto', engine: 'metal', code: null,
        reason: 'AUTO selected the first available engine', domain: 'half_yz',
        engines: [
          { name: 'metal', label: 'Metal — Apple GPU', solves: true },
          { name: 'bempp', label: 'BEMPP — CPU', solves: false, stage: 'preflight', code: 'imported_return_unsupported_by_engine', reason: refusal },
          { name: 'beat-cpu', label: 'BEAT · CPU — no GPU needed', solves: true },
        ],
      },
      error: null,
      isPending: false,
    };

    render(<SolveOptionsControls mode="cad" ingestRecord={null} />);

    const options = new Map(
      [...host.querySelector<HTMLSelectElement>('#cad-solve-engine')!.options].map((option) => [option.value, option]),
    );
    expect(options.get('bempp')!.disabled).toBe(true);
    expect(options.get('bempp')!.textContent).toContain(refusal);
    expect(options.get('metal')!.disabled).toBe(false);
    expect(options.get('beat-cpu')!.disabled).toBe(false);
    expect(host.textContent).toContain('Metal — Apple GPU · full 3-D · free space');
  });

  it('lets BEAT · Metal be chosen in CAD mode before a return is prepared', () => {
    queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
      engines: [
        { name: 'metal', label: 'Metal — Apple GPU', available: true, reason: null, version: 'test', fast_paths: [], geometry_sources: ['parametric', 'imported'] },
        // BEAT · Metal declares parametric only (AUTO must not choose it in
        // Fast), yet an explicit pick solves imported geometry.
        { name: 'beat-metal', label: 'BEAT · Metal — Apple GPU', available: true, reason: null, version: 'test', fast_paths: [], geometry_sources: ['parametric'] },
        { name: 'bempp', label: 'BEMPP — CPU', available: true, reason: null, version: 'test', fast_paths: [], geometry_sources: ['parametric'] },
      ],
    });
    render(<SolveOptionsControls mode="cad" ingestRecord={null} />);
    const options = new Map(
      [...host.querySelector<HTMLSelectElement>('#cad-solve-engine')!.options].map((option) => [option.value, option]),
    );
    expect(options.get('beat-metal')!.disabled).toBe(false);
    expect(options.get('bempp')!.disabled).toBe(true);
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
    expect(fieldPlaneHelp).toContain('surface data needed for acoustic field planes');
    expect(fieldPlaneHelp).toContain('0.1–1 MB');
    expect(fieldPlaneHelp).toContain('CAD-link imports');
  });

  describe("the Fast / Accurate explainer", () => {
    const engineOrder = ['metal', 'beat-cuda', 'beat-rocm', 'beat-metal', 'bempp', 'beat-cpu'];
    const setReady = (ready: string[], resolvedDefault: string) => {
      queryClient.setQueryData(CAPABILITIES_QUERY_KEY, {
        engines: engineOrder.map((name) => ({
          name,
          available: ready.includes(name),
          reason: ready.includes(name) ? null : 'not ready',
          version: ready.includes(name) ? 'test' : null,
          fast_paths: [],
          formulations: ['full-3d'],
          geometry_sources: ['parametric', 'imported'],
        })),
        engineSelection: {
          default: 'auto', resolvedDefault, full3dOrder: engineOrder,
        },
        cpuPreparationInFlight: false,
      });
    };
    const explainer = () => host.querySelector<HTMLDetailsElement>('details.accuracy-explainer')!;
    const paragraphs = () => [...explainer().querySelectorAll('p')].map((p) => p.textContent ?? '');
    const planFor = (engine: string | null): ImportedSolvePlanSnapshot => ({
      plan: { ingest_id: 'cad-test', requested: 'auto', engine, reason: 'test', engines: [] },
      error: null,
      isPending: false,
    });

    it.each([
      {
        engine: 'metal', ready: ['metal', 'beat-metal', 'beat-cpu', 'bempp'],
        fast: 'here on Metal:', fastKind: 'complex-k', accurate: 'Runs through BEAT Metal;',
      },
      {
        engine: 'bempp', ready: ['bempp', 'beat-cpu'],
        fast: 'here on BEMPP:', fastKind: 'complex-k', accurate: 'Runs through BEAT CPU, because no BEAT GPU backend is ready here;',
      },
      {
        engine: 'beat-cuda', ready: ['beat-cuda', 'beat-cpu', 'bempp'],
        fast: 'on this machine Fast runs on BEAT CUDA, which already uses the Burton–Miller formulation', fastKind: 'burton-miller',
        accurate: 'Runs through BEAT CUDA;',
      },
      {
        engine: 'beat-cpu', ready: ['beat-cpu'],
        fast: 'on this machine Fast runs on BEAT CPU, which already uses the Burton–Miller formulation', fastKind: 'burton-miller',
        accurate: 'Runs through BEAT CPU, because no BEAT GPU backend is ready here;',
      },
    ])('names the resolved $engine engine in parametric mode', ({ engine, ready, fast, fastKind, accurate }) => {
      setReady(ready, engine);
      render(<SolveOptionsControls mode="parametric" />);
      const [fastText, accurateText, when, measured] = paragraphs();
      expect(fastText.startsWith('Fast (default) — ')).toBe(true);
      expect(fastText).toContain(fast);
      if (fastKind === 'complex-k') {
        expect(fastText).toContain('small imaginary shift (0.005)');
        expect(fastText).toContain('fictitious frequencies');
        expect(fastText).toContain('sharp chamber or cavity resonances can look milder');
      } else {
        expect(fastText).toContain('adds no complex-k damping');
        expect(fastText).not.toContain('0.005');
      }
      expect(accurateText).toContain('Burton–Miller combined formulation');
      expect(accurateText).toContain('normal-derivative equation');
      expect(accurateText).toContain('without adding damping');
      expect(accurateText).toContain(accurate);
      expect(accurateText).toContain('several times slower');
      expect(accurateText).toContain('first solve waits while BEAT starts up');
      expect(when).toBe('When to pick Accurate: sheltered chambers or cavities driven near their resonance.');
      expect(measured).toContain('both modes placed resonances at the same frequencies');
      expect(measured).toContain('Neither mode changes mesh density');
      // The explainer describes both choices whichever one is selected.
      const select = host.querySelector<HTMLSelectElement>('#solve-accuracy')!;
      act(() => { select.value = 'accurate'; select.dispatchEvent(new Event('change', { bubbles: true })); });
      expect(useSolveOptionsStore.getState().options().accuracy).toBe('accurate');
      expect(paragraphs()).toEqual([fastText, accurateText, when, measured]);
    });

    it('describes the modes the same way in CAD Link mode, naming only the engine the plan resolved', () => {
      setReady(['metal', 'beat-metal', 'beat-cpu', 'bempp'], 'metal');
      importedPlan.current = planFor('metal');
      render(<SolveOptionsControls mode="cad" />);
      let [fastText, accurateText] = paragraphs();
      expect(fastText).toContain('complex-wavenumber BEM, here on Metal:');
      expect(accurateText).toContain('Runs through BEAT — a GPU backend when one is ready, otherwise BEAT CPU;');

      importedPlan.current = planFor('beat-cpu');
      const select = host.querySelector<HTMLSelectElement>('#solve-accuracy')!;
      act(() => { select.value = 'accurate'; select.dispatchEvent(new Event('change', { bubbles: true })); });
      expect(useSolveOptionsStore.getState().options().accuracy).toBe('accurate');
      [fastText, accurateText] = paragraphs();
      expect(fastText).toContain('complex-wavenumber BEM (Metal or BEMPP):');
      expect(fastText).toContain('Where Fast runs on a BEAT backend it uses Burton–Miller instead.');
      expect(accurateText).toContain('Runs through BEAT CPU, because no BEAT GPU backend is ready here;');
    });

    it('is a collapsed native disclosure beside the select, with no wrapper around the rows', () => {
      setReady(['metal', 'beat-metal', 'beat-cpu', 'bempp'], 'metal');
      for (const mode of ['parametric', 'cad'] as const) {
        render(<SolveOptionsControls mode={mode} />);
        const details = explainer();
        expect(host.querySelectorAll('details.accuracy-explainer')).toHaveLength(1);
        expect(details.parentElement).toBe(host);
        expect(details.previousElementSibling?.querySelector('#solve-accuracy')).not.toBeNull();
        expect(details.open).toBe(false);
        const summary = details.querySelector('summary')!;
        expect(summary.textContent).toBe('What’s the difference?');
        // A native <summary> is focusable and toggles on Enter/Space; the click
        // below is the activation those keys perform.
        act(() => { summary.click(); });
        expect(details.open).toBe(true);
        act(() => { summary.click(); });
        expect(details.open).toBe(false);
      }
    });
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

describe('accuracy explainer and the infinite baffle', () => {
  it('says Fast adds a shift, and that an infinite-baffle design is the exception', () => {
    const [fast] = accuracyExplainer('metal', 'beat-metal');
    expect(fast).toContain('imaginary shift (0.005)');
    expect(fast).toContain('An infinite-baffle design is the exception');
    expect(fast).toContain('no shift and no added damping');
  });
});

describe('infinite baffle sweep hint and the recorded arc', () => {
  it('shows the front half-space hint only for an infinite-baffle design', () => {
    const host = document.createElement('div');
    document.body.append(host);
    const root = createRoot(host);
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    const queryClient = new QueryClient();
    const draw = () => act(() => root.render(<QueryClientProvider client={queryClient}><DirectivityMapControls /></QueryClientProvider>));
    const original = useDesignStore.getState().design;
    try {
      draw();
      expect(host.textContent).not.toContain('front half-space');
      act(() => useDesignStore.setState({ design: { ...original, simulation: { ...original.simulation, sim_type: 'infinite-baffle' } } }));
      draw();
      expect(host.textContent).toContain('Infinite baffle observes the front half-space, 0–90°');
    } finally {
      act(() => useDesignStore.setState({ design: original }));
      act(() => root.unmount());
      host.remove();
    }
  });

  it('reports the arc the run observed, not the one the request carried', () => {
    const request = { angle_range: [0, 180, 37], angle_step: 5, distance: 2, norm_angle: 5, inclination: 45, enabled_axes: ['horizontal'], observation_origin: 'mouth', spherical_sampling: false, field_plane: true };
    const job = { solve_options: { polar_config: request }, polar_grid: { start: 0, end: 90, count: 19, resolved_step: 5 } };
    const polar = runPolarFromJob(job)!;
    expect([polar.angleStart, polar.angleEnd, polar.angleStep]).toEqual([0, 90, 5]);
    // No recorded grid: the request is all there is.
    expect(runPolarFromJob({ solve_options: { polar_config: request } })!.angleEnd).toBe(180);
  });
});
