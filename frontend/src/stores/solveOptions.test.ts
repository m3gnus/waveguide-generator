import { beforeEach, describe, expect, it, vi } from 'vitest';
import { durableSettings } from './durableSettings';
import { workspaceModeStore } from './workspaceMode';
import {
  MAX_FREQUENCY_POINTS,
  createPersistedSolveOptionsStore,
  createImportedSolveOptionsStore,
  defaultPolarUi,
  normalizePersistedSolveOptions,
  normalizePolarUi,
  parseFrequencyList,
  polarConfigFromUi,
  applyWireOptionsToImportedSolveOptions,
  resetSolveOptionsStore,
  seedImportedSolveOptionsFromParametric,
  useImportedSolveOptionsStore,
  restoreSolveSettingsFromBlocks,
  useSolveOptionsStore,
} from './solveOptions';

describe('engine selection preference', () => {
  it('records choices without preparing engines from inactive setup stores', () => {
    const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
    try {
      resetSolveOptionsStore();
      useSolveOptionsStore.getState().setEngine('beat-metal');
      useImportedSolveOptionsStore.getState().setEngine('beat-cpu');
      expect(fetcher).not.toHaveBeenCalled();
      expect(JSON.parse(durableSettings.get('enginePreference')!)).toEqual({ engine: 'beat-cpu' });
    } finally { vi.unstubAllGlobals(); }
  });

  it('restores the last choice from either picker after a restart without changing saved setup owners', () => {
    localStorage.clear();
    resetSolveOptionsStore();
    useSolveOptionsStore.getState().setEngine('beat-metal');
    useImportedSolveOptionsStore.getState().setEngine('beat-cpu');
    expect(useSolveOptionsStore.getState().engine).toBe('beat-metal');
    expect(createPersistedSolveOptionsStore().getState().engine).toBe('beat-cpu');
    expect(createImportedSolveOptionsStore().getState().engine).toBe('beat-cpu');
  });

  it('keeps engine selection usable when background prewarm fails', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
    try {
      resetSolveOptionsStore();
      useSolveOptionsStore.getState().setEngine('beat-metal');
      await Promise.resolve();
      expect(useSolveOptionsStore.getState().engine).toBe('beat-metal');
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('solve and directivity options', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('keeps saved v1 polar origin unchanged', () => {
    useSolveOptionsStore.getState().updatePolar({ observationOrigin: 'mouth' });
    restoreSolveSettingsFromBlocks({
      'ABEC.Polars:SPL_H': { items: { MapAngleRange: '0,90,19' } },
    }, { text_import_version: 'ath-2026-08c-v1', simulation: { sim_type: 'freestanding' } });
    expect(useSolveOptionsStore.getState().polar.observationOrigin).toBe('mouth');
  });

  it.each([
    ['freestanding', 'throat'],
    ['infinite-baffle', 'mouth'],
    [undefined, 'throat'],
  ])('restores imported polar origin for simulation %s as %s', (simType, origin) => {
    useSolveOptionsStore.getState().updatePolar({ observationOrigin: origin === 'mouth' ? 'throat' : 'mouth' });
    restoreSolveSettingsFromBlocks({
      'ABEC.Polars:SPL_H': { items: { MapAngleRange: '0,90,19', Distance: '2', NormAngle: '0' } },
    }, { text_import_version: 'ath-2026-09a-v2', simulation: { sim_type: simType } });
    expect(useSolveOptionsStore.getState().polar).toMatchObject({ observationOrigin: origin, distance: 2, normAngle: 0 });
  });

  it.each([
    ['freestanding', 'mouth'],
    ['infinite-baffle', 'throat'],
  ] as const)('WG.Solve origin wins for imported %s polars', (simType, origin) => {
    restoreSolveSettingsFromBlocks({
      'ABEC.Polars:SPL_H': { items: { MapAngleRange: '0,90,19' } },
      'WG.Solve': { items: { ObservationOrigin: origin } },
    }, { text_import_version: 'ath-2026-09a-v2', simulation: { sim_type: simType } });
    expect(useSolveOptionsStore.getState().polar.observationOrigin).toBe(origin);
  });

  it('keeps the current origin for native designs and imports without polars', () => {
    useSolveOptionsStore.getState().updatePolar({ observationOrigin: 'throat' });
    restoreSolveSettingsFromBlocks({ 'ABEC.Polars:SPL_H': { items: {} } }, {});
    expect(useSolveOptionsStore.getState().polar.observationOrigin).toBe('throat');
    useSolveOptionsStore.getState().updatePolar({ observationOrigin: 'mouth' });
    restoreSolveSettingsFromBlocks({}, { text_import_version: 'ath-2026-09a-v2' });
    expect(useSolveOptionsStore.getState().polar.observationOrigin).toBe('mouth');
  });

  it('defaults to Full 3D, v1 solve policies, and the new polar_config field names', () => {
    const options = useSolveOptionsStore.getState().options();
    expect(options).toEqual({
      engine: 'auto',
      solver_mode: 'full_3d',
      symmetry: 'auto',
      mesh_validation_mode: 'warn',
      verbose: false,
      frequency_spacing: 'log',
      polar_config: {
        angle_range: [0, 180, 37],
        angle_step: 5,
        distance: 2,
        norm_angle: 5,
        inclination: 45,
        enabled_axes: ['horizontal', 'vertical', 'diagonal'],
        observation_origin: 'mouth',
        spherical_sampling: false,
        field_plane: true,
      },
    });
  });

  it('submits the explicit field-plane retention choice', () => {
    useSolveOptionsStore.getState().updatePolar({ fieldPlane: false });
    expect(useSolveOptionsStore.getState().options().polar_config.field_plane).toBe(false);
  });

  it('merges the enabled field-plane default into settings saved before the option existed', async () => {
    const { fieldPlane: _fieldPlane, ...legacyPolar } = useSolveOptionsStore.getState().polar;
    localStorage.setItem('waveguide-v2-solve-options', JSON.stringify({
      state: { polar: legacyPolar },
      version: 0,
    }));

    await useSolveOptionsStore.persist.rehydrate();

    expect(useSolveOptionsStore.getState().polar.fieldPlane).toBe(true);
    expect(useSolveOptionsStore.getState().options().polar_config.field_plane).toBe(true);
  });

  it('persists the symmetry mode with the other solve options', () => {
    useSolveOptionsStore.getState().setSymmetry('half_yz');
    const stored = JSON.parse(localStorage.getItem('waveguide-v2-solve-options') ?? '{}') as { state?: { symmetry?: string } };
    expect(stored.state?.symmetry).toBe('half_yz');
    expect(useSolveOptionsStore.getState().options().symmetry).toBe('half_yz');
  });

  it('persists accuracy, clears an advanced override, and lets an explicit engine take precedence', async () => {
    const store = useSolveOptionsStore.getState();
    store.setEngine('metal');
    store.setAccuracy('accurate');
    expect(useSolveOptionsStore.getState()).toMatchObject({ engine: 'auto', accuracy: 'accurate' });
    expect(useSolveOptionsStore.getState().options()).toMatchObject({ engine: 'auto', accuracy: 'accurate' });
    const saved = localStorage.getItem('waveguide-v2-solve-options');
    expect(saved).toContain('"accuracy":"accurate"');
    resetSolveOptionsStore();
    localStorage.setItem('waveguide-v2-solve-options', saved!);
    await useSolveOptionsStore.persist.rehydrate();
    expect(useSolveOptionsStore.getState().accuracy).toBe('accurate');
    useSolveOptionsStore.getState().setEngine('bempp');
    expect(useSolveOptionsStore.getState()).toMatchObject({ engine: 'bempp', accuracy: 'fast' });
    useSolveOptionsStore.getState().setEngine('beat-metal');
    expect(useSolveOptionsStore.getState()).toMatchObject({ engine: 'beat-metal', accuracy: 'fast' });
  });

  it('migrates legacy BEAT CPU and Axisymmetric settings to Fast Full 3D', async () => {
    useSolveOptionsStore.getState().setAccuracy('accurate');
    localStorage.removeItem('wg2.engine.preference.v1'); // legacy install has no shared preference
    localStorage.setItem('waveguide-v2-solve-options', JSON.stringify({
      state: { engine: 'beat-cpu', solverMode: 'circsym' }, version: 0,
    }));
    await useSolveOptionsStore.persist.rehydrate();
    expect(useSolveOptionsStore.getState()).toMatchObject({ engine: 'beat-cpu', accuracy: 'fast', solverMode: 'full_3d' });
    expect(useSolveOptionsStore.getState().options()).toMatchObject({ engine: 'beat-cpu', solver_mode: 'full_3d' });
    expect(useSolveOptionsStore.getState().options().accuracy).toBeUndefined();
  });

  it.each(['axisym', 'circsym'])('migrates persisted removed engine %s and keeps other settings', (engine) => {
    const polar = { ...defaultPolarUi, distance: 3.5 };
    const options = normalizePersistedSolveOptions({
      engine, solverMode: 'circsym', symmetry: 'quarter',
      frequencyMode: 'list', frequencyListText: '500, 1000', polar,
    });
    expect(options).toMatchObject({
      engine: 'auto', solverMode: 'full_3d', symmetry: 'quarter',
      frequencyMode: 'list', frequencyListText: '500, 1000', polar,
    });
  });

  it('migrates the legacy automatic formulation to Full 3D', async () => {
    localStorage.setItem('waveguide-v2-solve-options', JSON.stringify({
      state: { solverMode: 'auto' },
      version: 0,
    }));

    await useSolveOptionsStore.persist.rehydrate();

    expect(useSolveOptionsStore.getState().solverMode).toBe('full_3d');
    expect(useSolveOptionsStore.getState().options().solver_mode).toBe('full_3d');
  });

  it('converts angular step to a sample count and never allows zero enabled planes', () => {
    expect(polarConfigFromUi({ ...useSolveOptionsStore.getState().polar, angleStart: -30, angleEnd: 90, angleStep: 10 }).angle_range).toEqual([-30, 90, 13]);
    expect(polarConfigFromUi({ ...useSolveOptionsStore.getState().polar, angleStart: 0, angleEnd: .3, angleStep: .1 }).angle_range).toEqual([0, .3, 4]);
    expect(polarConfigFromUi({ ...useSolveOptionsStore.getState().polar, angleStart: 0, angleEnd: 180, angleStep: 7 }).angle_range).toEqual([0, 180, 26]);
    useSolveOptionsStore.getState().toggleAxis('horizontal');
    useSolveOptionsStore.getState().toggleAxis('vertical');
    useSolveOptionsStore.getState().toggleAxis('diagonal');
    expect(useSolveOptionsStore.getState().polar.enabledAxes).toEqual(['diagonal']);
  });

  it('rejects polar settings that the server contract cannot run', () => {
    const polar = useSolveOptionsStore.getState().polar;
    expect(() => polarConfigFromUi({ ...polar, angleEnd: polar.angleStart })).toThrow(/end must be greater/);
    expect(() => polarConfigFromUi({ ...polar, angleStep: 0 })).toThrow(/step must be greater/);
    expect(() => polarConfigFromUi({ ...polar, distance: 0.05 })).toThrow(/at least 0.1/);
    expect(() => polarConfigFromUi({ ...polar, angleStart: 0, angleEnd: 180, angleStep: 0.1 })).toThrow(/at most 721/);
  });
});

/**
 * The durable copy of these settings is a JSON file in the application data
 * directory. It can arrive hand-edited, half-written, or produced by a version
 * that spelled a field differently, and it used to be spread into the store
 * without a glance.
 */
describe('a corrupt stored payload cannot reach the store', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  const rehydrateWith = async (state: unknown) => {
    localStorage.setItem('waveguide-v2-solve-options', JSON.stringify({ state, version: 0 }));
    await useSolveOptionsStore.persist.rehydrate();
  };

  /**
   * The live bug this exists for. A non-array `enabledAxes` was spread in as
   * it stood, and the first thing the Simulation rail does with it is
   * `enabledAxes.map` -- so the whole panel threw out of render, with no way
   * back except clearing the setting by hand.
   */
  it('survives a rig whose fields hold the wrong types entirely', async () => {
    await rehydrateWith({
      polar: {
        enabledAxes: null,
        angleStep: 'five',
        distance: {},
        normAngle: [],
        diagonalAngle: 'NaN',
        observationOrigin: 'ear',
        sphericalSampling: 'yes',
        fieldPlane: 1,
      },
      frequencyListText: 42,
      engine: { name: 'metal' },
      symmetry: 'sideways',
      meshValidationMode: 'nonsense',
      frequencySpacing: 'octave',
      frequencyMode: 'sweep',
      verbose: 'maybe',
    });

    const state = useSolveOptionsStore.getState();
    expect(state.polar.enabledAxes).toEqual(defaultPolarUi.enabledAxes);
    expect(state.polar).toEqual(defaultPolarUi);
    expect(state).toMatchObject({
      engine: 'auto',
      symmetry: 'auto',
      meshValidationMode: 'warn',
      verbose: false,
      frequencySpacing: 'log',
      frequencyMode: 'range',
      frequencyListText: '',
    });
    // The two calls the panel makes on every render, neither of which used to
    // survive the payload above.
    expect(state.polar.enabledAxes.map((axis) => axis).join('+')).toBe('horizontal+vertical+diagonal');
    expect(state.frequencyListParse().error).toContain('at least one');
    expect(() => state.options()).not.toThrow();
  });

  it('clamps a measurement distance the solve contract would refuse', async () => {
    await rehydrateWith({ polar: { ...defaultPolarUi, distance: 0.001 } });
    expect(useSolveOptionsStore.getState().polar.distance).toBe(0.1);
    expect(() => useSolveOptionsStore.getState().options()).not.toThrow();
  });

  it('repairs a degenerate sweep so it can never reach durable settings', async () => {
    // The live bug: 0..0 persisted, then threw out of `polarConfigFromUi` on
    // the render path at every subsequent launch.
    await rehydrateWith({ polar: { ...defaultPolarUi, angleStart: 0, angleEnd: 0 } });
    const { polar } = useSolveOptionsStore.getState();
    expect(polar.angleStart).toBe(defaultPolarUi.angleStart);
    expect(polar.angleEnd).toBe(defaultPolarUi.angleEnd);
    expect(() => useSolveOptionsStore.getState().options()).not.toThrow();
  });

  it('falls back the endpoint pair together when the sweep is inverted', () => {
    const normalized = normalizePolarUi({ ...defaultPolarUi, angleStart: 90, angleEnd: 30 });
    expect(normalized.angleStart).toBe(defaultPolarUi.angleStart);
    expect(normalized.angleEnd).toBe(defaultPolarUi.angleEnd);
    // A degenerate fallback pair gets the same check before it is trusted.
    const fromBadFallback = normalizePolarUi(
      { ...defaultPolarUi, angleStart: 10, angleEnd: 10 },
      { ...defaultPolarUi, angleStart: 5, angleEnd: 5 },
    );
    expect(fromBadFallback.angleEnd).toBeGreaterThan(fromBadFallback.angleStart);
  });

  it('keeps the settings it can read and only replaces the ones it cannot', () => {
    const normalized = normalizePersistedSolveOptions({
      engine: 'metal',
      symmetry: 'half_xz',
      frequencyListText: '500 1000',
      polar: { ...defaultPolarUi, distance: 3.5, enabledAxes: ['vertical', 'vertical', 'sideways'], angleStep: -1 },
    });
    expect(normalized.engine).toBe('metal');
    expect(normalized.symmetry).toBe('half_xz');
    expect(normalized.frequencyListText).toBe('500 1000');
    expect(normalized.polar.distance).toBe(3.5);
    // Deduplicated, filtered, and never emptied.
    expect(normalized.polar.enabledAxes).toEqual(['vertical']);
    // No clampable floor on the step, so an illegal one falls back.
    expect(normalized.polar.angleStep).toBe(defaultPolarUi.angleStep);
  });

  it('falls back to the value being replaced rather than to the shipped default', () => {
    const mine = { ...defaultPolarUi, distance: 4, observationOrigin: 'throat' as const };
    const normalized = normalizePolarUi({ distance: Number.NaN }, mine);
    expect(normalized.distance).toBe(4);
    expect(normalized.observationOrigin).toBe('throat');
  });

  it('normalizes on the way out too, so one bad value does not outlive the session', () => {
    useSolveOptionsStore.setState({ polar: { ...defaultPolarUi, distance: 0.001 } });
    const stored = JSON.parse(localStorage.getItem('waveguide-v2-solve-options') ?? '{}') as {
      state?: { polar?: { distance?: number } };
    };
    expect(stored.state?.polar?.distance).toBe(0.1);
  });
});

describe('explicit frequency lists', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('accepts commas, spaces, and newlines as separators', () => {
    expect(parseFrequencyList('500, 630 800\n1000').frequencies).toEqual([500, 630, 800, 1000]);
    expect(parseFrequencyList('2,5').frequencies).toEqual([2, 5]);
    expect(parseFrequencyList('2.5,5.5').frequencies).toEqual([2.5, 5.5]);
    expect(parseFrequencyList(' 1234.5 ').frequencies).toEqual([1234.5]);
  });

  it('rejects rather than repairs unusable lists', () => {
    for (const [text, fragment] of [
      ['', 'at least one'],
      ['   ', 'at least one'],
      ['500, abc', 'not a number'],
      ['0, 500', 'above 0 Hz'],
      ['-100', 'above 0 Hz'],
      ['1000, 500', 'must ascend'],
      ['500, 500', 'must ascend'],
    ] as const) {
      const parsed = parseFrequencyList(text);
      expect(parsed.frequencies, `expected "${text}" to be rejected`).toBeNull();
      expect(parsed.error).toContain(fragment);
    }
    expect(parseFrequencyList(Array.from({ length: MAX_FREQUENCY_POINTS + 1 }, (_, index) => index + 1).join(' ')).error)
      .toContain(`At most ${MAX_FREQUENCY_POINTS}`);
  });

  it('omits frequencies_hz entirely while the generated grid is selected', () => {
    useSolveOptionsStore.getState().setFrequencyListText('500 1000');
    expect('frequencies_hz' in useSolveOptionsStore.getState().options()).toBe(false);
  });

  it('sends the parsed list once list mode is selected', () => {
    useSolveOptionsStore.getState().setFrequencyMode('list');
    useSolveOptionsStore.getState().setFrequencyListText('500, 1000, 2000');
    expect(useSolveOptionsStore.getState().options().frequencies_hz).toEqual([500, 1000, 2000]);
  });

  it('throws instead of silently falling back to the generated grid', () => {
    useSolveOptionsStore.getState().setFrequencyMode('list');
    useSolveOptionsStore.getState().setFrequencyListText('2000, 1000');
    expect(() => useSolveOptionsStore.getState().options()).toThrow(/not usable/);
  });

  it('persists the sweep source and the typed list', () => {
    useSolveOptionsStore.getState().setFrequencyMode('list');
    useSolveOptionsStore.getState().setFrequencyListText('500 1000');
    const stored = JSON.parse(localStorage.getItem('waveguide-v2-solve-options') ?? '{}') as {
      state?: { frequencyMode?: string; frequencyListText?: string };
    };
    expect(stored.state?.frequencyMode).toBe('list');
    expect(stored.state?.frequencyListText).toBe('500 1000');
  });
});

describe('adaptive sweep preference', () => {
  it('defaults off, persists only a boolean and omits the off wire option', () => {
    resetSolveOptionsStore();
    expect(useSolveOptionsStore.getState().adaptiveFrequencySampling).toBe(false);
    expect(useSolveOptionsStore.getState().options()).not.toHaveProperty('adaptive_frequency_sampling');
    useSolveOptionsStore.getState().setAdaptiveFrequencySampling(true);
    expect(useSolveOptionsStore.getState().options().adaptive_frequency_sampling).toBe(true);
    expect(normalizePersistedSolveOptions({ adaptiveFrequencySampling: 'true' }).adaptiveFrequencySampling).toBe(false);
    expect(normalizePersistedSolveOptions({ adaptiveFrequencySampling: true }).adaptiveFrequencySampling).toBe(true);
    resetSolveOptionsStore();
  });
});


describe('the imported solve-options instance', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('is made by the same factory and starts from the same defaults', () => {
    const { options: _a, ...parametric } = useSolveOptionsStore.getState();
    const { options: _b, ...imported } = useImportedSolveOptionsStore.getState();
    expect(Object.keys(imported).sort()).toEqual(Object.keys(parametric).sort());
    expect(useImportedSolveOptionsStore.getState().options()).toEqual(useSolveOptionsStore.getState().options());
  });

  it('persists only its explicit engine choice and leaves the parametric setup alone', () => {
    const write = vi.spyOn(durableSettings, 'set');
    const parametric = JSON.stringify(useSolveOptionsStore.getState());
    useImportedSolveOptionsStore.getState().setEngine('bempp');
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 6 });
    useImportedSolveOptionsStore.getState().setFrequencySpacing('linear');
    expect(write).toHaveBeenCalledExactlyOnceWith('enginePreference', JSON.stringify({ engine: 'bempp' }));
    expect(JSON.stringify(useSolveOptionsStore.getState())).toBe(parametric);
    write.mockRestore();
  });

  it('is untouched by edits to the parametric store', () => {
    const imported = JSON.stringify(useImportedSolveOptionsStore.getState());
    useSolveOptionsStore.getState().setEngine('bempp');
    useSolveOptionsStore.getState().updatePolar({ distance: 6 });
    expect(JSON.stringify(useImportedSolveOptionsStore.getState())).toBe(imported);
  });

  it('is seeded from the parametric options by a one-way copy', () => {
    useSolveOptionsStore.getState().updatePolar({ distance: 6 });
    useSolveOptionsStore.getState().setFrequencySpacing('linear');
    seedImportedSolveOptionsFromParametric();
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({ frequencySpacing: 'linear', polar: { distance: 6 } });
    useImportedSolveOptionsStore.getState().updatePolar({ distance: 2.5 });
    expect(useSolveOptionsStore.getState().polar.distance).toBe(6);
    // The copy is detached: mutating one polar rig cannot reach the other.
    expect(useImportedSolveOptionsStore.getState().polar).not.toBe(useSolveOptionsStore.getState().polar);
  });

  it('takes a recorded run\'s options without writing the parametric store', () => {
    const parametric = JSON.stringify(useSolveOptionsStore.getState());
    applyWireOptionsToImportedSolveOptions({
      engine: 'beat-cpu', accuracy: 'accurate', frequency_spacing: 'linear', frequencies_hz: [100, 200],
      polar_config: { ...polarConfigFromUi({ ...defaultPolarUi, distance: 3 }) },
    });
    expect(useImportedSolveOptionsStore.getState()).toMatchObject({
      engine: 'beat-cpu', accuracy: 'accurate', frequencySpacing: 'linear', frequencyMode: 'list',
      frequencyListText: '100\n200', polar: { distance: 3 },
    });
    expect(JSON.stringify(useSolveOptionsStore.getState())).toBe(parametric);
  });
});

describe('file and CAD input keep their own imported options', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); workspaceModeStore.setImportedInput('cad'); });

  it('restores each side\'s own options when the input is switched', () => {
    const imported = useImportedSolveOptionsStore.getState;
    imported().updatePolar({ distance: 5 });
    imported().setFrequencyMode('list');
    imported().setFrequencyListText('100 200 300');
    workspaceModeStore.setImportedInput('file');
    // The file side starts from a copy of the parametric options, not CAD's edits.
    expect(imported()).toMatchObject({ frequencyMode: 'range', polar: { distance: useSolveOptionsStore.getState().polar.distance } });
    expect(imported().groundPlane.enabled).toBe(false);
    imported().setFrequencySpacing('linear');
    imported().setSweep({ count: 11 });
    workspaceModeStore.setImportedInput('cad');
    expect(imported()).toMatchObject({ frequencyMode: 'list', frequencyListText: '100 200 300', polar: { distance: 5 }, frequencySpacing: 'log' });
    workspaceModeStore.setImportedInput('file');
    expect(imported()).toMatchObject({ frequencySpacing: 'linear', sweepCount: 11, frequencyMode: 'range' });
    workspaceModeStore.setImportedInput('cad');
  });

});
