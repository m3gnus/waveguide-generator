import { beforeEach, describe, expect, it } from 'vitest';
import {
  MAX_FREQUENCY_POINTS,
  defaultPolarUi,
  normalizePersistedSolveOptions,
  normalizePolarUi,
  parseFrequencyList,
  polarConfigFromUi,
  resetSolveOptionsStore,
  useSolveOptionsStore,
} from './solveOptions';

describe('solve and directivity options', () => {
  beforeEach(() => { localStorage.clear(); resetSolveOptionsStore(); });

  it('defaults to AUTO, v1 solve policies, and the new polar_config field names', () => {
    const options = useSolveOptionsStore.getState().options();
    expect(options).toEqual({
      engine: 'auto',
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
