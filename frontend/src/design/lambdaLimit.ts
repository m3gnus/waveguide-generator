import type { DesignDocument } from '../stores/design';
import { parseFrequencyList, type FrequencyMode } from '../stores/solveOptions';

/**
 * Speed of sound the solvers build their wavenumber from, in m/s.
 *
 * Both pinned backends define ``SPEED_OF_SOUND = 343.0`` in their
 * ``_constants.py`` (hornlab_metal_bem, hornlab_bempp_bem) and
 * ``server/solver/acoustics.py`` reads it from whichever package ran. That value
 * only reaches the client in *result* metadata, long after the panel has to draw
 * a mesh hint, and ``/api/capabilities`` does not carry it at all. So this is a
 * mirror, in the same spirit as that module's own ``_FALLBACK_SOUND_SPEED_M_PER_S``
 * -- named rather than left as a bare 343 so the authority is one grep away.
 */
export const SOLVER_SOUND_SPEED_M_PER_S = 343;

export interface SolvedSweep {
  frequencyMode: FrequencyMode;
  frequencyListText: string;
}

export function compactFrequency(value: number): string {
  return value >= 1_000 ? `${Number((value / 1_000).toPrecision(3))} kHz` : `${value} Hz`;
}

/**
 * The highest frequency the next solve will actually run, or null when the
 * answer is unknowable rather than merely unusual.
 *
 * An explicit frequency list replaces the design's range outright, so in list
 * mode the sweep end in the rail is not what gets solved. A list that does not
 * parse has no top frequency at all -- and neither does an ``f2`` the server
 * could not evaluate, because an unevaluated Expr leaves the scalar sitting at
 * whichever family default it was hydrated with. Saying nothing beats quoting a
 * number this design will never solve at.
 */
export function highestSolvedFrequencyHz(design: DesignDocument, sweep: SolvedSweep): number | null {
  if (sweep.frequencyMode === 'list') {
    const { frequencies } = parseFrequencyList(sweep.frequencyListText);
    // The parser rejects anything not strictly ascending, so the last is the top.
    return frequencies ? frequencies[frequencies.length - 1] : null;
  }
  const expression = design._expressions?.['simulation.f2'];
  if (expression && !Number.isFinite(expression.value ?? Number.NaN)) return null;
  const f2 = design.simulation.f2;
  return Number.isFinite(f2) && f2 > 0 ? f2 : null;
}

/** Mesh edge length that puts six triangles across a wavelength, in millimetres. */
export function lambdaSixthMm(frequencyHz: number): number {
  // Three significant digits is the resolution the field is edited at, and
  // rounding once here means the warning fires on exactly the number the hint
  // prints -- a 2.86 mm mouth resolution beside a "≈ 2.86 mm" limit must not
  // read as too coarse.
  return Number((SOLVER_SOUND_SPEED_M_PER_S / frequencyHz / 6 * 1_000).toPrecision(3));
}

export interface LambdaHint {
  frequencyLabel: string;
  limitMm: number;
}

export function lambdaSixthHint(design: DesignDocument, sweep: SolvedSweep): LambdaHint | null {
  const frequencyHz = highestSolvedFrequencyHz(design, sweep);
  if (frequencyHz === null) return null;
  return { frequencyLabel: compactFrequency(frequencyHz), limitMm: lambdaSixthMm(frequencyHz) };
}
