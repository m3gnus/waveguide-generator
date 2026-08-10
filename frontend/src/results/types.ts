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
