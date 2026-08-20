import { describe, expect, it } from 'vitest';
import type { RadiationImpedancePresentation } from '../api/results';
import { radiationImpedanceTraces } from './radiationImpedance';

function presentation(): RadiationImpedancePresentation {
  return {
    schema_version: 1,
    quantity: 'average_aperture_pressure_per_volume_velocity',
    units: 'Pa*s/m^3',
    phase_time_convention: 'engineering_exp_plus_jwt',
    frequencies_hz: [100, 200],
    apertures: [
      { name: 'PORT_L', tag: 31, area_m2: 0.01 },
      { name: 'PORT_R', tag: 32, area_m2: 0.01 },
      { name: 'MF', tag: 33, area_m2: 0.02 },
    ],
    engineering_matrix: {
      real: [
        [[1, 2, 3], [4, 5, 6], [7, 8, 9]],
        [[11, 12, 13], [14, 15, 16], [17, 18, 19]],
      ],
      imaginary: [
        [[21, 22, 23], [24, 25, 26], [27, 28, 29]],
        [[31, 32, 33], [34, 35, 36], [37, 38, 39]],
      ],
    },
    in_phase_termination: {
      aperture_names: ['PORT_L', 'PORT_R'],
      real: [[3, 9], [23, 29]],
      imaginary: [[43, 49], [63, 69]],
    },
  };
}

describe('radiation impedance presentation curves', () => {
  it('shows the engineering in-phase port reductions before raw matrix terms', () => {
    const traces = radiationImpedanceTraces(presentation());
    expect(traces.map(({ name }) => name)).toEqual([
      'PORT_L · in-phase ports · Re',
      'PORT_L · in-phase ports · Im',
      'PORT_R · in-phase ports · Re',
      'PORT_R · in-phase ports · Im',
    ]);
    expect(traces[0].data).toEqual([[100, 3], [200, 23]]);
    expect(traces[1].data).toEqual([[100, 43], [200, 63]]);
  });

  it('falls back to engineering self impedances when no reduction is stored', () => {
    const value = presentation();
    value.in_phase_termination = { aperture_names: [], real: [[], []], imaginary: [[], []] };
    const traces = radiationImpedanceTraces(value);
    expect(traces[0]).toMatchObject({
      name: 'PORT_L · self · Re',
      data: [[100, 1], [200, 11]],
    });
    expect(traces[5]).toMatchObject({
      name: 'MF · self · Im',
      data: [[100, 29], [200, 39]],
    });
  });
});
