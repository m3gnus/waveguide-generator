import { beforeEach, describe, expect, it } from 'vitest';
import {
  MAX_FREQUENCY_POINTS,
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
      },
    });
  });

  it('persists the symmetry mode with the other solve options', () => {
    useSolveOptionsStore.getState().setSymmetry('half_yz');
    const stored = JSON.parse(localStorage.getItem('waveguide-v2-solve-options') ?? '{}') as { state?: { symmetry?: string } };
    expect(stored.state?.symmetry).toBe('half_yz');
    expect(useSolveOptionsStore.getState().options().symmetry).toBe('half_yz');
  });

  it('converts angular step to a sample count and never allows zero enabled planes', () => {
    expect(polarConfigFromUi({ ...useSolveOptionsStore.getState().polar, angleStart: -30, angleEnd: 90, angleStep: 10 }).angle_range).toEqual([-30, 90, 13]);
    useSolveOptionsStore.getState().toggleAxis('horizontal');
    useSolveOptionsStore.getState().toggleAxis('vertical');
    useSolveOptionsStore.getState().toggleAxis('diagonal');
    expect(useSolveOptionsStore.getState().polar.enabledAxes).toEqual(['diagonal']);
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
