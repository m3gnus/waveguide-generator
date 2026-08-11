import type { JobResults, NullableNumber } from '../api/results';

export interface ObservationMetadata {
  requested_distance_m?: number;
  effective_distance_m?: number;
  sound_speed_m_per_s?: number;
}

export interface ResultMetadata extends Record<string, unknown> {
  observation?: ObservationMetadata;
}

export interface ResultPayload extends JobResults {
  directivity_phase?: {
    horizontal?: Array<Array<[number, NullableNumber]>>;
    vertical?: Array<Array<[number, NullableNumber]>>;
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
  beam_shape?: {
    frequencies?: number[];
    level_db?: number;
    di_domain?: string;
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

/** A filesystem-safe suffix, used only when a wrapper has multiple drive bases. */
export function resultChannelFileSuffix(result: ResultPayload, channelId?: string): string {
  if (!channelId || resultChannels(result).length <= 1) return '';
  const safe = channelId.trim().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'channel';
  return `-${safe}`;
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
