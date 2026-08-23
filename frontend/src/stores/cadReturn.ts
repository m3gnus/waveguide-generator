import { create } from 'zustand';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import type { DriverKind } from '../api/drivers';
import {
  cloneSpec,
  expandLegacy,
  pairsOf,
  parseSpec,
  toWire,
  withPair,
  type CrossoverSpec,
  type CrossoverWire,
  type FilterFamily,
} from '../results/crossoverSpec';
import { useDocumentStore, type DesignIdentity } from './document';
import { namespaceStorage } from './durableSettings';

export interface CadDriveChannel {
  id: string;
  source_ids: string[];
  motion: 'normal' | 'axial';
}

/**
 * The T/S fields the rail can hold, in wire order.
 *
 * The alternatives the server already accepts (`mms_g`, `vas_l`, `fs_hz`,
 * `qms`) are here because that is what a driver database publishes: a
 * datasheet gives Mms, Fs and Vas far more often than it gives Mmd and Cms.
 * Each new key is inserted beside the one it substitutes for, so the emitted
 * key order of a hand-entered Mmd/Cms driver is byte-for-byte what it was.
 */
export const DRIVER_FIELD_KEYS = [
  'sd_cm2', 'bl_t_m', 're_ohm', 'le_mh', 'mmd_g', 'mms_g', 'cms_m_per_n',
  'vas_l', 'fs_hz', 'qms', 'rms_kg_per_s', 'xmax_mm', 'count', 'rear_volume_l',
] as const;
export type DriverFieldKey = typeof DRIVER_FIELD_KEYS[number];
/** Always required, whatever else is supplied (`DriverSpec`, non-null fields). */
export const DRIVER_REQUIRED_KEYS: readonly DriverFieldKey[] = ['sd_cm2', 'bl_t_m', 're_ohm'];
/** Exactly one of these reaches the wire — `DriverSpec.validate_completeness`
 * refuses a spec carrying both, and refuses one carrying neither. */
export const DRIVER_MASS_KEYS: readonly DriverFieldKey[] = ['mms_g', 'mmd_g'];
/** At least one of these is required; the solver derives the rest. */
export const DRIVER_COMPLIANCE_KEYS: readonly DriverFieldKey[] = ['cms_m_per_n', 'vas_l', 'fs_hz'];
/** WG's own inputs. They describe the installation, not the driver, so they
 * are never part of a preset's base values and never count as an edit of one. */
export const DRIVER_INSTALLATION_KEYS: readonly DriverFieldKey[] = ['count', 'rear_volume_l'];

export type { DriverKind };
export type DriverPresetSource = 'database' | 'mine' | 'manual';

/**
 * The driver a channel was filled in from.
 *
 * `base` is the picked driver's own numbers; `ChannelDriverForm.fields` holds
 * only what the user changed on top of them. Keeping the two apart is what
 * lets *Reset to database values* exist at all, and what lets an impedance
 * variant reload its base without discarding the user's edits.
 */
export interface DriverPreset {
  id: string;
  label: string;
  source: DriverPresetSource;
  kind: DriverKind;
  z_ohm: number | null;
  base: Partial<Record<DriverFieldKey, number>>;
}

export interface ChannelDriverForm {
  enabled: boolean;
  /** Overrides on top of `preset.base`; the whole driver when there is none. */
  fields: Partial<Record<DriverFieldKey, number>>;
  preset: DriverPreset | null;
}

const EMPTY_DRIVER_FORM: ChannelDriverForm = { enabled: false, fields: {}, preset: null };

/**
 * The channel id the coupled passive-cardioid solve writes its derived output
 * to. The server refuses a submission whose own channels or combine claim it
 * (`ImportedGeometrySource.validate_passive_cardioid`), so the rail keeps it
 * out of the assignable ids instead of letting the refusal be the first the
 * user hears of it.
 */
export const PASSIVE_CARDIOID_CHANNEL_ID = 'passive_cardioid';

export type PortAreaSource = 'user' | 'bem_aperture';

export const PASSIVE_CARDIOID_NUMBER_FIELDS = [
  'rearVolumeL', 'portLengthMm', 'modelPortAreaM2', 'bemPortAreaM2', 'foamResistancePaSM3',
] as const;
export type PassiveCardioidNumberField = typeof PASSIVE_CARDIOID_NUMBER_FIELDS[number];

/**
 * The passive-cardioid campaign inputs, in the units the wire carries.
 *
 * Two things about this shape are load-bearing and neither is cosmetic.
 *
 * `rearVolumeL` is a **volume**, never a compliance: the summary's
 * `chamber_compliance_m3_per_pa` is derived from it as V/(rho c^2), so
 * offering compliance as an input would ask the user for a number the solver
 * computes.
 *
 * The two port areas are separate on purpose. `modelPortAreaM2` is
 * user-supplied and drives the chamber/port physics; `bemPortAreaM2` is the
 * geometric area of the aperture the radiation matrix was solved over.
 * Resolving both from one input is a measured ~40% error (0.397 relative on
 * the volume-velocity ratio, 0.389 on the input impedance) that still looks
 * like a plausible curve — see docs/reference/CARDIOID-INPUT-CONTRACT.md.
 */
export interface PassiveCardioidForm {
  /** Mirrors the wire's opt-in boundary: off submits no cardioid field at all. */
  enabled: boolean;
  rearVolumeL: number | null;
  portLengthMm: number | null;
  modelPortAreaM2: number | null;
  bemPortAreaM2: number | null;
  portAreaSource: PortAreaSource;
  foamResistancePaSM3: number | null;
  invertPort: boolean;
  coupled: boolean;
}

/** Server-side defaults for the two booleans, so an untouched form and a
 * missing field mean the same thing. */
export const PASSIVE_CARDIOID_DEFAULTS: PassiveCardioidForm = {
  enabled: false,
  rearVolumeL: null,
  portLengthMm: null,
  modelPortAreaM2: null,
  bemPortAreaM2: null,
  portAreaSource: 'user',
  foamResistancePaSM3: null,
  invertPort: true,
  coupled: false,
};

