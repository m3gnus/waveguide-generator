import type { SolveAccuracy } from './solveOptions';
import { namespaceStorage } from './durableSettings';
import { parseExteriorTransducer } from './exteriorTransducer';
import { expandLegacy, parseSpec, type CrossoverSpec } from '../results/crossoverSpec';
import { DRIVER_FIELD_KEYS, PASSIVE_CARDIOID_DEFAULTS, PASSIVE_CARDIOID_NUMBER_FIELDS,
  normalizePassiveCardioid, chainDefaults, memberOrder, combineLevelMatchDefault,
  sourceInventorySignature, type CadReturnState, type CadDriveChannel, type DriverFieldKey,
  type DriverPreset, type ChannelDriverForm, type PassiveCardioidForm,
  type PassiveCardioidNumberField, type RoleSource, type SourceInventoryCarrier } from './cadReturn';
const solveProfileStorage = namespaceStorage('cadSolveProfiles');
function solveProfileKey(owner: string, inventory: SourceInventoryCarrier): string {
  return JSON.stringify([owner, sourceInventorySignature(inventory)]);
}
export function dropStoredSolveProfiles(): void { solveProfileStorage.removeItem(''); }
/**
 * Version 1 stored the combined output as a plain boolean whose `false` was
 * the old default rather than a decision. The combined output now defaults on
 * for a multi-driver return, so a version 1 `false` is restored as "no choice
 * yet" — otherwise every profile written before this change would silently
 * keep the combined output off while the rail says it is on by default.
 */
const LEGACY_SOLVE_PROFILE_VERSION = 1;
/**
 * Versions 1 and 2 stored the crossover as a sparse `combineCrossoversHz` map
 * plus `combineLevelMatch` and `combineAlign`. Version 3 stores one spec
 * override instead; `combineSpecFromLegacy` is the one place that converts.
 */
const LEGACY_COMBINE_FIELD_VERSIONS = new Set([1, 2]);
/**
 * Versions 1 to 3 filed a profile under the open design's identity. For a
 * project that only exists in CAD that identity belongs to whatever design
 * happened to be open -- so the drivers picked for a Fusion document were
 * stored against an unrelated parametric design and were gone the moment
 * another one was opened. Version 4 files it under the project instead, and
 * migrates an older entry to the design owner it already had.
 */
const LEGACY_DESIGN_OWNED_VERSIONS = new Set([1, 2, 3]);
const SUPPORTED_SOLVE_PROFILE_VERSIONS = new Set([1, 2, 3, 4]);
const MAX_SOLVE_PROFILES = 20;

export type PersistedSolveSettings = Pick<CadReturnState,
  'skippedSourceIds' | 'driveChannels'
  | 'exteriorOnly' | 'exteriorOnlyRegionIds' | 'combineEnabled' | 'combineSpec'
  | 'channelDrivers' | 'passiveCardioid' | 'driveVoltageV' | 'maxDriveVoltageV'
  | 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'> & {
  sourceSizesMm: Record<string, number>; rigidSizeMm: number; transitionMm: number;
};

interface SourceInventoryEntry {
  id: string;
  role: string;
  required: boolean;
}

interface StoredSolveProfile {
  key: string;
  /** The owner recorded by the old profile format. */
  owner: string;
  inventory: SourceInventoryEntry[];
  settings: PersistedSolveSettings;
  solveSelection?: { accuracy?: SolveAccuracy; engine: string };
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function finiteNumberRecord(value: unknown): Record<string, number> | null {
  if (!isObject(value)) return null;
  const entries = Object.entries(value);
  if (entries.some(([key, item]) => !key || typeof item !== 'number' || !Number.isFinite(item))) return null;
  return Object.fromEntries(entries) as Record<string, number>;
}

function stringArray(value: unknown): string[] | null {
  if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) return null;
  return [...value];
}

function parseInventory(value: unknown): SourceInventoryEntry[] | null {
  if (!Array.isArray(value)) return null;
  const inventory = value.flatMap((item): SourceInventoryEntry[] => (
    isObject(item)
      && typeof item.id === 'string' && item.id.length > 0
      && typeof item.role === 'string'
      && typeof item.required === 'boolean'
      ? [{ id: item.id, role: item.role, required: item.required }]
      : []
  ));
  if (inventory.length !== value.length || new Set(inventory.map(({ id }) => id)).size !== inventory.length) return null;
  return inventory;
}

