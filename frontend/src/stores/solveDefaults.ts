/**
 * WG's default solve settings, read from `shared/solve-defaults.json`.
 *
 * That file is the one statement of them: the initial solve settings here
 * (parametric and CAD share them) and the backend's default CAD setup
 * (`server/cadlink/default_setup.py`), which a first-time model sent from
 * Fusion is solved with, both read it. Change a default there, not here.
 */
import defaults from '../../../shared/solve-defaults.json';
import type { SolverMode } from './solveOptions';

type PolarAxisName = 'horizontal' | 'vertical' | 'diagonal';

export interface DefaultPolarUi {
  angleStart: number;
  angleEnd: number;
  angleStep: number;
  distance: number;
  normAngle: number;
  diagonalAngle: number;
  enabledAxes: PolarAxisName[];
  observationOrigin: 'mouth' | 'throat';
  sphericalSampling: boolean;
  fieldPlane: boolean;
}

const directivity = defaults.directivity;

export const DEFAULT_POLAR_UI: Readonly<DefaultPolarUi> = Object.freeze({
  angleStart: directivity.angle_start_deg,
  angleEnd: directivity.angle_end_deg,
  angleStep: directivity.angle_step_deg,
  distance: directivity.distance_m,
  normAngle: directivity.norm_angle_deg,
  diagonalAngle: directivity.diagonal_inclination_deg,
  enabledAxes: [...directivity.enabled_axes] as PolarAxisName[],
  observationOrigin: directivity.observation_origin as 'mouth' | 'throat',
  sphericalSampling: directivity.spherical_sampling,
  fieldPlane: directivity.field_plane,
});

/**
 * How many frequencies a default sweep has: `ceil(log2(end / start) *
 * pointsPerOctave) + 1` (50 Hz-20 kHz at 4 per octave: 36). The backend's
 * `sweep_points` (server/cadlink/default_setup.py) states the same rule; the
 * cases in server/tests/fixtures/cad_default_setup/sweep_points.json hold
 * both to it. The tolerance keeps a whole number of octaves from rounding up.
 */
export function sweepPoints(startHz: number, endHz: number, pointsPerOctave: number): number {
  return Math.ceil(Math.log2(endHz / startHz) * pointsPerOctave - 1e-9) + 1;
}

export const DEFAULT_SWEEP = Object.freeze({
  startHz: defaults.sweep.start_hz,
  endHz: defaults.sweep.end_hz,
  pointsPerOctave: defaults.sweep.points_per_octave,
  points: sweepPoints(defaults.sweep.start_hz, defaults.sweep.end_hz, defaults.sweep.points_per_octave),
  spacing: defaults.sweep.spacing as 'log' | 'linear',
});

export const DEFAULT_SOLVER = Object.freeze({
  engine: defaults.solver.engine,
  accuracy: defaults.solver.accuracy as 'fast' | 'accurate',
  solverMode: defaults.solver.solver_mode as SolverMode,
  symmetry: defaults.solver.symmetry as 'auto' | 'full' | 'half_xz' | 'half_yz' | 'quarter',
  meshValidationMode: defaults.solver.mesh_validation_mode as 'warn' | 'strict' | 'off',
  verbose: defaults.solver.verbose,
});

export const DEFAULT_GROUND_PLANE = Object.freeze({
  enabled: defaults.ground_plane.enabled,
  axis: defaults.ground_plane.axis as 'x' | 'y' | 'z',
  height_m: defaults.ground_plane.height_m,
});

const cad = defaults.cad;

/** Volts at the terminals once a channel has a driver; none has one by default. */
export const DEFAULT_DRIVE_VOLTAGE_V: number = cad.drive_voltage_v;
export const DEFAULT_CAD_PREPARATION_SYMMETRY = cad.preparation_symmetry_mode as 'auto' | 'full';
export const DEFAULT_EXTERIOR_ONLY: boolean = cad.exterior_only;
/** The mesh size the rail starts from when a return suggests none at all. */
export const UNSUGGESTED_MESH_SIZE_MM: number = cad.unsuggested_mesh_size_mm;

/** The combined output's default chain: its slope, the band roles lowest
 * first, and the crossover each pair of banded roles starts at. */
export const DEFAULT_CROSSOVER = Object.freeze({
  family: cad.crossover.family as 'lr' | 'butterworth' | 'bessel' | 'linear_phase',
  order: cad.crossover.order,
  bandRank: Object.freeze(Object.fromEntries(
    cad.crossover.band_roles.map((role, index) => [role, index]),
  )) as Readonly<Record<string, number>>,
  roleHz: Object.freeze(Object.fromEntries(
    cad.crossover.role_crossovers_hz.map(({ lower, upper, hz }) => [`${lower}→${upper}`, hz]),
  )) as Readonly<Record<string, number>>,
});
