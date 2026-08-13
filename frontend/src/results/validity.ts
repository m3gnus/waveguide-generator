import type { JobResults } from '../api/results';
import type { ResultPayload } from './types';

export interface SourceFrequencyValidity {
  sourceId: string;
  effectiveMaxFrequencyHz: number;
}

export interface ResultFrequencyValidity {
  governingMaxFrequencyHz: number;
  solvedMaxFrequencyHz: number | null;
  exceedsCeiling: boolean;
  sources: SourceFrequencyValidity[];
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function positiveFinite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function sourceIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === 'string' && item.trim().length > 0)
    .map((item) => item.trim());
}

/**
 * Resolve the CAD wrapper's per-source limits for one channel-scoped curve.
 *
 * The server deliberately keeps this evidence on the wrapper while keeping the
 * source membership on each channel. Joining those two records here is crucial:
 * taking the minimum of every source in the wrapper would assign an unrelated
 * drive channel's ceiling to the curve currently being inspected.
 */
export function resultFrequencyValidity(
  result: ResultPayload,
  wrapper: ResultPayload = result,
): ResultFrequencyValidity | null {
  const resultMetadata = record(result.metadata) ?? {};
  const wrapperMetadata = record(wrapper.metadata) ?? {};
  const perSource = record(
    resultMetadata.per_source_frequency_validity
      ?? wrapperMetadata.per_source_frequency_validity,
  );
  if (!perSource) return null;

  const declaredSourceIds = sourceIds(resultMetadata.source_ids);
  const recordedSourceIds = Object.keys(perSource);
  const channelCount = wrapper.channels ? Object.keys(wrapper.channels).length : 0;
  // Missing membership is safe only when there is exactly one possible join:
  // either the wrapper contains one channel, or the evidence map contains one
  // source. With several channels and several sources we still refuse to guess,
  // because assigning another channel's ceiling is worse than omitting a caveat.
  const relevantSourceIds = declaredSourceIds.length
    ? declaredSourceIds
    : !wrapper.channels || channelCount === 1 || recordedSourceIds.length === 1
      ? recordedSourceIds
      : [];
  const sources = relevantSourceIds.flatMap((sourceId): SourceFrequencyValidity[] => {
    const effectiveMaxFrequencyHz = positiveFinite(
      record(perSource[sourceId])?.effective_max_valid_frequency_hz,
    );
    return effectiveMaxFrequencyHz === null ? [] : [{ sourceId, effectiveMaxFrequencyHz }];
  });
  if (!sources.length) return null;

  const solvedFrequencies = Array.isArray(result.frequencies)
    ? result.frequencies.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
    : [];
  const solvedMaxFrequencyHz = solvedFrequencies.length ? Math.max(...solvedFrequencies) : null;
  const governingMaxFrequencyHz = Math.min(...sources.map(({ effectiveMaxFrequencyHz }) => effectiveMaxFrequencyHz));
  return {
    governingMaxFrequencyHz,
    solvedMaxFrequencyHz,
    exceedsCeiling: solvedMaxFrequencyHz !== null && solvedMaxFrequencyHz > governingMaxFrequencyHz,
    sources,
  };
}

/** Nested combine warnings are advisory result evidence, not failed solves. */
export function resultCombineWarnings(result: JobResults): string[] {
  const combine = record(record(result.metadata)?.combine);
  if (!Array.isArray(combine?.warnings)) return [];
  return combine.warnings.flatMap((warning) => {
    if (typeof warning !== 'string') return [];
    const text = warning.trim();
    return text ? [text] : [];
  });
}

export function formatValidityFrequency(value: number): string {
  if (!Number.isFinite(value)) return '';
  return Math.abs(value) >= 1_000
    ? `${(value / 1_000).toFixed(Math.abs(value) >= 10_000 ? 1 : 2)} kHz`
    : `${Math.round(value)} Hz`;
}
