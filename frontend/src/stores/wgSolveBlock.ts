/**
 * The solver settings a design file carries, in WG's own config block.
 *
 * `ABEC.Polars:*` covers what ATH itself understands -- the sweep range, the
 * measurement distance, the normalization angle, and which planes are drawn.
 * Everything else a solve depends on lived only in the browser, so a `.cfg`
 * described the horn but not the simulation it belonged to: reopening a design
 * silently reused whatever mesh policy, sweep spacing, frequency list, and
 * observation origin happened to be set at the time.
 *
 * These keys go in a `WG.Solve` block rather than alongside ATH's own. ATH
 * ignores blocks it does not recognise, and WG's importer preserves unknown
 * blocks verbatim, so a file stays readable by both and a round-trip through
 * either tool does not drop the other's settings.
 */
import type { ConfigBlock } from './design';
import type {
  FrequencyMode,
  FrequencySpacing,
  MeshValidationMode,
  ObservationOrigin,
  SymmetryMode,
} from './solveOptions';
import { MAX_FREQUENCY_POINTS, parseFrequencyList } from './frequencyList';

export const WG_SOLVE_BLOCK = 'WG.Solve';

/** Portable solve settings that belong to the design rather than to the machine. */
export interface WgSolveSettings {
  symmetry: SymmetryMode;
  meshValidationMode: MeshValidationMode;
  verbose: boolean;
  frequencySpacing: FrequencySpacing;
  frequencyMode: FrequencyMode;
  frequencyListText: string;
  observationOrigin: ObservationOrigin;
  sphericalSampling: boolean;
  fieldPlane: boolean;
}

/**
 * The legal members of each solver enum, exported so the store that persists
 * them normalizes against exactly the list the config reader accepts. They
 * live here rather than in `solveOptions` because that module imports this one
 * at runtime and this one only imports its types back.
 */
export const MESH_VALIDATION_MODES: MeshValidationMode[] = ['warn', 'strict', 'off'];
export const FREQUENCY_SPACINGS: FrequencySpacing[] = ['log', 'linear'];
export const FREQUENCY_MODES: FrequencyMode[] = ['range', 'list'];
export const OBSERVATION_ORIGINS: ObservationOrigin[] = ['mouth', 'throat'];
export const SYMMETRY_MODES: SymmetryMode[] = ['auto', 'full', 'half_xz', 'half_yz', 'quarter'];
/** Engine names come from the backend registry, so only the shape is checked. */
export const ENGINE_PATTERN = /^[A-Za-z0-9_-]{1,32}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function items(blocks: unknown): Record<string, unknown> | null {
  if (!isRecord(blocks)) return null;
  const block = blocks[WG_SOLVE_BLOCK];
  return isRecord(block) && isRecord(block.items) ? block.items : null;
}

function oneOf<T extends string>(value: unknown, allowed: T[]): T | undefined {
  return typeof value === 'string' && (allowed as string[]).includes(value) ? value as T : undefined;
}

function bool(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value;
  if (value === '1' || value === 1) return true;
  if (value === '0' || value === 0) return false;
  return undefined;
}

/**
 * Read back only what the block states.
 *
 * `null` means the file has no `WG.Solve` block at all, and an absent key
 * inside one means the same thing for that setting: the user's current choice
 * stands. Nothing here is repaired -- a value that cannot be read as written
 * is dropped rather than guessed at, so a hand-edited file can never quietly
 * change a solve into one the author did not describe.
 */
export function wgSolveOverrides(blocks: unknown): Partial<WgSolveSettings> | null {
  const source = items(blocks);
  if (!source) return null;
  const overrides: Partial<WgSolveSettings> = {};

  // Legacy blocks may contain Engine. It is intentionally ignored: backend
  // selection belongs to this machine and must not be overwritten by opening
  // somebody else's design.

  const symmetry = oneOf(source.Symmetry, SYMMETRY_MODES);
  if (symmetry) overrides.symmetry = symmetry;

  const meshValidation = oneOf(source.MeshValidation, MESH_VALIDATION_MODES);
  if (meshValidation) overrides.meshValidationMode = meshValidation;

  const verbose = bool(source.Verbose);
  if (verbose !== undefined) overrides.verbose = verbose;

  const spacing = oneOf(source.SweepSpacing, FREQUENCY_SPACINGS);
  if (spacing) overrides.frequencySpacing = spacing;

  const origin = oneOf(source.ObservationOrigin, OBSERVATION_ORIGINS);
  if (origin) overrides.observationOrigin = origin;

  const spherical = bool(source.SphericalSampling);
  if (spherical !== undefined) overrides.sphericalSampling = spherical;

  const fieldPlane = bool(source.FieldPlane);
  if (fieldPlane !== undefined) overrides.fieldPlane = fieldPlane;

  // A stored list is adopted only when it parses exactly as typed. Restoring
  // list mode without a usable list would block every solve until the user
  // found the sweep panel, so the mode follows the list, not the other way.
  const listText = typeof source.Frequencies === 'string' ? source.Frequencies.trim() : '';
  const mode = oneOf(source.SweepPoints, FREQUENCY_MODES);
  if (listText) {
    const { frequencies } = parseFrequencyList(listText);
    if (frequencies && frequencies.length <= MAX_FREQUENCY_POINTS) {
      overrides.frequencyListText = listText;
      overrides.frequencyMode = mode ?? 'list';
    }
  } else if (mode === 'range') {
    overrides.frequencyMode = 'range';
  }
  return overrides;
}

/**
 * Write the block, replacing any earlier one and leaving every other block
 * untouched so imported ATH passthrough survives the round trip.
 */
export function withWgSolveBlock(
  existing: Record<string, ConfigBlock> | undefined,
  settings: WgSolveSettings,
): Record<string, ConfigBlock> {
  const values: Record<string, string> = {
    Symmetry: settings.symmetry,
    MeshValidation: settings.meshValidationMode,
    Verbose: settings.verbose ? '1' : '0',
    SweepPoints: settings.frequencyMode,
    SweepSpacing: settings.frequencySpacing,
    ObservationOrigin: settings.observationOrigin,
    SphericalSampling: settings.sphericalSampling ? '1' : '0',
    FieldPlane: settings.fieldPlane ? '1' : '0',
  };
  // Only a usable list is written. Persisting a half-typed one would hand the
  // next reader a file that refuses to solve.
  if (settings.frequencyMode === 'list') {
    const { frequencies } = parseFrequencyList(settings.frequencyListText);
    if (frequencies) values.Frequencies = frequencies.join(', ');
    else values.SweepPoints = 'range';
  }
  return {
    ...(existing ?? {}),
    [WG_SOLVE_BLOCK]: { items: values, lines: [], comments: [], entries: [] },
  };
}