interface CadReturnState {
  selectedBundle: CadReturnBundle | null;
  ingestRecord: CadReturnIngestRecord | null;
  acknowledgedFindingIds: string[];
  sourceSizesMm: Record<string, number>;
  rigidSizeMm: number;
  transitionMm: number;
  skippedSourceIds: string[];
  driveChannels: CadDriveChannel[];
  areaDriftOverrides: string[];
  areaDriftSourceIds: string[];
  exteriorOnly: boolean;
  /** The user's explicit choice for the combined output, or null while they
   * have made none. Null is not "off": `combineEnabledEffective` reads it as
   * on for a multi-driver return, which is what the rail and the wire use. */
  combineEnabled: boolean | null;
  /**
   * The user's crossover, or null while they have made no change to it.
   *
   * Null is not "no crossover": `combineSpecEffective` reads it as the base
   * chain — role default frequencies, LR4, auto gain and delay — which is what
   * the rail draws and what the wire submits. One override replaced the sparse
   * frequency map plus two booleans it grew out of, because a per-channel spec
   * cannot be expressed as "one number per pair" once a pair is unlinked or a
   * single channel takes over its own gain.
   */
  combineSpec: CrossoverSpec | null;
  channelDrivers: Record<string, ChannelDriverForm>;
  passiveCardioid: PassiveCardioidForm;
  driveVoltageV: number;
  frequencyStartHz: number;
  frequencyEndHz: number;
  frequencyCount: number;
  needsIngest: boolean;
  ingestedBundleIdentity: string | null;
  ingestStaleReason: string | null;
  beginIngestIntent: () => number;
  isCurrentIngestIntent: (generation: number) => boolean;
  selectBundle: (bundle: CadReturnBundle | null) => void;
  /** Select a newly arrived return. When it correlates with the current
   * selection — same source inventory by id, role, and required flag — the
   * user's mesh sizes, channel mapping, drivers, combine, and sweep survive;
   * finding acknowledgements are reconciled after the new ingest identifies
   * which evidence is still present. `initial` means there was no current or
   * saved setup, while `reset` means an existing setup fell back to defaults. */
  selectArrivedBundle: (bundle: CadReturnBundle) => 'initial' | 'carried' | 'reset';
  refreshSelectedBundle: (bundle: CadReturnBundle | null) => void;
  markIngestStale: (reason: string) => void;
  applyIngest: (record: CadReturnIngestRecord, generation: number) => boolean;
  acknowledge: (findingId: string, value: boolean) => void;
  acknowledgeAllBlocking: () => void;
  setSourceSize: (sourceId: string, value: number) => void;
  setRigidSize: (value: number) => void;
  setTransition: (value: number) => void;
  setSkipped: (sourceId: string, skipped: boolean) => void;
  setSourceChannel: (sourceId: string, channelId: string) => void;
  setChannelMotion: (channelId: string, motion: 'normal' | 'axial') => void;
  setAreaDriftOverride: (sourceId: string, enabled: boolean) => void;
  flagAreaDrift: (sourceId: string) => void;
  setExteriorOnly: (enabled: boolean) => void;
  setCombineEnabled: (enabled: boolean) => void;
  /** Replace the whole override, or clear it back to the base chain. */
  setCombineSpec: (spec: CrossoverSpec | null) => void;
  /** Edit the current effective spec in place. The editor is handed the spec
   * the rail is showing, so a caller never has to reconstruct the base. */
  updateCombineSpec: (edit: (spec: CrossoverSpec) => CrossoverSpec) => void;
  setCombineCrossover: (pairKey: string, hz: number) => void;
  /** Adopt the crossover a recombined result was computed with, so the dock and
   * the rail hold one setting and the next solve starts where the last
   * recombine left off. A spec naming channels this return has not got is
   * ignored: a stale run must not seed the rail with foreign ids. */
  setCombineSpecFromResult: (spec: CrossoverSpec) => void;
  setChannelDriverEnabled: (channelId: string, enabled: boolean) => void;
  setChannelDriverField: (channelId: string, field: DriverFieldKey, value: number | null) => void;
  /** Pick a driver, or clear the picked one back to hand entry.
   *
   * A new driver replaces the overrides as well: they were edits of the
   * *previous* driver's numbers and applying them to this one would silently
   * publish a hybrid. `keepOverrides` is for reloading the same driver's other
   * impedance variant, where the edits are still the user's own. WG's
   * installation inputs survive either way — they describe the box, not the
   * driver. */
  setChannelDriverPreset: (channelId: string, preset: DriverPreset | null, keepOverrides?: boolean) => void;
  /** *Reset to database values*: drop the edits, keep the driver. */
  clearChannelDriverOverrides: (channelId: string) => void;
  setDriveVoltage: (value: number) => void;
  setPassiveCardioid: (patch: Partial<PassiveCardioidForm>) => void;
  setSweep: (update: Partial<Pick<CadReturnState, 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>>) => void;
}

const solveProfileStorage = namespaceStorage('cadSolveProfiles');
const acknowledgedFindingStorage = namespaceStorage('cadAcknowledgedFindings');
const SOLVE_PROFILE_STORAGE_VERSION = 3;
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
const SUPPORTED_SOLVE_PROFILE_VERSIONS = new Set([1, 2, 3]);
const ACKNOWLEDGED_FINDING_STORAGE_VERSION = 1;
const MAX_SOLVE_PROFILES = 20;
const MAX_ACKNOWLEDGED_FINDING_SETS = 20;
const MAX_FINDING_IDS_PER_SET = 1_000;

type PersistedSolveSettings = Pick<CadReturnState,
  'sourceSizesMm' | 'rigidSizeMm' | 'transitionMm' | 'skippedSourceIds' | 'driveChannels'
  | 'exteriorOnly' | 'combineEnabled' | 'combineSpec'
  | 'channelDrivers' | 'passiveCardioid' | 'driveVoltageV'
  | 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>;

interface SourceInventoryEntry {
  id: string;
  role: string;
  required: boolean;
}

interface StoredSolveProfile {
  key: string;
  designId: string;
  lineageId: string;
  inventory: SourceInventoryEntry[];
  settings: PersistedSolveSettings;
}

interface StoredAcknowledgedFindingSet {
  key: string;
  identity: Pick<DesignIdentity, 'designId' | 'lineageId'> | null;
  documentName: string | null;
  inventory: SourceInventoryEntry[];
  findingIds: string[];
}

let selectedSolveProfileKey: string | null = null;

function initialFromBundle(bundle: CadReturnBundle | null) {
  const sources = bundle?.readable ? bundle.sources : [];
  const sourceSizesMm = Object.fromEntries(sources.map((source) => [source.id, source.suggestedResolutionMm]));
  const suggestions = sources.map((source) => source.suggestedResolutionMm).filter((value) => value > 0);
  const coarsest = suggestions.length ? Math.max(...suggestions) : 1;
  return {
    sourceSizesMm,
    rigidSizeMm: coarsest,
    transitionMm: coarsest,
    skippedSourceIds: [] as string[],
    driveChannels: groupChannels(sources.map((source) => ({ sourceId: source.id, channelId: source.defaultDriveChannelId, motion: 'normal' as const }))),
    areaDriftOverrides: [] as string[],
  };
}

function bundleIdentity(bundle: CadReturnBundle): string {
  return JSON.stringify({
    name: bundle.name,
    bundlePath: bundle.bundlePath,
    modifiedAt: bundle.modifiedAt,
    readable: bundle.readable,
    documentName: bundle.documentName,
    sourceCount: bundle.sourceCount,
    instanceCount: bundle.instanceCount,
    sources: bundle.sources,
  });
}

function bundleChangeReason(previous: CadReturnBundle, current: CadReturnBundle | null): string | null {
  if (!current) return 'The ingested return no longer appears in the workspace listing.';
  if (bundleIdentity(previous) === bundleIdentity(current)) return null;
  if (previous.readable !== current.readable) return current.readable
    ? 'The return became readable after it was ingested.'
    : `The return is no longer readable${current.reason ? `: ${current.reason}` : '.'}`;
  if (JSON.stringify(previous.sources) !== JSON.stringify(current.sources)
    || previous.sourceCount !== current.sourceCount) {
    return 'The return source inventory or source sizing suggestions changed after ingestion.';
  }
  if (previous.documentName !== current.documentName || previous.instanceCount !== current.instanceCount) {
    return 'The return document or linked-instance inventory changed after ingestion.';
  }
  return 'The return bundle was modified after this ingestion.';
}

interface SourceInventoryCarrier {
  readable: boolean;
  sources: readonly SourceInventoryEntry[];
}

function sourceInventorySignature(bundle: SourceInventoryCarrier): string {
  return JSON.stringify(
    bundle.sources
      .map(({ id, role, required }) => ({ id, role, required }))
      .sort((a, b) => a.id.localeCompare(b.id)
        || a.role.localeCompare(b.role)
        || Number(a.required) - Number(b.required)),
  );
}

/** Two returns are solve-compatible when they expose the same sources with the
 * same acoustic roles. Sizing suggestions may differ — the user's sizes win. */
function compatibleSourceInventory(previous: SourceInventoryCarrier, next: SourceInventoryCarrier): boolean {
  return previous.readable && next.readable
    && sourceInventorySignature(previous) === sourceInventorySignature(next);
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
    return [{ id: item.id, source_ids: ids, motion: item.motion }];
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
  const { id, label, source, kind, z_ohm: z } = value;
  if (typeof id !== 'string' || !id || typeof label !== 'string' || !label) return undefined;
  if (source !== 'database' && source !== 'mine' && source !== 'manual') return undefined;
  if (kind !== 'lf' && kind !== 'cd' && kind !== 'unknown') return undefined;
  if (z !== null && (typeof z !== 'number' || !Number.isFinite(z))) return undefined;
  const base = parseDriverFields(value.base);
  if (!base) return undefined;
  return { id, label, source, kind, z_ohm: z, base };
}

