import { groupDelayMilliseconds, propagationReference } from './phaseAnalysis';
import type { ResultPayload } from './types';

export const DERIVED_ACOUSTICS_SCHEMA_VERSION = 1;

export interface DerivedAcousticsRow {
  frequency_hz: number;
  on_axis_spl_db: number | null;
  directivity_index_db: number | null;
  power_response_db_spl_avg: number | null;
  group_delay_ms: number | null;
  horizontal_beamwidth_deg: number | null;
  vertical_beamwidth_deg: number | null;
  spherical_di_db: number | null;
  beam_shape_exponent: number | null;
  beam_fit_residual_percent: number | null;
}

export interface DerivedAcousticsPayload {
  schema_version: number;
  rows: DerivedAcousticsRow[];
  metadata: {
    power_response_formula: string;
    directivity_index_definition: string;
    group_delay_definition: string;
    group_delay_available: boolean;
    group_delay_unavailable_reason: string | null;
    phase_time_convention: string | null;
    observation_distance_m: number | null;
    sound_speed_m_per_s: number | null;
  };
}

function finite(value: unknown): number | null {
  const number = Number(value);
  return value !== null && value !== '' && Number.isFinite(number) ? number : null;
}

function seriesAt(
  frequencies: number[] | undefined,
  values: Array<number | null> | undefined,
): Map<number, number | null> {
  const output = new Map<number, number | null>();
  (frequencies ?? []).forEach((frequency, index) => {
    const key = finite(frequency);
    if (key !== null && !output.has(key)) output.set(key, finite(values?.[index]));
  });
  return output;
}

function flatDi(result: ResultPayload): Array<number | null> {
  const values = result.di?.di;
  if (Array.isArray(values)) return values;
  if (!values) return [];
  return values.horizontal ?? Object.values(values)[0] ?? [];
}

function unionFrequencies(grids: Array<number[] | undefined>): number[] {
  const values = new Set<number>();
  grids.forEach((grid) => (grid ?? []).forEach((value) => {
    const number = finite(value);
    if (number !== null) values.add(number);
  }));
  return [...values].sort((left, right) => left - right);
}

export function buildDerivedAcoustics(result: ResultPayload): DerivedAcousticsPayload {
  const splFrequencies = result.spl_on_axis?.frequencies?.length
    ? result.spl_on_axis.frequencies : result.frequencies;
  const diFrequencies = result.di?.frequencies?.length
    ? result.di.frequencies : result.frequencies;
  const beamFrequencies = result.beam_shape?.frequencies?.length
    ? result.beam_shape.frequencies : result.frequencies;
  const spl = seriesAt(splFrequencies, result.spl_on_axis?.spl);
  const di = seriesAt(diFrequencies, flatDi(result));
  const beam = result.beam_shape;
  const horizontal = seriesAt(beamFrequencies, beam?.horizontal_beamwidth_deg);
  const vertical = seriesAt(beamFrequencies, beam?.vertical_beamwidth_deg);
  const sphericalDi = seriesAt(beamFrequencies, beam?.spherical_di_db);
  const exponent = seriesAt(beamFrequencies, beam?.shape_exponent);
  const residual = seriesAt(beamFrequencies, beam?.fit_residual_percent);
  const reference = propagationReference(result);
  const groupDelay = new Map(groupDelayMilliseconds({
    frequencies: splFrequencies,
    phaseDegrees: result.spl_on_axis?.phase_degrees ?? [],
  }, reference).map(({ frequencyHz, value }) => [frequencyHz, value]));
  const frequencies = unionFrequencies([splFrequencies, diFrequencies, beamFrequencies]);

  const rows = frequencies.map((frequency): DerivedAcousticsRow => {
    const onAxis = spl.get(frequency) ?? null;
    const directivityIndex = di.get(frequency) ?? null;
    return {
      frequency_hz: frequency,
      on_axis_spl_db: onAxis,
      directivity_index_db: directivityIndex,
      power_response_db_spl_avg: onAxis !== null && directivityIndex !== null
        ? onAxis - directivityIndex : null,
      group_delay_ms: groupDelay.get(frequency) ?? null,
      horizontal_beamwidth_deg: horizontal.get(frequency) ?? null,
      vertical_beamwidth_deg: vertical.get(frequency) ?? null,
      spherical_di_db: sphericalDi.get(frequency) ?? null,
      beam_shape_exponent: exponent.get(frequency) ?? null,
      beam_fit_residual_percent: residual.get(frequency) ?? null,
    };
  });

  const observation = result.metadata?.observation;
  return {
    schema_version: DERIVED_ACOUSTICS_SCHEMA_VERSION,
    rows,
    metadata: {
      power_response_formula: 'on_axis_spl_db - directivity_index_db',
      directivity_index_definition: String(
        (result.metadata?.directivity_index as Record<string, unknown> | undefined)?.definition
          ?? 'full-sphere mean-square-pressure directivity index',
      ),
      group_delay_definition: 'excess on-axis delay after removing common propagation time',
      group_delay_available: groupDelay.size > 0,
      group_delay_unavailable_reason: groupDelay.size > 0
        ? null
        : 'A tagged observation distance, sound speed, and sufficiently resolved phase sweep are required.',
      phase_time_convention: result.metadata?.phase_time_convention == null
        ? null : String(result.metadata.phase_time_convention),
      observation_distance_m: finite(
        observation?.effective_distance_m ?? observation?.requested_distance_m,
      ),
      sound_speed_m_per_s: finite(observation?.sound_speed_m_per_s),
    },
  };
}

function csvCell(value: number | null): string {
  return value === null ? '' : String(value);
}

export function buildDerivedAcousticsCsv(result: ResultPayload): string {
  const payload = buildDerivedAcoustics(result);
  const keys = [
    'frequency_hz',
    'on_axis_spl_db',
    'directivity_index_db',
    'power_response_db_spl_avg',
    'group_delay_ms',
    'horizontal_beamwidth_deg',
    'vertical_beamwidth_deg',
    'spherical_di_db',
    'beam_shape_exponent',
    'beam_fit_residual_percent',
  ] as const;
  return `${[
    keys.join(','),
    ...payload.rows.map((row) => keys.map((key) => csvCell(row[key])).join(',')),
  ].join('\n')}\n`;
}

export function buildDerivedAcousticsJson(result: ResultPayload): string {
  return `${JSON.stringify(buildDerivedAcoustics(result), null, 2)}\n`;
}
