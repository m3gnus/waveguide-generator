import { beforeEach, describe, expect, it } from 'vitest';
import { polarConfigFromUi, resetSolveOptionsStore, useSolveOptionsStore } from './solveOptions';

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