function parseChannelDrivers(value: unknown): Record<string, ChannelDriverForm> | null {
  if (!isObject(value)) return null;
  const drivers: Record<string, ChannelDriverForm> = {};
  for (const [channelId, item] of Object.entries(value)) {
    if (!channelId || !isObject(item) || typeof item.enabled !== 'boolean') return null;
    const fields = parseDriverFields(item.fields);
    const preset = parseDriverPreset(item.preset);
    if (!fields || preset === undefined) return null;
    drivers[channelId] = { enabled: item.enabled, fields, preset };
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

function parseSolveSettings(
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
    combineEnabled: value.combineEnabled,
    channelDrivers,
    passiveCardioid,
    driveVoltageV: value.driveVoltageV,
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

function solveProfileKey(identity: Pick<DesignIdentity, 'designId' | 'lineageId'>, inventory: SourceInventoryCarrier): string {
  return JSON.stringify([identity.designId, identity.lineageId, sourceInventorySignature(inventory)]);
}

function unlinkedAcknowledgementKey(documentName: string, inventory: SourceInventoryCarrier): string {
  return JSON.stringify([documentName, sourceInventorySignature(inventory)]);
}

function dropStoredSolveProfiles(): void {
  try { solveProfileStorage.removeItem('cadSolveProfiles'); } catch { /* persistence is best effort */ }
}

function readStoredSolveProfiles(): StoredSolveProfile[] {
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
    const profiles: StoredSolveProfile[] = [];
    for (const value of parsed.profiles) {
      if (!isObject(value) || typeof value.key !== 'string'
        || typeof value.designId !== 'string' || !value.designId
        || typeof value.lineageId !== 'string' || !value.lineageId) {
        dropStoredSolveProfiles();
        return [];
      }
      const inventory = parseInventory(value.inventory);
      const settings = inventory ? parseSolveSettings(value.settings, inventory, version) : null;
      if (!inventory || !settings) {
        dropStoredSolveProfiles();
        return [];
      }
      const identity = { designId: value.designId, lineageId: value.lineageId };
      if (value.key !== solveProfileKey(identity, { readable: true, sources: inventory })) {
        dropStoredSolveProfiles();
        return [];
      }
      profiles.push({
        key: value.key,
        ...identity,
        inventory,
        settings: legacyCombineChoice && settings.combineEnabled === false
          ? { ...settings, combineEnabled: null }
          : settings,
      });
    }
    if (new Set(profiles.map(({ key }) => key)).size !== profiles.length) {
      dropStoredSolveProfiles();
      return [];
    }
    return profiles;
  } catch {
    dropStoredSolveProfiles();
    return [];
  }
}

function writeStoredSolveProfiles(profiles: StoredSolveProfile[]): void {
  try {
    solveProfileStorage.setItem('cadSolveProfiles', JSON.stringify({
      version: SOLVE_PROFILE_STORAGE_VERSION,
      profiles: profiles.slice(0, MAX_SOLVE_PROFILES),
    }));
  } catch { /* persistence is best effort */ }
}

function currentSolveProfileKey(bundle: CadReturnBundle): string | null {
  const identity = useDocumentStore.getState().identity;
  return identity?.designId && identity.lineageId ? solveProfileKey(identity, bundle) : null;
}

function hasSolveProfileForCurrentDesign(): boolean {
  const identity = useDocumentStore.getState().identity;
  if (!identity?.designId || !identity.lineageId) return false;
  return readStoredSolveProfiles().some((profile) => (
    profile.designId === identity.designId && profile.lineageId === identity.lineageId
  ));
}

function persistedSolveSettings(state: CadReturnState): PersistedSolveSettings {
  return {
    sourceSizesMm: { ...state.sourceSizesMm },
    rigidSizeMm: state.rigidSizeMm,
    transitionMm: state.transitionMm,
    skippedSourceIds: [...state.skippedSourceIds],
    driveChannels: state.driveChannels.map((channel) => ({ ...channel, source_ids: [...channel.source_ids] })),
    exteriorOnly: state.exteriorOnly,
    combineEnabled: state.combineEnabled,
    combineSpec: state.combineSpec ? cloneSpec(state.combineSpec) : null,
    channelDrivers: Object.fromEntries(Object.entries(state.channelDrivers).map(([channelId, form]) => [
      channelId,
      {
        enabled: form.enabled,
        fields: { ...form.fields },
        preset: form.preset ? { ...form.preset, base: { ...form.preset.base } } : null,
      },
    ])),
    passiveCardioid: { ...state.passiveCardioid },
    driveVoltageV: state.driveVoltageV,
    frequencyStartHz: state.frequencyStartHz,
    frequencyEndHz: state.frequencyEndHz,
    frequencyCount: state.frequencyCount,
  };
}

function saveSolveProfile(state: CadReturnState): void {
  const bundle = state.selectedBundle;
  const identity = useDocumentStore.getState().identity;
  if (!bundle?.readable || !identity?.designId || !identity.lineageId) return;
  const inventory = bundle.sources.map(({ id, role, required }) => ({ id, role, required }));
  const key = solveProfileKey(identity, bundle);
  const profile: StoredSolveProfile = {
    key,
    designId: identity.designId,
    lineageId: identity.lineageId,
    inventory,
    settings: persistedSolveSettings(state),
  };
  writeStoredSolveProfiles([profile, ...readStoredSolveProfiles().filter((item) => item.key !== key)]);
  selectedSolveProfileKey = key;
}

function restoreSolveProfile(bundle: CadReturnBundle): PersistedSolveSettings | null {
  const key = currentSolveProfileKey(bundle);
  if (!key) return null;
  const profiles = readStoredSolveProfiles();
  const index = profiles.findIndex((profile) => profile.key === key);
  if (index < 0) return null;
  const profile = profiles[index];
  if (!compatibleSourceInventory({ readable: true, sources: profile.inventory }, bundle)) return null;
  if (index > 0) writeStoredSolveProfiles([profile, ...profiles.filter((_, itemIndex) => itemIndex !== index)]);
  return profile.settings;
}

function dropStoredAcknowledgedFindings(): void {
  try { acknowledgedFindingStorage.removeItem('cadAcknowledgedFindings'); } catch { /* persistence is best effort */ }
}

function readStoredAcknowledgedFindings(): StoredAcknowledgedFindingSet[] {
  try {
    const raw = acknowledgedFindingStorage.getItem('cadAcknowledgedFindings');
    if (raw === null) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!isObject(parsed) || parsed.version !== ACKNOWLEDGED_FINDING_STORAGE_VERSION
      || !Array.isArray(parsed.entries) || parsed.entries.length > MAX_ACKNOWLEDGED_FINDING_SETS) {
      dropStoredAcknowledgedFindings();
      return [];
    }
    const entries: StoredAcknowledgedFindingSet[] = [];
    for (const value of parsed.entries) {
      if (!isObject(value) || typeof value.key !== 'string'
        || (value.documentName !== null && typeof value.documentName !== 'string')) {
        dropStoredAcknowledgedFindings();
        return [];
      }
      const inventory = parseInventory(value.inventory);
      const findingIds = stringArray(value.findingIds);
      if (!inventory || !findingIds || findingIds.length > MAX_FINDING_IDS_PER_SET
        || findingIds.some((id) => !id)
        || new Set(findingIds).size !== findingIds.length) {
        dropStoredAcknowledgedFindings();
        return [];
      }
      let identity: Pick<DesignIdentity, 'designId' | 'lineageId'> | null = null;
      let expectedKey: string;
      if (value.identity === null) {
        if (typeof value.documentName !== 'string' || !value.documentName.trim()) {
          dropStoredAcknowledgedFindings();
          return [];
        }
        expectedKey = unlinkedAcknowledgementKey(value.documentName, { readable: true, sources: inventory });
      } else {
        if (!isObject(value.identity)
          || typeof value.identity.designId !== 'string' || !value.identity.designId
          || typeof value.identity.lineageId !== 'string' || !value.identity.lineageId
          || value.documentName !== null) {
          dropStoredAcknowledgedFindings();
          return [];
        }
        identity = { designId: value.identity.designId, lineageId: value.identity.lineageId };
        expectedKey = solveProfileKey(identity, { readable: true, sources: inventory });
      }
      if (value.key !== expectedKey) {
        dropStoredAcknowledgedFindings();
        return [];
      }
      entries.push({ key: value.key, identity, documentName: value.documentName, inventory, findingIds });
    }
    if (new Set(entries.map(({ key }) => key)).size !== entries.length) {
      dropStoredAcknowledgedFindings();
      return [];
    }
    return entries;
  } catch {
    dropStoredAcknowledgedFindings();
    return [];
  }
}

function writeStoredAcknowledgedFindings(entries: StoredAcknowledgedFindingSet[]): void {
  try {
    acknowledgedFindingStorage.setItem('cadAcknowledgedFindings', JSON.stringify({
      version: ACKNOWLEDGED_FINDING_STORAGE_VERSION,
      entries: entries.slice(0, MAX_ACKNOWLEDGED_FINDING_SETS),
    }));
  } catch { /* persistence is best effort */ }
}

function currentAcknowledgedFindingSet(
  bundle: CadReturnBundle,
  record: CadReturnIngestRecord,
): Omit<StoredAcknowledgedFindingSet, 'findingIds'> | null {
  const inventory = bundle.sources.map(({ id, role, required }) => ({ id, role, required }));
  if (record.freshness.verdict === 'unlinked') {
    const documentName = bundle.documentName?.trim();
    if (!documentName) return null;
    return {
      key: unlinkedAcknowledgementKey(documentName, bundle),
      identity: null,
      documentName,
      inventory,
    };
  }
  const identity = useDocumentStore.getState().identity;
  if (!identity?.designId || !identity.lineageId) return null;
  const storedIdentity = { designId: identity.designId, lineageId: identity.lineageId };
  return {
    key: solveProfileKey(storedIdentity, bundle),
    identity: storedIdentity,
    documentName: null,
    inventory,
  };
}

function restoreAcknowledgedFindings(bundle: CadReturnBundle, record: CadReturnIngestRecord): string[] {
  const current = currentAcknowledgedFindingSet(bundle, record);
  if (!current) return [];
  const entries = readStoredAcknowledgedFindings();
  const index = entries.findIndex((entry) => entry.key === current.key);
  if (index < 0) return [];
  const entry = entries[index];
  if (!compatibleSourceInventory({ readable: true, sources: entry.inventory }, bundle)) return [];
  if (index > 0) writeStoredAcknowledgedFindings([entry, ...entries.filter((_, entryIndex) => entryIndex !== index)]);
  const presentFindingIds = new Set(record.findings.map((finding) => finding.id));
  return entry.findingIds.filter((findingId) => presentFindingIds.has(findingId));
}

function saveAcknowledgedFindings(state: CadReturnState): void {
  const bundle = state.selectedBundle;
  const record = state.ingestRecord;
  if (!bundle?.readable || !record) return;
  const current = currentAcknowledgedFindingSet(bundle, record);
  if (!current) return;
  const presentFindingIds = new Set(record.findings.map((finding) => finding.id));
  const entry: StoredAcknowledgedFindingSet = {
    ...current,
    findingIds: state.acknowledgedFindingIds.filter((findingId) => presentFindingIds.has(findingId)),
  };
  writeStoredAcknowledgedFindings([
    entry,
    ...readStoredAcknowledgedFindings().filter((item) => item.key !== entry.key),
  ]);
}

// This token deliberately lives outside Zustand state: advancing intent must
// reject a late network result without manufacturing a render by itself. Every
// input that changes the bytes an ingest represents advances the same token.
let ingestIntentGeneration = 0;

function supersedeIngestIntent(): number {
  ingestIntentGeneration += 1;
  return ingestIntentGeneration;
}

function reconcileListing(state: CadReturnState, selectedBundle: CadReturnBundle) {
  const ids = new Set(selectedBundle.sources.map((source) => source.id));
  const sourceSizesMm = Object.fromEntries(selectedBundle.sources.map((source) => [
    source.id,
    state.sourceSizesMm[source.id] ?? source.suggestedResolutionMm,
  ]));
  const skippedSourceIds = state.skippedSourceIds.filter((id) => ids.has(id));
  const areaDriftOverrides = state.areaDriftOverrides.filter((id) => ids.has(id));
  const rows = selectedBundle.sources
    .filter((source) => !skippedSourceIds.includes(source.id))
    .map((source) => {
      const existing = state.driveChannels.find((channel) => channel.source_ids.includes(source.id));
      return {
        sourceId: source.id,
        channelId: existing?.id ?? source.defaultDriveChannelId,
        motion: existing?.motion ?? 'normal' as const,
      };
    });
  const driveChannels = groupChannels(rows);
  return {
    selectedBundle,
    sourceSizesMm,
    skippedSourceIds,
    areaDriftOverrides,
    driveChannels,
    channelDrivers: retainedChannelDrivers(state, driveChannels),
  };
}

/**
 * Whether a channel can carry a driver model at all.
 *
 * The server refuses one on an axial or multi-source channel -- see
 * `DriveChannel.validate_driver_applicability` -- because the radiating area
 * and surface pressure belong to exactly one source patch. `CadDriveChannels`
 * hides the driver controls by the same rule.
 */
export function channelAcceptsDriver(channel: CadDriveChannel): boolean {
  return channel.source_ids.length === 1 && channel.motion === 'normal';
}

/**
 * Drop driver forms whose channel can no longer carry them.
 *
 * A completed form used to survive the channel changing underneath it. The
 * submission builder serializes drivers by channel id alone, so a channel that
 * had since become axial or multi-source was still submitted with one and the
 * server rejected the entire solve. The quieter failure is worse: a channel id
 * is reusable, and reassigning sources can rebuild the same id around a
 * *different* source, at which point the old driver would have been applied to
 * the wrong radiator without a word. Keeping a form therefore requires the
 * channel to still exist, still accept a driver, and still hold exactly the
 * source it was filled in for.
 */
function retainedChannelDrivers(
  state: Pick<CadReturnState, 'channelDrivers' | 'driveChannels'>,
  nextChannels: CadDriveChannel[],
): Record<string, ChannelDriverForm> {
  const before = new Map(state.driveChannels.map((channel) => [channel.id, channel.source_ids.join(' ')]));
  return Object.fromEntries(Object.entries(state.channelDrivers).filter(([id]) => {
    const next = nextChannels.find((channel) => channel.id === id);
    if (!next || !channelAcceptsDriver(next)) return false;
    const previousMembers = before.get(id);
    return previousMembers === undefined || previousMembers === next.source_ids.join(' ');
  }));
}

function groupChannels(sourceChannels: Array<{ sourceId: string; channelId: string; motion: 'normal' | 'axial' }>): CadDriveChannel[] {
  const grouped = new Map<string, CadDriveChannel>();
  sourceChannels.forEach(({ sourceId, channelId, motion }) => {
    const id = channelId.trim();
    if (!id) return;
    const channel = grouped.get(id) ?? { id, source_ids: [], motion };
    channel.source_ids.push(sourceId);
    grouped.set(id, channel);
  });
  return [...grouped.values()];
}

export const useCadReturnStore = create<CadReturnState>((set, get) => ({
  selectedBundle: null,
  ingestRecord: null,
  acknowledgedFindingIds: [],
  ...initialFromBundle(null),
  areaDriftSourceIds: [],
  exteriorOnly: false,
  combineEnabled: null,
  combineSpec: null,
  channelDrivers: {},
  passiveCardioid: { ...PASSIVE_CARDIOID_DEFAULTS },
  driveVoltageV: 2.83,
  frequencyStartHz: 200,
  frequencyEndHz: 20_000,
  frequencyCount: 24,
  needsIngest: true,
  ingestedBundleIdentity: null,
  ingestStaleReason: null,
  beginIngestIntent: supersedeIngestIntent,
  isCurrentIngestIntent: (generation) => generation === ingestIntentGeneration,
  selectBundle: (selectedBundle) => {
    supersedeIngestIntent();
    const restored = selectedBundle ? restoreSolveProfile(selectedBundle) : null;
    selectedSolveProfileKey = selectedBundle ? currentSolveProfileKey(selectedBundle) : null;
    set({
      selectedBundle,
      ...initialFromBundle(selectedBundle),
      exteriorOnly: false,
      combineEnabled: null,
      combineSpec: null,
      channelDrivers: {},
      passiveCardioid: { ...PASSIVE_CARDIOID_DEFAULTS },
      driveVoltageV: 2.83,
      frequencyStartHz: 200,
      frequencyEndHz: 20_000,
      frequencyCount: 24,
      ...(restored ?? {}),
      ingestRecord: null,
      acknowledgedFindingIds: [],
      areaDriftOverrides: [],
      areaDriftSourceIds: [],
      needsIngest: true,
      ingestedBundleIdentity: null,
      ingestStaleReason: null,
    });
  },
  selectArrivedBundle: (bundle) => {
    const previous = get().selectedBundle;
    const nextProfileKey = currentSolveProfileKey(bundle);
    if (!previous || !compatibleSourceInventory(previous, bundle)
      || selectedSolveProfileKey !== nextProfileKey) {
      supersedeIngestIntent();
      const restored = restoreSolveProfile(bundle);
      selectedSolveProfileKey = nextProfileKey;
      set({
        selectedBundle: bundle,
        ...initialFromBundle(bundle),
        exteriorOnly: false,
        combineEnabled: null,
        combineSpec: null,
        channelDrivers: {},
        passiveCardioid: { ...PASSIVE_CARDIOID_DEFAULTS },
        driveVoltageV: 2.83,
        frequencyStartHz: 200,
        frequencyEndHz: 20_000,
        frequencyCount: 24,
        ...(restored ?? {}),
        ingestRecord: null,
        acknowledgedFindingIds: [],
        areaDriftOverrides: [],
        areaDriftSourceIds: [],
        needsIngest: true,
        ingestedBundleIdentity: null,
        ingestStaleReason: null,
      });
      if (restored) return 'carried';
      return previous || hasSolveProfileForCurrentDesign() ? 'reset' : 'initial';
    }
    supersedeIngestIntent();
    set((state) => ({
      ...reconcileListing(state, bundle),
      // The new geometry needs its own ingest before stable finding ids can be
      // reconciled with the evidence the user has already acknowledged.
      ingestRecord: null,
      acknowledgedFindingIds: [],
      areaDriftOverrides: [],
      areaDriftSourceIds: [],
      needsIngest: true,
      ingestedBundleIdentity: null,
      ingestStaleReason: null,
    }));
    return 'carried';
  },
  refreshSelectedBundle: (selectedBundle) => {
    const previous = get().selectedBundle;
    // A listing revision can replace the geometry under the same path. Advance
    // intent even before any ingest record exists, or an older request could
    // later attach itself to the revised evidence as though it described it.
    if (previous && (!selectedBundle || bundleIdentity(previous) !== bundleIdentity(selectedBundle))) {
      supersedeIngestIntent();
    }
    set((state) => {
      const previous = state.selectedBundle;
      if (!previous) return {};
      const differsFromIngest = state.ingestRecord && (
        !selectedBundle || state.ingestedBundleIdentity !== bundleIdentity(selectedBundle)
      );
      const reason = differsFromIngest
        ? bundleChangeReason(previous, selectedBundle) ?? state.ingestStaleReason ?? 'The return listing differs from the bundle used for this ingestion.'
        : null;
      if (!selectedBundle) return reason ? { needsIngest: true, ingestStaleReason: reason } : {};
      return {
        ...reconcileListing(state, selectedBundle),
        ...(reason ? { needsIngest: true, ingestStaleReason: reason } : {}),
      };
    });
  },
  markIngestStale: (reason) => {
    // Design replacement must supersede an in-flight ingest even when there is
    // no completed record yet, which is why invalidation is outside the guard.
    supersedeIngestIntent();
    set((state) => (
      state.ingestRecord ? { needsIngest: true, ingestStaleReason: reason } : {}
    ));
  },
  applyIngest: (ingestRecord, generation) => {
    if (generation !== ingestIntentGeneration) return false;
    const skipped = new Set(ingestRecord.skipped_source_ids);
    const current = get();
    const acknowledgedFindingIds = current.selectedBundle
      ? restoreAcknowledgedFindings(current.selectedBundle, ingestRecord)
      : [];
    const channels = current.driveChannels.flatMap((channel) => {
      const source_ids = channel.source_ids.filter((id) => !skipped.has(id));
      return source_ids.length ? [{ ...channel, source_ids }] : [];
    });
    set({
      ingestRecord,
      acknowledgedFindingIds,
      sourceSizesMm: { ...ingestRecord.mesh_sizes.source_size_mm },
      rigidSizeMm: ingestRecord.mesh_sizes.rigid_size_mm,
      transitionMm: ingestRecord.mesh_sizes.transition_mm,
      skippedSourceIds: [...ingestRecord.skipped_source_ids],
      driveChannels: channels,
      areaDriftSourceIds: [...new Set([
        ...current.areaDriftSourceIds,
        ...(ingestRecord.role_findings ?? []).filter((finding) => String(finding.kind).includes('area-drift')).map((finding) => String(finding.source_id)),
      ])],
      needsIngest: false,
      ingestedBundleIdentity: current.selectedBundle ? bundleIdentity(current.selectedBundle) : null,
      ingestStaleReason: null,
    });
    saveSolveProfile(get());
    return true;
  },
  acknowledge: (findingId, value) => {
    set((state) => ({
      acknowledgedFindingIds: value
        ? [...new Set([...state.acknowledgedFindingIds, findingId])]
        : state.acknowledgedFindingIds.filter((id) => id !== findingId),
    }));
    saveAcknowledgedFindings(get());
  },
  acknowledgeAllBlocking: () => {
    set((state) => ({
      acknowledgedFindingIds: state.ingestRecord?.findings.filter((finding) => finding.blocking).map((finding) => finding.id) ?? [],
    }));
    saveAcknowledgedFindings(get());
  },
  setSourceSize: (sourceId, value) => {
    supersedeIngestIntent();
    set((state) => ({ sourceSizesMm: { ...state.sourceSizesMm, [sourceId]: value }, needsIngest: true }));
    saveSolveProfile(get());
  },
  setRigidSize: (rigidSizeMm) => {
    supersedeIngestIntent();
    set({ rigidSizeMm, needsIngest: true });
    saveSolveProfile(get());
  },
  setTransition: (transitionMm) => {
    supersedeIngestIntent();
    set({ transitionMm, needsIngest: true });
    saveSolveProfile(get());
  },
  setSkipped: (sourceId, skipped) => {
    supersedeIngestIntent();
    set((state) => {
      const skippedSourceIds = skipped
        ? [...new Set([...state.skippedSourceIds, sourceId])]
        : state.skippedSourceIds.filter((id) => id !== sourceId);
      let driveChannels = state.driveChannels.map((channel) => ({ ...channel, source_ids: channel.source_ids.filter((id) => id !== sourceId) })).filter((channel) => channel.source_ids.length);
      if (!skipped && !driveChannels.some((channel) => channel.source_ids.includes(sourceId))) {
        const source = state.selectedBundle?.sources.find((item) => item.id === sourceId);
        if (source) driveChannels = [...driveChannels, { id: source.defaultDriveChannelId, source_ids: [sourceId], motion: 'normal' }];
      }
      const source = state.selectedBundle?.sources.find((item) => item.id === sourceId);
      const sourceSizesMm = !skipped && source && state.sourceSizesMm[sourceId] === undefined
        ? { ...state.sourceSizesMm, [sourceId]: source.suggestedResolutionMm }
        : state.sourceSizesMm;
      return {
        skippedSourceIds,
        driveChannels,
        sourceSizesMm,
        channelDrivers: retainedChannelDrivers(state, driveChannels),
        needsIngest: true,
      };
    });
    saveSolveProfile(get());
  },
  setSourceChannel: (sourceId, channelId) => {
    set((state) => {
      const activeIds = (state.selectedBundle?.sources ?? []).map((source) => source.id).filter((id) => !state.skippedSourceIds.includes(id));
      const rows = activeIds.map((id) => {
        const existing = state.driveChannels.find((channel) => channel.source_ids.includes(id));
        return { sourceId: id, channelId: id === sourceId ? channelId : existing?.id ?? id, motion: existing?.motion ?? 'normal' as const };
      });
      const driveChannels = groupChannels(rows);
      return { driveChannels, channelDrivers: retainedChannelDrivers(state, driveChannels) };
    });
    saveSolveProfile(get());
  },
  setChannelMotion: (channelId, motion) => {
    set((state) => {
      const driveChannels = state.driveChannels.map((channel) => channel.id === channelId ? { ...channel, motion } : channel);
      return { driveChannels, channelDrivers: retainedChannelDrivers(state, driveChannels) };
    });
    saveSolveProfile(get());
  },
  setAreaDriftOverride: (sourceId, enabled) => {
    supersedeIngestIntent();
    set((state) => ({
      areaDriftOverrides: enabled
        ? [...new Set([...state.areaDriftOverrides, sourceId])]
        : state.areaDriftOverrides.filter((id) => id !== sourceId),
      needsIngest: true,
    }));
  },
  flagAreaDrift: (sourceId) => set((state) => ({ areaDriftSourceIds: [...new Set([...state.areaDriftSourceIds, sourceId])] })),
  setExteriorOnly: (exteriorOnly) => { set({ exteriorOnly }); saveSolveProfile(get()); },
  setCombineEnabled: (combineEnabled) => { set({ combineEnabled }); saveSolveProfile(get()); },
  setCombineSpec: (combineSpec) => { set({ combineSpec }); saveSolveProfile(get()); },
  updateCombineSpec: (edit) => {
    set((state) => {
      const current = combineSpecEffective(state);
      return current ? { combineSpec: edit(current) } : {};
    });
    saveSolveProfile(get());
  },
  setCombineCrossover: (pairKey, hz) => {
    useCadReturnStore.getState().updateCombineSpec((spec) => withPair(spec, pairKey, { hz }));
  },
  setCombineSpecFromResult: (spec) => {
    set((state) => {
      const known = new Set(state.driveChannels.map((channel) => channel.id));
      return spec.members.length >= 2 && spec.members.every((member) => known.has(member))
        ? { combineSpec: cloneSpec(spec) }
        : {};
    });
    saveSolveProfile(get());
  },
  setChannelDriverEnabled: (channelId, enabled) => {
    set((state) => {
      const current = state.channelDrivers[channelId] ?? EMPTY_DRIVER_FORM;
      return { channelDrivers: { ...state.channelDrivers, [channelId]: { ...current, enabled } } };
    });
    saveSolveProfile(get());
  },
  setChannelDriverField: (channelId, field, value) => {
    set((state) => {
      const current = state.channelDrivers[channelId] ?? { ...EMPTY_DRIVER_FORM, enabled: true };
      const fields = { ...current.fields };
      // An override equal to the base value is not an edit; storing it would
      // outline an untouched field and count it in the header.
      if (value === null || !Number.isFinite(value) || value === current.preset?.base[field]) delete fields[field];
      else fields[field] = value;
      return { channelDrivers: { ...state.channelDrivers, [channelId]: { ...current, fields } } };
    });
    saveSolveProfile(get());
  },
  setChannelDriverPreset: (channelId, preset, keepOverrides = false) => {
    set((state) => {
      const current = state.channelDrivers[channelId] ?? { ...EMPTY_DRIVER_FORM, enabled: true };
      const kept = keepOverrides ? { ...current.fields } : retainedInstallationFields(current.fields);
      return {
        channelDrivers: {
          ...state.channelDrivers,
          [channelId]: { ...current, preset, fields: kept },
        },
      };
    });
    saveSolveProfile(get());
  },
  clearChannelDriverOverrides: (channelId) => {
    set((state) => {
      const current = state.channelDrivers[channelId];
      if (!current) return {};
      return {
        channelDrivers: {
          ...state.channelDrivers,
          [channelId]: { ...current, fields: retainedInstallationFields(current.fields) },
        },
      };
    });
    saveSolveProfile(get());
  },
  setDriveVoltage: (driveVoltageV) => { set({ driveVoltageV }); saveSolveProfile(get()); },
  setPassiveCardioid: (patch) => {
    set((state) => ({ passiveCardioid: normalizePassiveCardioid({ ...state.passiveCardioid, ...patch }) }));
    saveSolveProfile(get());
  },
  setSweep: (update) => { set(update); saveSolveProfile(get()); },
}));

/** Only WG's own installation inputs survive a driver change or a reset. */
function retainedInstallationFields(
  fields: Partial<Record<DriverFieldKey, number>>,
): Partial<Record<DriverFieldKey, number>> {
  return Object.fromEntries(
    Object.entries(fields).filter(([key]) => DRIVER_INSTALLATION_KEYS.includes(key as DriverFieldKey)),
  );
}

/** The driver as it will be submitted: the preset's numbers under the edits. */
export function driverValues(form: ChannelDriverForm | undefined): Partial<Record<DriverFieldKey, number>> {
  if (!form) return {};
  return { ...(form.preset?.base ?? {}), ...form.fields };
}

/** Which fields the user has changed away from the preset. Installation inputs
 * are excluded: they were never the driver's to state. */
export function driverEditedKeys(form: ChannelDriverForm | undefined): DriverFieldKey[] {
  if (!form?.preset) return [];
  return DRIVER_FIELD_KEYS.filter((key) => (
    !DRIVER_INSTALLATION_KEYS.includes(key)
    && form.fields[key] !== undefined
    && form.fields[key] !== form.preset?.base[key]
  ));
}

/**
 * Which requirement groups are still unsatisfied, mirroring the server's
 * `DriverSpec.validate_completeness` rather than a flat list of keys: one mass
 * and one compliance source are enough, and which one is the user's choice.
 */
export function driverMissingGroups(form: ChannelDriverForm | undefined): DriverFieldKey[][] {
  const values = driverValues(form);
  const groups: DriverFieldKey[][] = [];
  for (const key of DRIVER_REQUIRED_KEYS) if (values[key] === undefined) groups.push([key]);
  if (DRIVER_MASS_KEYS.every((key) => values[key] === undefined)) groups.push([...DRIVER_MASS_KEYS]);
  if (DRIVER_COMPLIANCE_KEYS.every((key) => values[key] === undefined)) groups.push([...DRIVER_COMPLIANCE_KEYS]);
  return groups;
}

/**
 * The wire driver spec for one channel, or undefined while incomplete.
 *
 * Exactly one mass reaches the server, because a spec carrying both is a
 * refusal of the whole solve. Mms wins when both are known: it is what a
 * datasheet and the driver library publish, and the solver converts it to Mmd
 * itself by subtracting the free-air radiation mass.
 */
export function channelDriverWire(
  form: ChannelDriverForm | undefined,
): Record<string, number | string> | undefined {
  if (!form?.enabled) return undefined;
  const values = driverValues(form);
  if (driverMissingGroups(form).length) return undefined;
  const mass: DriverFieldKey = values.mms_g !== undefined ? 'mms_g' : 'mmd_g';
  const wire: Record<string, number | string> = {};
  for (const key of DRIVER_FIELD_KEYS) {
    if (DRIVER_MASS_KEYS.includes(key) && key !== mass) continue;
    const value = values[key];
    if (value !== undefined) wire[key] = value;
  }
  if (form.preset?.label) wire.label = form.preset.label;
  return wire;
}

export const PASSIVE_CARDIOID_FIELD_LABELS: Record<PassiveCardioidNumberField, string> = {
  rearVolumeL: 'Rear volume',
  portLengthMm: 'Port length',
  modelPortAreaM2: 'Physical port area',
  bemPortAreaM2: 'BEM port area',
  foamResistancePaSM3: 'Foam resistance',
};

/**
 * Hold the two port areas together when the user says they are the same face.
 *
 * `port_area_source: 'bem_aperture'` asserts that the area driving the physics
 * *is* the aperture the radiation matrix was solved over, and the server
 * enforces that with `math.isclose(..., rel_tol=1e-12)`. Two independently
 * typed numbers cannot survive that, so the provenance choice drives the model
 * area from the BEM area rather than asking the user to retype it. Switching
 * back to `user` leaves the value alone: it is now theirs to change.
 */
export function normalizePassiveCardioid(form: PassiveCardioidForm): PassiveCardioidForm {
  return form.portAreaSource === 'bem_aperture' && form.modelPortAreaM2 !== form.bemPortAreaM2
    ? { ...form, modelPortAreaM2: form.bemPortAreaM2 }
    : form;
}

/** Which required cardioid inputs are still missing or outside the server's
 * bounds. Empty means the whole set can be submitted together. */
export function passiveCardioidMissingFields(form: PassiveCardioidForm): PassiveCardioidNumberField[] {
  const positive = (value: number | null): boolean => value !== null && Number.isFinite(value) && value > 0;
  const nonNegative = (value: number | null): boolean => value !== null && Number.isFinite(value) && value >= 0;
  return PASSIVE_CARDIOID_NUMBER_FIELDS.filter((field) => (
    field === 'portLengthMm' || field === 'foamResistancePaSM3'
      ? !nonNegative(form[field])
      : !positive(form[field])
  ));
}

export interface PassiveCardioidWire {
  passive_cardioid_rear_volume_l: number;
  passive_cardioid_port_length_mm: number;
  model_port_area_m2: number;
  bem_port_area_m2: number;
  port_area_source: PortAreaSource;
  passive_cardioid_foam_resistance_pa_s_m3: number;
  passive_cardioid_invert_port: boolean;
  passive_cardioid_coupled: boolean;
}

/**
 * The whole cardioid field set, or null.
 *
 * All-or-nothing is the wire's own rule, not a convenience: a missing
 * `passive_cardioid_rear_volume_l` puts the job on the exact pre-campaign
 * solve path, and any other cardioid field sent without it — `coupled: true`
 * and `invert_port: false` included — is a hard refusal naming the strays. So
 * a disabled form contributes no keys whatsoever, and an incomplete one is
 * caught by `passiveCardioidBlocker` before anything is built.
 */
export function passiveCardioidWire(form: PassiveCardioidForm): PassiveCardioidWire | null {
  if (!form.enabled || passiveCardioidMissingFields(form).length) return null;
  const normalized = normalizePassiveCardioid(form);
  return {
    passive_cardioid_rear_volume_l: normalized.rearVolumeL as number,
    passive_cardioid_port_length_mm: normalized.portLengthMm as number,
    model_port_area_m2: normalized.modelPortAreaM2 as number,
    bem_port_area_m2: normalized.bemPortAreaM2 as number,
    port_area_source: normalized.portAreaSource,
    passive_cardioid_foam_resistance_pa_s_m3: normalized.foamResistancePaSM3 as number,
    passive_cardioid_invert_port: normalized.invertPort,
    passive_cardioid_coupled: normalized.coupled,
  };
}

/** Why the cardioid section cannot be submitted yet, in the user's words. */
export function passiveCardioidBlocker(
  state: Pick<CadReturnState, 'passiveCardioid' | 'driveChannels'>,
): string | null {
  const form = state.passiveCardioid;
  if (!form.enabled) return null;
  const missing = passiveCardioidMissingFields(form);
  if (missing.length) {
    return `Passive cardioid needs ${missing.map((field) => PASSIVE_CARDIOID_FIELD_LABELS[field]).join(', ')}. `
      + 'The campaign inputs are submitted as one set or not at all.';
  }
  if (form.coupled && state.driveChannels.some((channel) => channel.id === PASSIVE_CARDIOID_CHANNEL_ID)) {
    return `A drive channel is already named "${PASSIVE_CARDIOID_CHANNEL_ID}", which the coupled solve reserves `
      + 'for its derived output. Reassign that source to a different channel, or turn Coupled off.';
  }
  return null;
}

/** Drive-channel ids the rail may offer. The coupled campaign owns its own. */
export function assignableChannelIds(ids: readonly string[], coupled: boolean): string[] {
  return coupled ? ids.filter((id) => id !== PASSIVE_CARDIOID_CHANNEL_ID) : [...ids];
}

export interface CombinePair {
  key: string;
  lower: string;
  upper: string;
  /** The frequency this pair submits: the user's value when they set one, else
   * the role default, else the log-spaced in-sweep fallback. An unlinked pair
   * reports the lower channel's low-pass corner, which is the closest thing to
   * "the crossover" it has. */
  hz: number;
  lowerRole: string | undefined;
  upperRole: string | undefined;
  /** The role-based default for this pair, or undefined when either end has no
   * banded role. */
  defaultHz: number | undefined;
  /** Whether that role default lies outside the current sweep, in which case
   * the log-spaced fallback is used instead: the server refuses a crossover
   * outside the solved band (`SolveRequest.validate_combine_band`), so a
   * default must never be the reason a solve is rejected. */
  outsideSweep: boolean;
  /** Whether the two sections agree, so one frequency and one slope describe
   * the pair. An unlinked pair is editable only in Advanced. */
  linked: boolean;
  family: FilterFamily;
  order: number;
}

const ROLE_BAND_RANK: Record<string, number> = { LF: 0, MF: 1, HF: 2 };

/** What a speaker designer expects to see in the field before touching it.
 * Keyed lowest band first, matching the chain's own order. */
const ROLE_DEFAULT_HZ: Record<string, number> = {
  'LF→MF': 100,
  'MF→HF': 1_000,
  'LF→HF': 1_000,
};

/** The default crossover for a pair of banded roles, or undefined when either
 * end is unroled or the two share a band. */
export function combineDefaultHz(
  lowerRole: string | undefined,
  upperRole: string | undefined,
): number | undefined {
  return lowerRole && upperRole ? ROLE_DEFAULT_HZ[`${lowerRole}→${upperRole}`] : undefined;
}

/** The band-carrying sources a chain is ordered by. The stored solve profile
 * carries the same two fields under another name, which is why the chain
 * helpers below take this shape rather than a whole bundle. */
interface RoleSource {
  id: string;
  role: string;
}

function bundleSources(state: Pick<CadReturnState, 'selectedBundle'>): RoleSource[] {
  return (state.selectedBundle?.sources ?? []).map(({ id, role }) => ({ id, role }));
}

function memberRole(
  channels: readonly CadDriveChannel[],
  sources: readonly RoleSource[],
  channelId: string,
): string | undefined {
  const channel = channels.find((item) => item.id === channelId);
  return (channel?.source_ids ?? [])
    .map((id) => sources.find((source) => source.id === id)?.role ?? '')
    .filter((role) => ROLE_BAND_RANK[role] !== undefined)
    .sort((a, b) => ROLE_BAND_RANK[a] - ROLE_BAND_RANK[b])[0];
}

function memberOrder(channels: readonly CadDriveChannel[], sources: readonly RoleSource[]): string[] {
  return [...channels]
    .map((channel, index) => {
      const ranks = channel.source_ids
        .map((id) => ROLE_BAND_RANK[sources.find((source) => source.id === id)?.role ?? ''])
        .filter((rank): rank is number => rank !== undefined);
      return { id: channel.id, index, rank: ranks.length ? Math.min(...ranks) : Number.POSITIVE_INFINITY };
    })
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.id);
}

