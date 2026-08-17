import type { ConfigBlock } from './design';

const ATH_POLAR_PREFIX = 'ABEC.Polars:';
const AXIS_ORDER = ['horizontal', 'vertical', 'diagonal'] as const;
const DIAGONAL_BLOCK = `${ATH_POLAR_PREFIX}SPL_D`;
const EPSILON = 1e-6;

export interface AthPolarUiState {
  angleStart: number;
  angleEnd: number;
  angleStep: number;
  distance: number;
  normAngle: number;
  diagonalAngle: number;
  enabledAxes: Array<(typeof AXIS_ORDER)[number]>;
  observationOrigin: 'mouth' | 'throat';
  sphericalSampling: boolean;
}

export const DEFAULT_ATH_POLAR_UI: AthPolarUiState = Object.freeze({
  angleStart: 0,
  angleEnd: 180,
  angleStep: 5,
  distance: 2,
  normAngle: 5,
  diagonalAngle: 45,
  enabledAxes: [...AXIS_ORDER],
  observationOrigin: 'mouth',
  sphericalSampling: false,
});

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function finite(value: unknown): number | null {
  const numeric = Number(value);
  return value !== null && value !== '' && Number.isFinite(numeric) ? numeric : null;
}

function formatNumeric(value: number): string {
  return String(Object.is(value, -0) ? 0 : value);
}

function classifyInclination(value: unknown): (typeof AXIS_ORDER)[number] {
  const inclination = finite(value) ?? 0;
  const normalized = ((inclination % 360) + 360) % 360;
  const mod180 = normalized % 180;
  if (Math.abs(mod180) < EPSILON || Math.abs(mod180 - 180) < EPSILON) return 'horizontal';
  if (Math.abs(mod180 - 90) < EPSILON) return 'vertical';
  return 'diagonal';
}

function blockItems(block: unknown): Record<string, unknown> {
  return isRecord(block) && isRecord(block.items) ? block.items : {};
}

/**
 * Which plane a polar block describes, block name included.
 *
 * Inclination is the physical truth and normally decides this on its own. It
 * cannot decide the one case where the user picked a diagonal that lies on a
 * cardinal plane: 0/90/180/270 read back as horizontal or vertical, so saving
 * and reopening silently moved the selection off the diagonal the file was
 * written with. WG's own `SPL_D` block states the axis its author chose, so it
 * settles that case. Every other block -- including a hand-edited `SPL_H`
 * carrying a real 45-degree inclination -- is still read from its inclination,
 * which is what an imported ATH file means by it.
 */
function classifyBlock(name: string, items: Record<string, unknown>): (typeof AXIS_ORDER)[number] {
  const byInclination = classifyInclination(items.Inclination);
  return name === DIAGONAL_BLOCK && byInclination !== 'diagonal' ? 'diagonal' : byInclination;
}

function polarEntries(blocks: unknown): Array<[string, unknown]> {
  if (!isRecord(blocks)) return [];
  return Object.entries(blocks)
    .filter(([name]) => name.startsWith(ATH_POLAR_PREFIX))
    .sort(([left], [right]) => left.localeCompare(right, undefined, { sensitivity: 'base' }));
}

function parseRange(value: unknown): Pick<AthPolarUiState, 'angleStart' | 'angleEnd' | 'angleStep'> | null {
  if (typeof value !== 'string') return null;
  const values = value.split(',').map((part) => finite(part.trim()));
  if (values.length !== 3 || values.some((part) => part === null)) return null;
  const [angleStart, angleEnd, rawCount] = values as [number, number, number];
  const count = Math.max(2, Math.floor(rawCount));
  if (angleEnd <= angleStart) return null;
  return {
    angleStart,
    angleEnd,
    angleStep: (angleEnd - angleStart) / (count - 1),
  };
}

/**
 * Read only the directivity settings an ATH/v1 config actually states.
 *
 * `null` means the file says nothing about directivity, which is the case for
 * every ATH file and for every WG design saved before these blocks were
 * written. Callers must leave the user's settings alone in that case rather
 * than falling back to defaults: the file not mentioning a measurement
 * distance is not the file asking for 2 m.
 */
export function athPolarOverrides(blocks: unknown): Partial<AthPolarUiState> | null {
  const entries = polarEntries(blocks);
  if (!entries.length) return null;
  const overrides: Partial<AthPolarUiState> = {};

  const firstItems = blockItems(entries[0][1]);
  const range = parseRange(firstItems.MapAngleRange);
  if (range) Object.assign(overrides, range);
  const distance = finite(firstItems.Distance);
  if (distance !== null) overrides.distance = distance;
  const normAngle = finite(firstItems.NormAngle);
  if (normAngle !== null) overrides.normAngle = normAngle;

  const selected = new Set<(typeof AXIS_ORDER)[number]>();
  for (const [name, block] of entries) {
    const items = blockItems(block);
    const axis = classifyBlock(name, items);
    selected.add(axis);
    if (axis === 'diagonal') {
      const inclination = finite(items.Inclination);
      if (inclination !== null) overrides.diagonalAngle = inclination;
    }
  }
  const enabledAxes = AXIS_ORDER.filter((axis) => selected.has(axis));
  if (enabledAxes.length) overrides.enabledAxes = enabledAxes;
  return overrides;
}

/** Read the exact ABEC polar blocks used by ATH and the v1 config editor. */
export function polarUiFromAthBlocks(blocks: unknown): AthPolarUiState {
  return {
    ...DEFAULT_ATH_POLAR_UI,
    enabledAxes: [...DEFAULT_ATH_POLAR_UI.enabledAxes],
    ...athPolarOverrides(blocks),
  };
}

