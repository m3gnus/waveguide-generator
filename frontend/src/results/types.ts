import type { NullableNumber, RadiationImpedancePresentation, ResultData } from '../api/results';

export interface ObservationMetadata {
  requested_distance_m?: number;
  effective_distance_m?: number;
  observation_origin?: string;
  sound_speed_m_per_s?: number;
}

export interface RadiatedPowerMetadata {
  surface_w: NullableNumber[];
  sphere_w: NullableNumber[];
  sphere_coverage_sr: number | null;
  definition: string;
  agreement_db: NullableNumber[];
}

/**
 * What the LR4 sum recorded about the channels it summed.
 *
 * `member_roles` runs parallel to `members`, so a crossover pair can be named
 * by band (`LF → MF`) without loading the ingest record the roles came from.
 * Every field is stated as the server writes it; `combineMetadataOf` is the
 * only sanctioned way to obtain one, because a payload that does not carry
 * both members and crossovers is not a combined channel.
 */
export interface CombineMetadata {
  members: string[];
  member_roles?: Array<string | null>;
  crossovers_hz: number[];
  /** Time alignment applied per member, keyed by channel id. */
  delays_ms?: Record<string, number>;
  /** The server also records the target and the per-member gains it applied. */
  level_match?: { enabled?: boolean; [key: string]: unknown };
  align?: boolean;
  warnings?: string[];
}

export interface ResultMetadata extends Record<string, unknown> {
  observation?: ObservationMetadata;
  /** Electrical impedance is engineering convention even when pressure is solver convention. */
  impedance_phase_convention?: string;
  /** Channel membership used to join wrapper-owned CAD validity evidence. */
  source_ids?: string[];
  /** The driver band this channel speaks for, stamped from the ingest record's
   * source roles. Null when the sources carry no band role, absent on
   * parametric runs and on derived channels such as the combined sum. */
  role?: string | null;
  source_labels?: string[];
  per_source_frequency_validity?: Record<string, {
    effective_max_valid_frequency_hz?: number;
    [key: string]: unknown;
  }>;
  radiated_power?: RadiatedPowerMetadata;
  combine?: Partial<CombineMetadata> & Record<string, unknown>;
}

export interface ResultPayload extends ResultData {
  /** Stored separately from result JSON and joined by the Results surface. */
  radiation_impedance?: RadiationImpedancePresentation;
  directivity_phase?: {
    horizontal?: Array<Array<[number, NullableNumber]>>;
    vertical?: Array<Array<[number, NullableNumber]>>;
    diagonal?: Array<Array<[number, NullableNumber]>>;
  };
  metadata?: ResultMetadata;
  di?: {
    frequencies?: number[];
    di?: NullableNumber[] | Record<string, NullableNumber[]>;
  };
  balloon?: {
    frequencies: number[];
    theta_deg: number[];
    phi_deg: number[];
    spl_norm_db: NullableNumber[][][];
    distance_m?: number;
    hemisphere?: boolean;
  };
  /** Present only on the passive-cardioid coupled channel. */
  passive_cardioid?: {
    cone_excursion_mm?: NullableNumber[];
    [key: string]: unknown;
  };
  beam_shape?: {
    frequencies?: number[];
    level_db?: number;
    di_domain?: string;
    di_sampling_domain?: string;
    valid?: boolean[];
    shape_exponent?: NullableNumber[];
    fit_residual_percent?: NullableNumber[];
    horizontal_beamwidth_deg?: NullableNumber[];
    vertical_beamwidth_deg?: NullableNumber[];
    aspect_ratio?: NullableNumber[];
    spherical_di_db?: NullableNumber[];
  };
}

export interface ResultChannel {
  id: string;
  result: ResultPayload;
}