function parseDriveChannels(value: unknown, inventory: SourceInventoryEntry[], skippedSourceIds: string[]): CadDriveChannel[] | null {
  if (!Array.isArray(value)) return null;
  const sourceIds = new Set(inventory.map(({ id }) => id));
  const skipped = new Set(skippedSourceIds);
  const assigned = new Set<string>();
  const channels = value.flatMap((item): CadDriveChannel[] => {
    if (!isObject(item) || typeof item.id !== 'string' || !item.id
      || (item.motion !== 'normal' && item.motion !== 'axial')) return [];
    const ids = stringArray(item.source_ids);
    if (!ids?.length || ids.some((id) => !sourceIds.has(id) || skipped.has(id) || assigned.has(id))) return [];
    ids.forEach((id) => assigned.add(id));
    return [{ id: item.id, source_ids: ids, motion: item.motion,
      ...(item.exterior_transducer !== undefined ? { exterior_transducer: parseExteriorTransducer(item.exterior_transducer) } : {}) }];
  });
  return channels.length === value.length ? channels : null;
}

function parseDriverFields(value: unknown): Partial<Record<DriverFieldKey, number>> | null {
  if (!isObject(value)) return null;
  const fields: Partial<Record<DriverFieldKey, number>> = {};
  for (const [field, fieldValue] of Object.entries(value)) {
    if (!DRIVER_FIELD_KEYS.includes(field as DriverFieldKey)
      || typeof fieldValue !== 'number' || !Number.isFinite(fieldValue)) return null;
    fields[field as DriverFieldKey] = fieldValue;
  }
  return fields;
}

/**
 * A stored preset, or `null` for a profile written before drivers were picked.
 *
 * Absence is the migration: every profile saved before the picker existed
 * carries hand-typed `fields` and no preset, and those numbers are still the
 * whole driver. Anything present is parsed strictly, as everywhere else here.
 */
function parseDriverPreset(value: unknown): DriverPreset | null | undefined {
  if (value === undefined || value === null) return null;
  if (!isObject(value)) return undefined;
  const { id, label, source, kind, z_ohm: z, xo_min_hz: xoMin } = value;
  if (typeof id !== 'string' || !id || typeof label !== 'string' || !label) return undefined;
  if (source !== 'database' && source !== 'mine' && source !== 'manual') return undefined;
  if (kind !== 'lf' && kind !== 'cd' && kind !== 'unknown') return undefined;
  if (z !== null && (typeof z !== 'number' || !Number.isFinite(z))) return undefined;
  // Absent is the migration: a preset stored before this field existed. A
  // present value is parsed strictly, same as every other field here.
  if (xoMin !== undefined && xoMin !== null && (typeof xoMin !== 'number' || !Number.isFinite(xoMin))) return undefined;
  const base = parseDriverFields(value.base);
  if (!base) return undefined;
  return { id, label, source, kind, z_ohm: z, xo_min_hz: typeof xoMin === 'number' ? xoMin : null, base };
}

function parseChannelDrivers(value: unknown): Record<string, ChannelDriverForm> | null {
  if (!isObject(value)) return null;
  const drivers: Record<string, ChannelDriverForm> = {};
  for (const [channelId, item] of Object.entries(value)) {
    if (!channelId || !isObject(item)) return null;
    // Migration: forms stored before "present = driven" carried an `enabled`
    // flag. Its value is deliberately ignored — presence of a preset or T/S
    // fields is what drives a channel now, so a stored driver that was
    // switched off comes back driven. A present flag still has to be a
    // boolean, same strictness as every other field here.
    if (item.enabled !== undefined && typeof item.enabled !== 'boolean') return null;
    const fields = parseDriverFields(item.fields);
    const preset = parseDriverPreset(item.preset);
    if (!fields || preset === undefined) return null;
    if (item.frozen !== undefined && typeof item.frozen !== 'boolean') return null;
    drivers[channelId] = { fields, preset, ...(item.frozen !== undefined ? { frozen: item.frozen } : {}) };
  }
  return drivers;
}

/**
 * A stored cardioid form, or the defaults when the profile predates the field.
 *
 * Absence is tolerated where every neighbouring parse is strict: profiles
 * written before this section existed carry no key, and dropping every stored
 * mesh size and driver over a feature the user has not used would be a worse
 * answer than starting that one section off. Anything present is still parsed
 * strictly, and a malformed form fails the whole profile as usual.
 */