interface ChainDefault {
  key: string;
  lower: string;
  upper: string;
  lowerRole: string | undefined;
  upperRole: string | undefined;
  defaultHz: number | undefined;
  outsideSweep: boolean;
  /** The frequency an untouched pair submits. */
  defaultOrFallbackHz: number;
}

/** The pairs and the frequency each one holds before the user touches it. This
 * is the base the spec override sits on: role defaults where the roles say
 * one, a log-spaced in-sweep frequency where they do not. */
function chainDefaults(
  channels: readonly CadDriveChannel[],
  sources: readonly RoleSource[],
  frequencyStartHz: number,
  frequencyEndHz: number,
): ChainDefault[] {
  const members = memberOrder(channels, sources);
  if (members.length < 2) return [];
  const logStart = Math.log(Math.max(1, frequencyStartHz));
  const logEnd = Math.log(Math.max(frequencyStartHz + 1, frequencyEndHz));
  return members.slice(0, -1).map((lower, index) => {
    const upper = members[index + 1];
    const spaced = Math.round(Math.exp(logStart + ((index + 1) * (logEnd - logStart)) / members.length));
    const lowerRole = memberRole(channels, sources, lower);
    const upperRole = memberRole(channels, sources, upper);
    const defaultHz = combineDefaultHz(lowerRole, upperRole);
    const outsideSweep = defaultHz !== undefined
      && (defaultHz < frequencyStartHz || defaultHz > frequencyEndHz);
    return {
      key: `${lower}→${upper}`,
      lower,
      upper,
      lowerRole,
      upperRole,
      defaultHz,
      outsideSweep,
      defaultOrFallbackHz: defaultHz !== undefined && !outsideSweep ? defaultHz : spaced,
    };
  });
}

