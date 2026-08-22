import { resultFrequencyValidity } from './validity';
import type { RadiatedPowerMetadata, ResultPayload } from './types';

export const POWER_AGREEMENT_WARNING_DB = 0.5;

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

export interface PowerAgreementHealth {
  maxDifferenceDb: number;
  frequencyHz: number;
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
  let maximum: PowerAgreementHealth | null = null;
  for (let index = 0; index < power.agreement_db.length; index += 1) {
    const agreement = power.agreement_db[index];
    const frequency = result.frequencies[index];
    if (!finite(agreement) || !finite(frequency) || frequency > upperHz) continue;
    const difference = Math.abs(agreement);
    if (!maximum || difference > maximum.maxDifferenceDb) {
      maximum = { maxDifferenceDb: difference, frequencyHz: frequency };
    }
  }
  return maximum && maximum.maxDifferenceDb > POWER_AGREEMENT_WARNING_DB ? maximum : null;
}