function parsePassiveCardioid(value: unknown): PassiveCardioidForm | null | undefined {
  if (value === undefined) return { ...PASSIVE_CARDIOID_DEFAULTS };
  if (!isObject(value)) return null;
  if (typeof value.enabled !== 'boolean'
    || typeof value.invertPort !== 'boolean'
    || typeof value.coupled !== 'boolean'
    || (value.portAreaSource !== 'user' && value.portAreaSource !== 'bem_aperture')) return null;
  const numbers: Partial<Record<PassiveCardioidNumberField, number | null>> = {};
  for (const field of PASSIVE_CARDIOID_NUMBER_FIELDS) {
    const item = value[field];
    if (item === null || item === undefined) { numbers[field] = null; continue; }
    if (typeof item !== 'number' || !Number.isFinite(item)) return null;
    numbers[field] = item;
  }
  return normalizePassiveCardioid({
    enabled: value.enabled,
    rearVolumeL: numbers.rearVolumeL ?? null,
    portLengthMm: numbers.portLengthMm ?? null,
    modelPortAreaM2: numbers.modelPortAreaM2 ?? null,
    bemPortAreaM2: numbers.bemPortAreaM2 ?? null,
    portAreaSource: value.portAreaSource,
    foamResistancePaSM3: numbers.foamResistancePaSM3 ?? null,
    invertPort: value.invertPort,
    coupled: value.coupled,
  });
}

/**
 * Convert a version 1/2 profile's crossover fields into one spec override.
 *
 * The old fields were sparse: only the pairs the user actually typed were
 * stored, and the two booleans were nullable "no choice yet". A profile that
 * touched none of them therefore migrates to `null` — no override — rather
 * than to a spec that would freeze today's defaults into stored state.
 */
function combineSpecFromLegacy(
  settings: Omit<PersistedSolveSettings, 'combineSpec'>,
  sources: readonly RoleSource[],
  crossoversHz: Record<string, number>,
  levelMatch: boolean | null,
  align: boolean | null,
): CrossoverSpec | null {
  const touched = Object.keys(crossoversHz).length > 0 || levelMatch !== null || align !== null;
  if (!touched) return null;
  const pairs = chainDefaults(settings.driveChannels, sources, settings.frequencyStartHz, settings.frequencyEndHz);
  if (pairs.length < 1) return null;
  return expandLegacy(
    memberOrder(settings.driveChannels, sources),
    pairs.map((pair) => crossoversHz[pair.key] ?? pair.defaultOrFallbackHz),
    levelMatch ?? combineLevelMatchDefault(settings),
    align ?? true,
  );
}

