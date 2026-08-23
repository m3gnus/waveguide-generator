import { describe, expect, it } from 'vitest';
import { driverDerivedValues, driverValuesDisagree } from './driverDerived';

/**
 * A consistent 8-inch woofer: Vas, Fs, Mms and Sd all describe the same
 * suspension, so the two compliance routes must agree and the mismatch must
 * be a rounding error rather than a warning.
 */
const CONSISTENT = { sd_cm2: 220, bl_t_m: 9.2, re_ohm: 5.6, mms_g: 32, fs_hz: 38, vas_l: 37.4574, qms: 4.2 };

describe('derived Thiele-Small values', () => {
  it('derives Cms from Fs and Mms, and the same number from Vas and Sd', () => {
    const derived = driverDerivedValues(CONSISTENT);
    const fromVas = driverDerivedValues({ sd_cm2: CONSISTENT.sd_cm2, vas_l: CONSISTENT.vas_l });
    expect(derived.cmsMPerN).toBeCloseTo(1 / ((2 * Math.PI * 38) ** 2 * 0.032), 12);
    // The two routes are independent formulas over the same driver, so they
    // agree only when the datasheet is self-consistent -- which is the whole
    // point of showing the mismatch.
    expect(fromVas.cmsMPerN! / derived.cmsMPerN!).toBeCloseTo(1, 4);
  });

  it('computes Qes, Qts and the 1 W sensitivity from the datasheet inputs', () => {
    const derived = driverDerivedValues(CONSISTENT);
    const qes = (2 * Math.PI * 38 * 0.032 * 5.6) / (9.2 * 9.2);
    expect(derived.qes).toBeCloseTo(qes, 12);
    expect(derived.qts).toBeCloseTo((4.2 * qes) / (4.2 + qes), 12);
    const eta0 = (4 * Math.PI ** 2 * 38 ** 3 * (CONSISTENT.vas_l / 1_000)) / (343 ** 3 * qes);
    expect(derived.sensitivityDb).toBeCloseTo(112.2 + 10 * Math.log10(eta0), 12);
    // Sanity: a 220 cm² woofer of this motor lands in the normal 85-95 dB band.
    expect(derived.sensitivityDb).toBeGreaterThan(85);
    expect(derived.sensitivityDb).toBeLessThan(95);
  });

  it('flags Fs, Mms and Vas that cannot all be right', () => {
    expect(driverValuesDisagree(driverDerivedValues(CONSISTENT))).toBe(false);
    // Double Vas without touching Fs or Mms: the implied Fs drops by ~30%.
    const wrong = driverDerivedValues({ ...CONSISTENT, vas_l: CONSISTENT.vas_l * 2 });
    expect(wrong.fsMismatch).toBeGreaterThan(0.05);
    expect(driverValuesDisagree(wrong)).toBe(true);
  });

  it('reports nothing it cannot compute rather than guessing', () => {
    const derived = driverDerivedValues({ sd_cm2: 220, bl_t_m: 9.2 });
    expect(derived.cmsMPerN).toBeUndefined();
    expect(derived.qes).toBeUndefined();
    expect(derived.qts).toBeUndefined();
    expect(derived.sensitivityDb).toBeUndefined();
    expect(derived.fsMismatch).toBeUndefined();
    expect(driverValuesDisagree(derived)).toBe(false);
  });
});