interface PortablePolarConfig {
  angle_range: [number, number, number];
  distance: number;
  norm_angle: number;
  inclination: number;
  enabled_axes: Array<(typeof AXIS_ORDER)[number]>;
}

function portableBlocksConfig(blocks: unknown): PortablePolarConfig | null {
  const entries = polarEntries(blocks);
  if (!entries.length) return null;
  const firstItems = blockItems(entries[0][1]);
  if (typeof firstItems.MapAngleRange !== 'string') return null;
  const range = firstItems.MapAngleRange.split(',').map((part) => finite(part.trim()));
  const distance = finite(firstItems.Distance);
  const normAngle = finite(firstItems.NormAngle);
  if (range.length !== 3 || range.some((part) => part === null) || distance === null || normAngle === null) return null;
  const [start, end, count] = range as [number, number, number];
  if (end <= start || !Number.isInteger(count) || count < 2) return null;

  const selected = new Set<(typeof AXIS_ORDER)[number]>();
  let inclination = DEFAULT_ATH_POLAR_UI.diagonalAngle;
  for (const [name, block] of entries) {
    const items = blockItems(block);
    const axis = classifyBlock(name, items);
    selected.add(axis);
    if (axis === 'diagonal') inclination = finite(items.Inclination) ?? inclination;
  }
  const enabledAxes = AXIS_ORDER.filter((axis) => selected.has(axis));
  if (!enabledAxes.length) return null;
  return {
    angle_range: [start, end, count],
    distance,
    norm_angle: normAngle,
    inclination,
    enabled_axes: enabledAxes,
  };
}

function portableConfigsEqual(left: PortablePolarConfig, right: PortablePolarConfig): boolean {
  return left.angle_range.every((value, index) => value === right.angle_range[index])
    && left.distance === right.distance
    && left.norm_angle === right.norm_angle
    && left.enabled_axes.length === right.enabled_axes.length
    && left.enabled_axes.every((axis, index) => axis === right.enabled_axes[index])
    && (!left.enabled_axes.includes('diagonal') || left.inclination === right.inclination);
}

function portableConfig(value: unknown): PortablePolarConfig | null {
  if (!isRecord(value) || !Array.isArray(value.angle_range) || value.angle_range.length !== 3) return null;
  const start = finite(value.angle_range[0]);
  const end = finite(value.angle_range[1]);
  const count = finite(value.angle_range[2]);
  const distance = finite(value.distance);
  const normAngle = finite(value.norm_angle);
  const inclination = finite(value.inclination);
  if (start === null || end === null || count === null || distance === null || normAngle === null || inclination === null) return null;
  if (end <= start || !Number.isInteger(count) || count < 2) return null;
  if (!Array.isArray(value.enabled_axes)) return null;
  const requested = new Set(value.enabled_axes.filter((axis): axis is (typeof AXIS_ORDER)[number] => (
    typeof axis === 'string' && (AXIS_ORDER as readonly string[]).includes(axis)
  )));
  const enabledAxes = AXIS_ORDER.filter((axis) => requested.has(axis));
  if (!enabledAxes.length) return null;
  return {
    angle_range: [start, end, count],
    distance,
    norm_angle: normAngle,
    inclination,
    enabled_axes: enabledAxes,
  };
}

function canonicalBlock(config: PortablePolarConfig, inclination?: number): ConfigBlock {
  const items: Record<string, string> = {
    MapAngleRange: config.angle_range.map(formatNumeric).join(','),
    NormAngle: formatNumeric(config.norm_angle),
    Distance: formatNumeric(config.distance),
  };
  if (inclination !== undefined) items.Inclination = formatNumeric(inclination);
  return { items, lines: [], comments: [], entries: [] };
}

/**
 * Replace only ATH's managed polar blocks, retaining Report and every other
 * imported passthrough block byte-for-byte in the server's block model.
 */
export function replaceAthPolarBlocks(
  existing: Record<string, ConfigBlock> | undefined,
  value: unknown,
): Record<string, ConfigBlock> | null {
  const config = portableConfig(value);
  if (!config) return null;
  const imported = portableBlocksConfig(existing);
  // Match v1's untouched-import behavior: an old ATH file whose blocks already
  // describe the selected settings keeps its original inclination spelling,
  // item order, comments, and any ATH extension items.
  if (imported && portableConfigsEqual(imported, config)) return existing ?? {};
  const merged = Object.fromEntries(
    Object.entries(existing ?? {}).filter(([name]) => !name.startsWith(ATH_POLAR_PREFIX)),
  ) as Record<string, ConfigBlock>;
  if (config.enabled_axes.includes('horizontal')) {
    merged['ABEC.Polars:SPL_H'] = canonicalBlock(config);
  }
  if (config.enabled_axes.includes('vertical')) {
    merged['ABEC.Polars:SPL_V'] = canonicalBlock(config, 90);
  }
  if (config.enabled_axes.includes('diagonal')) {
    merged['ABEC.Polars:SPL_D'] = canonicalBlock(config, config.inclination);
  }
  return merged;
}

/** Overlay a solve snapshot's portable directivity settings on a design wire payload. */
export function designWireWithAthPolars(
  design: Record<string, unknown>,
  value: unknown,
): Record<string, unknown> {
  const existing = isRecord(design.extra_blocks)
    ? design.extra_blocks as Record<string, ConfigBlock>
    : {};
  const extraBlocks = replaceAthPolarBlocks(existing, value);
  return extraBlocks === null ? design : { ...design, extra_blocks: extraBlocks };
}
