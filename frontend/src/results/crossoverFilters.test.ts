import { describe, expect, it } from 'vitest';
import { channelWeight, complexAbs, sectionResponse } from './crossoverFilters';
import type { FilterFamily } from './crossoverSpec';

/**
 * Reference values generated from `server/solver/filters.py` itself (the same
 * `lowpass`/`highpass` the solver calls, fc = 1 kHz). These are the contract:
 * the overlay this module feeds must weight the members exactly as the solve
 * did, so a drift here is a wrong curve, not a cosmetic difference.
 */
const FREQUENCIES = [250.0, 500.0, 1000.0, 2000.0, 4000.0];
const FC = 1_000;

const REFERENCE: Array<{ family: FilterFamily; order: number; kind: 'hp' | 'lp'; values: Array<[number, number]> }> = [
  { family: 'lr', order: 2, kind: 'lp', values: [[0.830449827, -0.442906574], [0.48, -0.64], [0.0, -0.5], [-0.12, -0.16], [-0.051903114, -0.027681661]] },
  { family: 'lr', order: 2, kind: 'hp', values: [[-0.051903114, 0.027681661], [-0.12, 0.16], [-0.0, 0.5], [0.48, 0.64], [0.830449827, 0.442906574]] },
  { family: 'lr', order: 4, kind: 'lp', values: [[0.74805069, -0.657763791], [0.055363322, -0.939546727], [-0.5, -0.0], [0.003460208, 0.05872167], [0.002922073, 0.00256939]] },
  { family: 'lr', order: 4, kind: 'hp', values: [[0.002922073, -0.00256939], [0.003460208, -0.05872167], [-0.5, -0.0], [0.055363322, 0.939546727], [0.74805069, 0.657763791]] },
  { family: 'lr', order: 6, kind: 'lp', values: [[0.530746674, -0.847242507], [-0.499881657, -0.848284024], [-0.0, 0.5], [0.007810651, -0.013254438], [-0.000129577, -0.000206846]] },
  { family: 'lr', order: 6, kind: 'hp', values: [[-0.000129577, 0.000206846], [0.007810651, 0.013254438], [0.0, -0.5], [-0.499881657, 0.848284024], [0.530746674, 0.847242507]] },
  { family: 'lr', order: 8, kind: 'lp', values: [[0.249814225, -0.968278026], [-0.909469769, -0.40632226], [0.5, 0.0], [-0.003552616, 0.001587196], [3.812e-06, 1.4775e-05]] },
  { family: 'lr', order: 8, kind: 'hp', values: [[3.812e-06, -1.4775e-05], [-0.003552616, -0.001587196], [0.5, -0.0], [-0.909469769, 0.40632226], [0.249814225, 0.968278026]] },
  { family: 'butterworth', order: 1, kind: 'lp', values: [[0.941176471, -0.235294118], [0.8, -0.4], [0.5, -0.5], [0.2, -0.4], [0.058823529, -0.235294118]] },
  { family: 'butterworth', order: 1, kind: 'hp', values: [[0.058823529, 0.235294118], [0.2, 0.4], [0.5, 0.5], [0.8, 0.4], [0.941176471, 0.235294118]] },
  { family: 'butterworth', order: 2, kind: 'lp', values: [[0.93385214, -0.352177696], [0.705882353, -0.665512265], [0.0, -0.707106781], [-0.176470588, -0.166378066], [-0.058365759, -0.022011106]] },
  { family: 'butterworth', order: 2, kind: 'hp', values: [[-0.058365759, 0.022011106], [-0.176470588, 0.166378066], [-0.0, 0.707106781], [0.705882353, 0.665512265], [0.93385214, 0.352177696]] },
  { family: 'butterworth', order: 3, kind: 'lp', values: [[0.874786429, -0.484256773], [0.492307692, -0.861538462], [-0.5, -0.5], [-0.107692308, 0.061538462], [-0.007566512, 0.013668538]] },
  { family: 'butterworth', order: 3, kind: 'hp', values: [[-0.007566512, -0.013668538], [-0.107692308, -0.061538462], [-0.5, 0.5], [0.492307692, 0.861538462], [0.874786429, 0.484256773]] },
  { family: 'butterworth', order: 5, kind: 'lp', values: [[0.685385988, -0.7281793], [-0.106658587, -0.993804979], [-0.5, 0.5], [0.031056406, 0.003333081], [0.000711113, -0.000669322]] },
  { family: 'butterworth', order: 5, kind: 'hp', values: [[0.000711113, 0.000669322], [0.031056406, -0.003333081], [-0.5, -0.5], [-0.106658587, 0.993804979], [0.685385988, 0.7281793]] },
  { family: 'butterworth', order: 8, kind: 'lp', values: [[0.276086157, -0.961132891], [-0.880092609, -0.474785996], [0.707106781, 0.0], [-0.003437862, 0.001854633], [4.213e-06, 1.4666e-05]] },
  { family: 'butterworth', order: 8, kind: 'hp', values: [[4.213e-06, -1.4666e-05], [-0.003437862, -0.001854633], [0.707106781, 0.0], [-0.880092609, 0.474785996], [0.276086157, 0.961132891]] },
  { family: 'bessel', order: 2, kind: 'lp', values: [[0.924291077, -0.327283199], [0.717502434, -0.577764619], [0.190983006, -0.680827064], [-0.153610038, -0.284163622], [-0.08179252, -0.050119851]] },
  { family: 'bessel', order: 2, kind: 'hp', values: [[-0.08179252, 0.050119851], [-0.153610038, 0.284163622], [0.190983006, 0.680827064], [0.717502434, 0.577764619], [0.924291077, 0.327283199]] },
  { family: 'bessel', order: 3, kind: 'lp', values: [[0.887825708, -0.416796122], [0.590246094, -0.710535337], [-0.116477093, -0.697447551], [-0.248064677, -0.039440249], [-0.030757879, 0.026388717]] },
  { family: 'bessel', order: 3, kind: 'hp', values: [[-0.030757879, -0.026388717], [-0.248064677, 0.039440249], [-0.116477093, 0.697447551], [0.590246094, 0.710535337], [0.887825708, 0.416796122]] },
  { family: 'bessel', order: 4, kind: 'lp', values: [[0.846449119, -0.494221488], [0.453209476, -0.802955106], [-0.362477823, -0.607132463], [-0.165154243, 0.135558655], [0.006861007, 0.017697551]] },
  { family: 'bessel', order: 4, kind: 'hp', values: [[0.006861007, -0.017697551], [-0.165154243, -0.135558655], [-0.362477823, 0.607132463], [0.453209476, 0.802955106], [0.846449119, 0.494221488]] },
  { family: 'linear_phase', order: 2, kind: 'lp', values: [[0.941176471, 0.0], [0.8, 0.0], [0.5, 0.0], [0.2, 0.0], [0.058823529, 0.0]] },
  { family: 'linear_phase', order: 2, kind: 'hp', values: [[0.058823529, 0.0], [0.2, 0.0], [0.5, 0.0], [0.8, 0.0], [0.941176471, 0.0]] },
  { family: 'linear_phase', order: 4, kind: 'lp', values: [[0.996108949, 0.0], [0.941176471, 0.0], [0.5, 0.0], [0.058823529, 0.0], [0.003891051, 0.0]] },
  { family: 'linear_phase', order: 4, kind: 'hp', values: [[0.003891051, 0.0], [0.058823529, 0.0], [0.5, 0.0], [0.941176471, 0.0], [0.996108949, 0.0]] },
  { family: 'linear_phase', order: 8, kind: 'lp', values: [[0.999984741, 0.0], [0.996108949, 0.0], [0.5, 0.0], [0.003891051, 0.0], [1.5259e-05, 0.0]] },
  { family: 'linear_phase', order: 8, kind: 'hp', values: [[1.5259e-05, 0.0], [0.003891051, 0.0], [0.5, 0.0], [0.996108949, 0.0], [0.999984741, 0.0]] },
];

