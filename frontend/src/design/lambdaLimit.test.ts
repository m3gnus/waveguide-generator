import { describe, expect, it } from 'vitest';
import { designForFamily, type DesignDocument } from '../stores/design';
import { highestSolvedFrequencyHz, lambdaSixthHint, lambdaSixthMm, SOLVER_SOUND_SPEED_M_PER_S } from './lambdaLimit';

const range = { frequencyMode: 'range' as const, frequencyListText: '' };

function designWithSweepEnd(f2: number): DesignDocument {
  const design = designForFamily('R-OSSE');
  design.simulation.f2 = f2;
  return design;
}

describe('λ/6 mesh limit', () => {
  it('follows the solver constant rather than a pinned 20 kHz', () => {
    expect(SOLVER_SOUND_SPEED_M_PER_S).toBe(343);
    expect(lambdaSixthMm(20_000)).toBe(2.86);
    expect(lambdaSixthMm(16_000)).toBe(3.57);
    expect(lambdaSixthMm(8_000)).toBe(7.15);
    expect(lambdaSixthMm(1_000)).toBe(57.2);
  });

  it.each([
    [20_000, '20 kHz', 2.86],
    [18_000, '18 kHz', 3.18],
    [16_000, '16 kHz', 3.57],
    [12_500, '12.5 kHz', 4.57],
    [900, '900 Hz', 63.5],
  ])('states this design\'s own sweep end of %i Hz', (f2, frequencyLabel, limitMm) => {
    expect(lambdaSixthHint(designWithSweepEnd(f2), range)).toEqual({ frequencyLabel, limitMm });
  });

  it('warns above the limit it prints, not above the old 2.86 mm', () => {
    // The motivating case: a 400 Hz - 16 kHz sweep with a 3.2 mm mouth mesh was
    // flagged amber against a 20 kHz limit it was never going to be solved at.
    const hint = lambdaSixthHint(designWithSweepEnd(16_000), range)!;
    expect(3.2 > hint.limitMm).toBe(false);
    expect(3.6 > hint.limitMm).toBe(true);
  });

  it('says nothing when the sweep end is absent, zero, or not finite', () => {
    for (const f2 of [0, -1, Number.NaN, Number.POSITIVE_INFINITY]) {
      expect(lambdaSixthHint(designWithSweepEnd(f2), range)).toBeNull();
    }
    const design = designWithSweepEnd(16_000);
    delete (design.simulation as Partial<DesignDocument['simulation']>).f2;
    expect(lambdaSixthHint(design, range)).toBeNull();
  });

  it('says nothing when f2 is an expression the server could not evaluate', () => {
    const design = designWithSweepEnd(16_000);
    // The scalar is still 16 kHz here only because hydration leaves the family
    // default in place; quoting it would name a sweep end nobody chose.
    design._expressions = { 'simulation.f2': { value: null, raw: 'coverage(p)' } };
    expect(lambdaSixthHint(design, range)).toBeNull();

    design._expressions = { 'simulation.f2': { value: 18_000, raw: '9000 * 2' } };
    design.simulation.f2 = 18_000;
    expect(lambdaSixthHint(design, range)).toMatchObject({ frequencyLabel: '18 kHz' });
  });

  it('follows an explicit frequency list, which replaces the design range', () => {
    const design = designWithSweepEnd(16_000);
    const sweep = { frequencyMode: 'list' as const, frequencyListText: '500, 1000, 20000' };
    expect(highestSolvedFrequencyHz(design, sweep)).toBe(20_000);
    expect(lambdaSixthHint(design, sweep)).toEqual({ frequencyLabel: '20 kHz', limitMm: 2.86 });
  });

  it('says nothing while an explicit list is unusable, instead of quoting the ignored range', () => {
    const design = designWithSweepEnd(16_000);
    for (const frequencyListText of ['', '2000, 1000', 'nope']) {
      expect(lambdaSixthHint(design, { frequencyMode: 'list', frequencyListText })).toBeNull();
    }
  });
});
