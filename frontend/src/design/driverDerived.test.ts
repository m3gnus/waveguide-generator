import { describe, expect, it } from 'vitest';
import { driverDerivedValues, driverValuesDisagree } from './driverDerived';

/**
 * A consistent 8-inch woofer: Vas, Fs, Mms and Sd all describe the same
 * suspension, so the two compliance routes must agree and the mismatch must
 * be a rounding error rather than a warning. Vas = Cms rho c^2 Sd^2 with
 * Cms = 1/((2 pi 38)^2 0.032) and hornlab-sim's rho = 1.21, c = 343.
 */
const CONSISTENT = { sd_cm2: 220, bl_t_m: 9.2, re_ohm: 5.6, mms_g: 32, fs_hz: 38, vas_l: 37.7696, qms: 4.2 };

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

  it('adds the free-air radiation mass to Mmd before using Fs, Qms and Vas', () => {
    // Independent derivation. Datasheet Fs and Qms are free-air figures, so
    // they hold for Mms = Mmd + M_air, where M_air is the low-frequency
    // radiation mass of a baffled piston on both faces (Beranek):
    // M_air = 2 * 8 rho a^3 / 3, a = sqrt(Sd / pi).
    // Sd = 220 cm^2: a = sqrt(0.022 / pi) = 0.0836828 m, a^3 = 5.860156e-4 m^3,
    // M_air = (16 / 3) * 1.21 * 5.860156e-4 = 3.781754 g.
    // So the CONSISTENT woofer stated as Mmd = 32 - 3.781754 = 28.218246 g is
    // the same driver, and must show the numbers of Mms = 32 g:
    // Cms = 1 / ((2 pi 38)^2 * 0.032)            = 5.481799e-4 m/N
    // Qes = 2 pi 38 * 0.032 * 5.6 / 9.2^2         = 0.5055054
    // Qts = 4.2 * Qes / (4.2 + Qes)               = 0.4511997
    // eta0 = 4 pi^2 38^3 * 0.0377696 / (343^3 Qes), 112.2 + 10 log10(eta0) = 88.2324 dB
    // Before the fix Mmd was used as Mms: Cms 6.2161e-4, Qes 0.44579, Qts 0.40302.
    const { mms_g: _mms, ...rest } = CONSISTENT;
    const derived = driverDerivedValues({ ...rest, mmd_g: 28.218246 });
    expect(derived.cmsMPerN! / 5.481799e-4).toBeCloseTo(1, 6);
    expect(derived.qes).toBeCloseTo(0.5055054, 6);
    expect(derived.qts).toBeCloseTo(0.4511997, 6);
    expect(derived.sensitivityDb).toBeCloseTo(88.2324, 3);
    expect(driverValuesDisagree(derived)).toBe(false);
  });

  it('derives nothing from Mmd without Sd, since the air mass is then unknown', () => {
    const derived = driverDerivedValues({ bl_t_m: 9.2, re_ohm: 5.6, mmd_g: 28.2, fs_hz: 38, qms: 4.2 });
    expect(derived.cmsMPerN).toBeUndefined();
    expect(derived.qes).toBeUndefined();
    expect(derived.qts).toBeUndefined();
    expect(derived.sensitivityDb).toBeUndefined();
  });
});
