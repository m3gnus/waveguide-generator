import { formatValidityFrequency, resultFrequencyValidity } from './validity';
import type { RadiatedPowerMetadata, ResultPayload } from './types';

export const POWER_AGREEMENT_WARNING_DB = 0.5;

/** A small minority of flagged samples is reported separately from a broad issue. */
const FEW_AFFECTED_FRACTION = 0.05;

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Feature-detect the additive native-solver metadata block. */
export function radiatedPowerMetadata(result: ResultPayload): RadiatedPowerMetadata | null {
  const raw: unknown = result.metadata?.radiated_power;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null;
  const record = raw as Record<string, unknown>;
  if (!Array.isArray(record.surface_w) || !Array.isArray(record.sphere_w) || !Array.isArray(record.agreement_db)) return null;
  return {
    surface_w: record.surface_w.map((value) => finite(value) ? value : null),
    sphere_w: record.sphere_w.map((value) => finite(value) ? value : null),
    sphere_coverage_sr: finite(record.sphere_coverage_sr) ? record.sphere_coverage_sr : null,
    definition: typeof record.definition === 'string' ? record.definition : '',
    agreement_db: record.agreement_db.map((value) => finite(value) ? value : null),
  };
}

export type PowerAgreementSpread = 'single' | 'few' | 'broad';

export interface PowerAgreementHealth {
  /** Largest |agreement| inside the checked band. */
  maxDifferenceDb: number;
  /** Positive means the far-field sphere integral reads higher. */
  signedDifferenceDb: number;
  frequencyHz: number;
  affectedCount: number;
  checkedCount: number;
  /** Lowest and highest checked frequencies above the warning threshold. */
  affectedSpanHz: [number, number];
  /** Largest |agreement| among the checked frequencies below the threshold. */
  baselineDb: number | null;
  spread: PowerAgreementSpread;
}

/** Largest sphere/surface disagreement inside the result's recorded valid band. */
export function powerAgreementHealth(
  result: ResultPayload,
  wrapper: ResultPayload = result,
): PowerAgreementHealth | null {
  const power = radiatedPowerMetadata(result);
  if (!power) return null;
  const validity = resultFrequencyValidity(result, wrapper);
  const upperHz = validity?.governingMaxFrequencyHz ?? Number.POSITIVE_INFINITY;
  let checkedCount = 0;
  let affectedCount = 0;
  let worst: { signedDifferenceDb: number; frequencyHz: number } | null = null;
  let baselineDb: number | null = null;
  let lowestAffectedHz = Number.POSITIVE_INFINITY;
  let highestAffectedHz = Number.NEGATIVE_INFINITY;
  for (let index = 0; index < power.agreement_db.length; index += 1) {
    const agreement = power.agreement_db[index];
    const frequency = result.frequencies[index];
    if (!finite(agreement) || !finite(frequency) || frequency > upperHz) continue;
    checkedCount += 1;
    const difference = Math.abs(agreement);
    if (difference > POWER_AGREEMENT_WARNING_DB) {
      affectedCount += 1;
      lowestAffectedHz = Math.min(lowestAffectedHz, frequency);
      highestAffectedHz = Math.max(highestAffectedHz, frequency);
      if (!worst || difference > Math.abs(worst.signedDifferenceDb)) {
        worst = { signedDifferenceDb: agreement, frequencyHz: frequency };
      }
    } else if (baselineDb === null || difference > baselineDb) {
      baselineDb = difference;
    }
  }
  if (!worst) return null;

  const spread: PowerAgreementSpread = affectedCount === 1 && affectedCount < checkedCount
    ? 'single'
    : affectedCount < checkedCount && affectedCount / checkedCount <= FEW_AFFECTED_FRACTION
      ? 'few'
      : 'broad';
  return {
    maxDifferenceDb: Math.abs(worst.signedDifferenceDb),
    signedDifferenceDb: worst.signedDifferenceDb,
    frequencyHz: worst.frequencyHz,
    affectedCount,
    checkedCount,
    affectedSpanHz: [lowestAffectedHz, highestAffectedHz],
    baselineDb,
    spread,
  };
}

/** Two decimals: a third digit is not actionable for this consistency check. */
function decibels(value: number): string {
  return `${value >= 0 ? '+' : '−'}${Math.abs(value).toFixed(2)} dB`;
}

/** Compose the compact pill label and the actionable detail in its tooltip. */
export function powerCheckMessage(
  health: PowerAgreementHealth,
): { label: string; title: string } {
  const worst = decibels(health.signedDifferenceDb);
  const worstFrequency = formatValidityFrequency(health.frequencyHz);
  const direction = health.signedDifferenceDb >= 0
    ? 'the far-field sphere integral reads higher than the driven-surface flux'
    : 'the driven-surface flux reads higher than the far-field sphere integral';
  const opening = `At ${worstFrequency}, the two radiated-power estimates differ by ${worst}: ${direction}.`;
  const where = 'Per-frequency surface and sphere power are available in the Derived acoustics export and the static run report.';

  if (health.spread === 'broad') {
    return {
      label: `Power check: ${worst} at ${worstFrequency}`,
      title: `${opening} ${health.affectedCount} of ${health.checkedCount} checked frequencies exceed ${POWER_AGREEMENT_WARNING_DB} dB. A broadly distributed mismatch can indicate poor numerical conditioning, insufficient mesh resolution or spherical sampling, or an invalid symmetry assumption. Investigate those causes before trusting the flagged results. ${where}`,
    };
  }

  const remainder = health.baselineDb === null
    ? ''
    : ` The remaining checked frequencies agree within ${health.baselineDb.toFixed(2)} dB on this power cross-check.`;
  if (health.spread === 'single') {
    return {
      label: `Power check: ${worst} at ${worstFrequency}`,
      title: `${opening}${remainder} One of ${health.checkedCount} checked frequencies exceeds ${POWER_AGREEMENT_WARNING_DB} dB. Treat results at ${worstFrequency} cautiously and re-solve a denser sweep around it; if the mismatch persists, refine the mesh and spherical sampling. ${where}`,
    };
  }

  const [low, high] = health.affectedSpanHz;
  const span = `${formatValidityFrequency(low)}–${formatValidityFrequency(high)}`;
  return {
    label: `Power check: ${worst} at ${worstFrequency}`,
    title: `${opening}${remainder} ${health.affectedCount} of ${health.checkedCount} checked frequencies exceed ${POWER_AGREEMENT_WARNING_DB} dB, spanning ${span}. Treat that range cautiously and re-solve it more densely; if the mismatch persists, refine the mesh and spherical sampling. ${where}`,
  };
}