/** The band a channel speaks for: the lowest banded role among its sources,
 * which is the same rank that orders the chain. Undefined when no source
 * carries one. */
export function combineChannelRole(
  state: Pick<CadReturnState, 'driveChannels' | 'selectedBundle'>,
  channelId: string,
): string | undefined {
  return memberRole(state.driveChannels, bundleSources(state), channelId);
}

/** Whether the combined output is on: the user's explicit choice, or on by
 * default for a return with two or more drive channels. */
export function combineEnabledEffective(
  state: Pick<CadReturnState, 'combineEnabled' | 'driveChannels'>,
): boolean {
  return state.combineEnabled ?? state.driveChannels.length >= 2;
}

/** Chain members ordered lowest band first, from the return's source roles.
 * Channels whose sources carry no banded role keep their listed position at
 * the end, so an unroled return degrades to listing order rather than a
 * guess. */
export function combineMembers(
  state: Pick<CadReturnState, 'driveChannels' | 'selectedBundle'>,
): string[] {
  return memberOrder(state.driveChannels, bundleSources(state));
}

export type CombineChainState = Pick<CadReturnState,
  'driveChannels' | 'selectedBundle' | 'combineSpec' | 'channelDrivers'
  | 'frequencyStartHz' | 'frequencyEndHz'>;