/** Return imported drive bases in the solver-declared order, then any extras. */
export function resultChannels(result: ResultPayload): ResultChannel[] {
  if (!result.channels) return [];
  const ordered = result.channel_order?.filter((id) => id in result.channels!) ?? [];
  const remaining = Object.keys(result.channels).filter((id) => !ordered.includes(id));
  return [...ordered, ...remaining].map((id) => ({ id, result: result.channels![id] as ResultPayload }));
}

/** The channel's combine record, or null when it is not a combined sum. */
export function combineMetadataOf(payload: ResultPayload | undefined): CombineMetadata | null {
  const combine = payload?.metadata?.combine;
  if (!combine || typeof combine !== 'object') return null;
  if (!Array.isArray(combine.members) || !Array.isArray(combine.crossovers_hz)) return null;
  return combine as CombineMetadata;
}

/**
 * Which channel is the combined sum.
 *
 * Read from the combine record rather than from the id, because the id is only
 * a convention: a return that names its channels itself, or a recombine issued
 * under another id, would otherwise leave the dock with no combined view at
 * all. A drive channel may itself legally be named `combined`, so metadata is
 * the only authoritative discriminator.
 */
export function combinedChannelId(result: ResultPayload): string | null {
  const channels = resultChannels(result);
  const tagged = channels.find(({ result: payload }) => combineMetadataOf(payload));
  return tagged?.id ?? null;
}

function portableSuffixKey(value: string): string {
  // Sanitized suffixes are ASCII, but normalize as well so this comparison stays
  // aligned with portable filesystems if the allowed character set later grows.
  return value.normalize('NFKC').toLocaleLowerCase();
}

/** Allocate every channel suffix as one bundle operation. Sanitizing an id is
 * lossy, so resolving collisions one channel at a time can alias two artifacts. */
export function resultChannelFileSuffixes(result: ResultPayload): ReadonlyMap<string, string> {
  const channels = resultChannels(result);
  if (channels.length <= 1) return new Map(channels.map(({ id }) => [id, '']));
  const used = new Set<string>();
  const suffixes = new Map<string, string>();
  channels.forEach(({ id }) => {
    const base = id.trim().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'channel';
    let allocated: string | null = null;
    // At most N names are occupied and this examines N+1 deterministic names,
    // so a finite bundle must succeed. Keep the explicit refusal as an invariant
    // guard: emitting a duplicate is never an acceptable fallback.
    for (let ordinal = 1; ordinal <= channels.length + 1; ordinal += 1) {
      const candidate = ordinal === 1 ? base : `${base}-${ordinal}`;
      const portable = portableSuffixKey(candidate);
      if (used.has(portable)) continue;
      used.add(portable);
      allocated = `-${candidate}`;
      break;
    }
    if (allocated === null) {
      throw new Error(`Export bundle refused: no unique portable filename suffix could be allocated for channel ${id}.`);
    }
    suffixes.set(id, allocated);
  });
  if (suffixes.size !== channels.length || used.size !== channels.length) {
    throw new Error('Export bundle refused: channel filename suffixes are not portable and unique.');
  }
  return suffixes;
}

/** A filesystem-safe suffix, used only when a wrapper has multiple drive bases. */
export function resultChannelFileSuffix(result: ResultPayload, channelId?: string): string {
  if (!channelId) return '';
  const suffixes = resultChannelFileSuffixes(result);
  const suffix = suffixes.get(channelId);
  if (suffix === undefined) throw new Error(`Drive channel ${channelId} is not present in this result.`);
  return suffix;
}

/** Resolve one imported drive basis; never merge it with a legacy flat path. */
export function scopeResultChannel(result: ResultPayload, channelId?: string): ResultPayload {
  if (!result.channels) return result;
  const available = resultChannels(result).map(({ id }) => id);
  if (!available.length) return result;
  const selected = channelId ?? (available.length === 1 ? available[0] : null);
  if (!selected) throw new Error('Choose a drive channel before exporting this CAD-import result.');
  const payload = result.channels[selected];
  if (!payload) throw new Error(`Drive channel ${selected} is not present in this result.`);
  return payload as ResultPayload;
}