describe('crossover filter families', () => {
  it('reproduces the server response for every family and order', () => {
    for (const { family, order, kind, values } of REFERENCE) {
      FREQUENCIES.forEach((frequency, index) => {
        const actual = sectionResponse({ family, order, fcHz: FC }, kind, frequency);
        const label = `${family}${order} ${kind} @ ${frequency} Hz`;
        expect(actual[0], `${label} real`).toBeCloseTo(values[index][0], 7);
        expect(actual[1], `${label} imaginary`).toBeCloseTo(values[index][1], 7);
      });
    }
  });

  it('is −3 dB at the corner for every family', () => {
    for (const { family, order, kind } of REFERENCE) {
      if (family === 'lr' || family === 'linear_phase') continue;
      const magnitude = complexAbs(sectionResponse({ family, order, fcHz: FC }, kind, FC));
      expect(magnitude, `${family}${order} ${kind}`).toBeCloseTo(Math.SQRT1_2, 6);
    }
  });

  it('is −6 dB at the corner for Linkwitz-Riley, which is what makes it sum flat', () => {
    for (const order of [2, 4, 6, 8]) {
      expect(complexAbs(sectionResponse({ family: 'lr', order, fcHz: FC }, 'lp', FC))).toBeCloseTo(0.5, 9);
    }
  });

  it('gives a linear-phase section zero phase', () => {
    for (const order of [2, 4, 8]) {
      expect(sectionResponse({ family: 'linear_phase', order, fcHz: FC }, 'lp', 700)[1]).toBe(0);
    }
  });

  it('multiplies a channel high-pass and low-pass into one band weight', () => {
    const hp = { family: 'lr' as FilterFamily, order: 4, fcHz: 200 };
    const lp = { family: 'butterworth' as FilterFamily, order: 3, fcHz: 2_000 };
    const band = channelWeight(hp, lp, 700);
    const expectedHp = sectionResponse(hp, 'hp', 700);
    const expectedLp = sectionResponse(lp, 'lp', 700);
    expect(band[0]).toBeCloseTo(expectedHp[0] * expectedLp[0] - expectedHp[1] * expectedLp[1], 12);
    expect(channelWeight(null, null, 700)).toEqual([1, 0]);
  });
});