/** The untouched chain: LR4 at the role defaults, auto gain when level
 * matching would default on, auto delay. Null when there is nothing to
 * combine. */
export function combineBaseSpec(state: CombineChainState): CrossoverSpec | null {
  const pairs = chainDefaults(state.driveChannels, bundleSources(state), state.frequencyStartHz, state.frequencyEndHz);
  if (!pairs.length) return null;
  return expandLegacy(
    combineMembers(state),
    pairs.map((pair) => pair.defaultOrFallbackHz),
    combineLevelMatchDefault(state),
    true,
  );
}

/**
 * The spec the rail draws and the wire submits.
 *
 * The stored override wins, but only while it still describes this return's
 * chain. A drive-channel edit that adds, removes or renames a member leaves an
 * override naming channels that no longer exist, and submitting that is a
 * refusal at best and a crossover on the wrong pair at worst; the base chain
 * is the honest answer there.
 */
export function combineSpecEffective(state: CombineChainState): CrossoverSpec | null {
  const base = combineBaseSpec(state);
  if (!base) return null;
  const override = state.combineSpec;
  if (!override) return base;
  const sameMembers = override.members.length === base.members.length
    && override.members.every((member, index) => member === base.members[index]);
  return sameMembers ? override : base;
}

/** Adjacent chain pairs, defaulted by the roles of the two bands they join and
 * falling back to a log-spaced frequency inside the current sweep, so an
 * untouched form is both submittable and what a designer would have typed. */