export function parseSolveSettings(
  value: unknown,
  inventory: SourceInventoryEntry[],
  version: number,
): PersistedSolveSettings | null {
  if (!isObject(value)) return null;
  const legacyCombineFields = LEGACY_COMBINE_FIELD_VERSIONS.has(version);
  const sourceSizesMm = finiteNumberRecord(value.sourceSizesMm);
  const combineCrossoversHz = legacyCombineFields ? finiteNumberRecord(value.combineCrossoversHz) : {};
  const skippedSourceIds = stringArray(value.skippedSourceIds);
  const channelDrivers = parseChannelDrivers(value.channelDrivers);
  const passiveCardioid = parsePassiveCardioid(value.passiveCardioid);
  if (!sourceSizesMm || !combineCrossoversHz || !skippedSourceIds || !channelDrivers
    || !passiveCardioid) return null;
  const inventoryIds = new Set(inventory.map(({ id }) => id));
  if (Object.keys(sourceSizesMm).some((id) => !inventoryIds.has(id))
    || skippedSourceIds.some((id) => !inventoryIds.has(id))
    || new Set(skippedSourceIds).size !== skippedSourceIds.length) return null;
  const driveChannels = parseDriveChannels(value.driveChannels, inventory, skippedSourceIds);
  if (!driveChannels) return null;
  const legacyLevelMatch = legacyCombineFields ? value.combineLevelMatch ?? null : null;
  const legacyAlign = legacyCombineFields ? value.combineAlign ?? null : null;
  if (typeof value.rigidSizeMm !== 'number' || !Number.isFinite(value.rigidSizeMm)
    || typeof value.transitionMm !== 'number' || !Number.isFinite(value.transitionMm)
    || typeof value.exteriorOnly !== 'boolean'
    || (value.combineEnabled !== null && typeof value.combineEnabled !== 'boolean')
    || (legacyLevelMatch !== null && typeof legacyLevelMatch !== 'boolean')
    || (legacyAlign !== null && typeof legacyAlign !== 'boolean')
    || typeof value.driveVoltageV !== 'number' || !Number.isFinite(value.driveVoltageV)
    || typeof value.frequencyStartHz !== 'number' || !Number.isFinite(value.frequencyStartHz)
    || typeof value.frequencyEndHz !== 'number' || !Number.isFinite(value.frequencyEndHz)
    || typeof value.frequencyCount !== 'number' || !Number.isFinite(value.frequencyCount)) return null;
  const settings = {
    sourceSizesMm,
    rigidSizeMm: value.rigidSizeMm,
    transitionMm: value.transitionMm,
    skippedSourceIds,
    driveChannels,
    exteriorOnly: value.exteriorOnly,
    exteriorOnlyRegionIds: stringArray(value.exteriorOnlyRegionIds) ?? null,
    combineEnabled: value.combineEnabled,
    channelDrivers,
    passiveCardioid,
    driveVoltageV: value.driveVoltageV,
    // Absent is a profile stored before an amplifier could be stated, which
    // means the drivers were the only ceiling -- exactly what null says.
    maxDriveVoltageV: typeof value.maxDriveVoltageV === 'number'
      && Number.isFinite(value.maxDriveVoltageV) && value.maxDriveVoltageV > 0
      ? value.maxDriveVoltageV
      : null,
    frequencyStartHz: value.frequencyStartHz,
    frequencyEndHz: value.frequencyEndHz,
    frequencyCount: value.frequencyCount,
  };
  if (legacyCombineFields) {
    return {
      ...settings,
      combineSpec: combineSpecFromLegacy(settings, inventory, combineCrossoversHz, legacyLevelMatch, legacyAlign),
    };
  }
  // An absent key is "no override", the same as a stored null; a present but
  // malformed one fails the whole profile, as every other field here does.
  if (value.combineSpec !== null && value.combineSpec !== undefined && !parseSpec(value.combineSpec)) return null;
  return { ...settings, combineSpec: parseSpec(value.combineSpec ?? null) };
}

export function readLegacySolveProfiles(): StoredSolveProfile[] {
  try {
    const raw = solveProfileStorage.getItem('cadSolveProfiles');
    if (raw === null) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!isObject(parsed)
      || typeof parsed.version !== 'number'
      || !SUPPORTED_SOLVE_PROFILE_VERSIONS.has(parsed.version)
      || !Array.isArray(parsed.profiles) || parsed.profiles.length > MAX_SOLVE_PROFILES) {
      dropStoredSolveProfiles();
      return [];
    }
    const version = parsed.version;
    const legacyCombineChoice = version === LEGACY_SOLVE_PROFILE_VERSION;
    const designOwned = LEGACY_DESIGN_OWNED_VERSIONS.has(version);
    const profiles: StoredSolveProfile[] = [];
    for (const value of parsed.profiles) {
      // A migrated entry is re-keyed rather than re-validated against the key
      // its own version wrote: the owner is the thing that changed.
      if (!isObject(value)) continue;
      const owner = designOwned
        ? (typeof value.designId === 'string' && value.designId
          && typeof value.lineageId === 'string' && value.lineageId
          ? `design:${value.designId}:${value.lineageId}`
          : null)
        : (isObject(value) && typeof value.owner === 'string' && value.owner ? value.owner : null);
      if (!isObject(value) || typeof value.key !== 'string' || !owner) {
        continue;
      }
      const inventory = parseInventory(value.inventory);
      const settings = inventory ? parseSolveSettings(value.settings, inventory, version) : null;
      if (!inventory || !settings) {
        continue;
      }
      const key = solveProfileKey(owner, { readable: true, sources: inventory });
      if (!designOwned && value.key !== key) {
        continue;
      }
      profiles.push({
        key,
        owner,
        inventory,
        settings: legacyCombineChoice && settings.combineEnabled === false
          ? { ...settings, combineEnabled: null }
          : settings,
        ...(isObject(value.solveSelection)
          && typeof value.solveSelection.engine === 'string'
          ? { solveSelection: {
              accuracy: value.solveSelection.accuracy === 'accurate' ? 'accurate' as const : 'fast' as const,
              engine: value.solveSelection.engine,
            } }
          : {}),
      });
    }
    return profiles.filter((profile, index) => profiles.findIndex(other => other.key === profile.key) === index);
  } catch {
    dropStoredSolveProfiles();
    return [];
  }
}
