import type { DriverFieldKey } from '../stores/cadReturn';

/**
 * The small-signal numbers a datasheet leaves out, computed from the ones it
 * prints.
 *
 * These are display only: the solver derives its own coefficients from the
 * submitted spec (`server/solver/driver_lem.py`), so nothing here is ever sent
 * anywhere. They exist so the sheet can show whether the typed numbers agree
 * with each other -- a Vas that contradicts Fs and Mms is the single most
 * common transcription error in a driver database, and it is invisible until
 * something displays the consequence.
 */

/** Air density and speed of sound, matching the solver's reference values. */
const RHO_KG_PER_M3 = 1.2;
const C_M_PER_S = 343;
const TWO_PI = 2 * Math.PI;

export interface DriverDerivedValues {
  cmsMPerN?: number;
  qes?: number;
  qts?: number;
  /** Reference efficiency, dimensionless (not a percentage). */
  eta0?: number;
  sensitivityDb?: number;
  /** Relative disagreement between the stated Fs and the Fs implied by
   * Mms, Vas and Sd. Present only when all four are known. */
  fsMismatch?: number;
}

/** Above this the sheet says so: the numbers cannot all be right. */
export const DRIVER_MISMATCH_TOLERANCE = 0.05;

type Values = Partial<Record<DriverFieldKey, number>>;

function positive(value: number | undefined): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : undefined;
}

/** Cms implied by Vas and Sd: Cms = Vas / (rho c^2 Sd^2), SI throughout. */
function complianceFromVas(vasL: number, sdCm2: number): number {
  const vasM3 = vasL / 1_000;
  const sdM2 = sdCm2 / 10_000;
  return vasM3 / (RHO_KG_PER_M3 * C_M_PER_S * C_M_PER_S * sdM2 * sdM2);
}

export function driverDerivedValues(values: Values): DriverDerivedValues {
  const sd = positive(values.sd_cm2);
  const bl = positive(values.bl_t_m);
  const re = positive(values.re_ohm);
  const mmsG = positive(values.mms_g) ?? positive(values.mmd_g);
  const fs = positive(values.fs_hz);
  const vas = positive(values.vas_l);
  const qms = positive(values.qms);
  const mms = mmsG === undefined ? undefined : mmsG / 1_000;

  const derived: DriverDerivedValues = {};

  if (fs !== undefined && mms !== undefined) {
    derived.cmsMPerN = 1 / ((TWO_PI * fs) ** 2 * mms);
  } else if (vas !== undefined && sd !== undefined) {
    derived.cmsMPerN = complianceFromVas(vas, sd);
  } else if (positive(values.cms_m_per_n) !== undefined) {
    // Nothing to derive it from, but a hand-entered driver may state it.
    derived.cmsMPerN = values.cms_m_per_n;
  }

  if (fs !== undefined && mms !== undefined && re !== undefined && bl !== undefined) {
    derived.qes = (TWO_PI * fs * mms * re) / (bl * bl);
    if (qms !== undefined) derived.qts = (qms * derived.qes) / (qms + derived.qes);
    if (vas !== undefined) {
      const eta0 = (4 * Math.PI ** 2 * fs ** 3 * (vas / 1_000)) / (C_M_PER_S ** 3 * derived.qes);
      if (eta0 > 0) {
        derived.eta0 = eta0;
        derived.sensitivityDb = 112.2 + 10 * Math.log10(eta0);
      }
    }
  }

  if (fs !== undefined && mms !== undefined && vas !== undefined && sd !== undefined) {
    const impliedFs = 1 / (TWO_PI * Math.sqrt(mms * complianceFromVas(vas, sd)));
    derived.fsMismatch = Math.abs(impliedFs - fs) / fs;
  }

  return derived;
}

export function driverValuesDisagree(derived: DriverDerivedValues): boolean {
  return derived.fsMismatch !== undefined && derived.fsMismatch > DRIVER_MISMATCH_TOLERANCE;
}