export function combineChain(state: CombineChainState): CombinePair[] {
  const spec = combineSpecEffective(state);
  const defaults = chainDefaults(state.driveChannels, bundleSources(state), state.frequencyStartHz, state.frequencyEndHz);
  if (!spec) return [];
  const specPairs = pairsOf(spec);
  return defaults.map((pair, index) => {
    const specPair = specPairs[index];
    const section = specPair?.lowerLp ?? specPair?.upperHp ?? null;
    return {
      key: pair.key,
      lower: pair.lower,
      upper: pair.upper,
      hz: section?.fcHz ?? pair.defaultOrFallbackHz,
      lowerRole: pair.lowerRole,
      upperRole: pair.upperRole,
      defaultHz: pair.defaultHz,
      outsideSweep: pair.outsideSweep,
      linked: specPair?.linked ?? false,
      family: section?.family ?? 'lr',
      order: section?.order ?? 4,
    };
  });
}

/** Whether level match should default off: real voltage-driven levels exist
 * for every member, so re-equalising them would erase the drivers' point. */
export function combineLevelMatchDefault(
  state: Pick<CadReturnState, 'driveChannels' | 'channelDrivers'>,
): boolean {
  const allDriven = state.driveChannels.length > 0 && state.driveChannels.every(
    (channel) => channelAcceptsDriver(channel)
      && channelDriverWire(state.channelDrivers[channel.id]) !== undefined,
  );
  return !allDriven;
}

/** The submitted combine, always in the per-channel v2 form. The legacy
 * `crossovers_hz` triple is still accepted by the server, but it cannot state
 * a family, a slope, a manual gain or an unlinked pair, so nothing sends it. */
export function combineWire(
  state: Pick<CadReturnState, 'combineEnabled'> & CombineChainState,
): CrossoverWire | undefined {
  if (!combineEnabledEffective(state)) return undefined;
  const spec = combineSpecEffective(state);
  return spec ? toWire(spec) : undefined;
}

export function blockingFindings(record: CadReturnIngestRecord | null): CadReturnIngestRecord['findings'] {
  return record?.findings.filter((finding) => finding.blocking) ?? [];
}

export function unacknowledgedBlocking(state: Pick<CadReturnState, 'ingestRecord' | 'acknowledgedFindingIds'>): string[] {
  const acknowledged = new Set(state.acknowledgedFindingIds);
  return blockingFindings(state.ingestRecord).map((finding) => finding.id).filter((id) => !acknowledged.has(id));
}

export function acknowledgedFindingWire(record: CadReturnIngestRecord, findingIds: string[]): string[] {
  return findingIds.map((id) => `${record.report_sha256}:${id}`);
}

export function resetCadReturnStore(): void {
  supersedeIngestIntent();
  selectedSolveProfileKey = null;
  useCadReturnStore.setState({
    selectedBundle: null,
    ingestRecord: null,
    acknowledgedFindingIds: [],
    ...initialFromBundle(null),
    areaDriftSourceIds: [],
    exteriorOnly: false,
    combineEnabled: null,
    combineSpec: null,
    channelDrivers: {},
    passiveCardioid: { ...PASSIVE_CARDIOID_DEFAULTS },
    driveVoltageV: 2.83,
    frequencyStartHz: 200,
    frequencyEndHz: 20_000,
    frequencyCount: 24,
    needsIngest: true,
    ingestedBundleIdentity: null,
    ingestStaleReason: null,
  });
}
