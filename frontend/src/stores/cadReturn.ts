import { create } from 'zustand';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';
import { useDocumentStore, type DesignIdentity } from './document';
import { namespaceStorage } from './durableSettings';

export interface CadDriveChannel {
  id: string;
  source_ids: string[];
  motion: 'normal' | 'axial';
}

export const DRIVER_FIELD_KEYS = [
  'sd_cm2', 'bl_t_m', 're_ohm', 'le_mh', 'mmd_g', 'cms_m_per_n',
  'rms_kg_per_s', 'xmax_mm', 'count', 'rear_volume_l',
] as const;
export type DriverFieldKey = typeof DRIVER_FIELD_KEYS[number];
export const DRIVER_REQUIRED_KEYS: readonly DriverFieldKey[] = [
  'sd_cm2', 'bl_t_m', 're_ohm', 'mmd_g', 'cms_m_per_n',
];

export interface ChannelDriverForm {
  enabled: boolean;
  fields: Partial<Record<DriverFieldKey, number>>;
}

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
  combineEnabled: boolean;
  combineCrossoversHz: Record<string, number>;
  combineLevelMatch: boolean | null;
  combineAlign: boolean | null;
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
   * finding acknowledgements never do. Otherwise falls back to a full reset. */
  selectArrivedBundle: (bundle: CadReturnBundle) => 'carried' | 'reset';
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
  setCombineCrossover: (pairKey: string, hz: number) => void;
  setCombineLevelMatch: (value: boolean | null) => void;
  setCombineAlign: (value: boolean | null) => void;
  setChannelDriverEnabled: (channelId: string, enabled: boolean) => void;
  setChannelDriverField: (channelId: string, field: DriverFieldKey, value: number | null) => void;
  setDriveVoltage: (value: number) => void;
  setPassiveCardioid: (patch: Partial<PassiveCardioidForm>) => void;
  setSweep: (update: Partial<Pick<CadReturnState, 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>>) => void;
}

const solveProfileStorage = namespaceStorage('cadSolveProfiles');
const SOLVE_PROFILE_STORAGE_VERSION = 1;
const MAX_SOLVE_PROFILES = 20;

type PersistedSolveSettings = Pick<CadReturnState,
  'sourceSizesMm' | 'rigidSizeMm' | 'transitionMm' | 'skippedSourceIds' | 'driveChannels'
  | 'exteriorOnly' | 'combineEnabled' | 'combineCrossoversHz' | 'combineLevelMatch'
  | 'combineAlign' | 'channelDrivers' | 'passiveCardioid' | 'driveVoltageV'
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

function parseChannelDrivers(value: unknown): Record<string, ChannelDriverForm> | null {
  if (!isObject(value)) return null;
  const drivers: Record<string, ChannelDriverForm> = {};
  for (const [channelId, item] of Object.entries(value)) {
    if (!channelId || !isObject(item) || typeof item.enabled !== 'boolean' || !isObject(item.fields)) return null;
    const fields: Partial<Record<DriverFieldKey, number>> = {};
    for (const [field, fieldValue] of Object.entries(item.fields)) {
      if (!DRIVER_FIELD_KEYS.includes(field as DriverFieldKey)
        || typeof fieldValue !== 'number' || !Number.isFinite(fieldValue)) return null;
      fields[field as DriverFieldKey] = fieldValue;
    }
    drivers[channelId] = { enabled: item.enabled, fields };
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

function parseSolveSettings(value: unknown, inventory: SourceInventoryEntry[]): PersistedSolveSettings | null {
  if (!isObject(value)) return null;
  const sourceSizesMm = finiteNumberRecord(value.sourceSizesMm);
  const combineCrossoversHz = finiteNumberRecord(value.combineCrossoversHz);
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
  if (typeof value.rigidSizeMm !== 'number' || !Number.isFinite(value.rigidSizeMm)
    || typeof value.transitionMm !== 'number' || !Number.isFinite(value.transitionMm)
    || typeof value.exteriorOnly !== 'boolean'
    || typeof value.combineEnabled !== 'boolean'
    || (value.combineLevelMatch !== null && typeof value.combineLevelMatch !== 'boolean')
    || (value.combineAlign !== null && typeof value.combineAlign !== 'boolean')
    || typeof value.driveVoltageV !== 'number' || !Number.isFinite(value.driveVoltageV)
    || typeof value.frequencyStartHz !== 'number' || !Number.isFinite(value.frequencyStartHz)
    || typeof value.frequencyEndHz !== 'number' || !Number.isFinite(value.frequencyEndHz)
    || typeof value.frequencyCount !== 'number' || !Number.isFinite(value.frequencyCount)) return null;
  return {
    sourceSizesMm,
    rigidSizeMm: value.rigidSizeMm,
    transitionMm: value.transitionMm,
    skippedSourceIds,
    driveChannels,
    exteriorOnly: value.exteriorOnly,
    combineEnabled: value.combineEnabled,
    combineCrossoversHz,
    combineLevelMatch: value.combineLevelMatch,
    combineAlign: value.combineAlign,
    channelDrivers,
    passiveCardioid,
    driveVoltageV: value.driveVoltageV,
    frequencyStartHz: value.frequencyStartHz,
    frequencyEndHz: value.frequencyEndHz,
    frequencyCount: value.frequencyCount,
  };
}

function solveProfileKey(identity: Pick<DesignIdentity, 'designId' | 'lineageId'>, inventory: SourceInventoryCarrier): string {
  return JSON.stringify([identity.designId, identity.lineageId, sourceInventorySignature(inventory)]);
}

function dropStoredSolveProfiles(): void {
  try { solveProfileStorage.removeItem('cadSolveProfiles'); } catch { /* persistence is best effort */ }
}

function readStoredSolveProfiles(): StoredSolveProfile[] {
  try {
    const raw = solveProfileStorage.getItem('cadSolveProfiles');
    if (raw === null) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!isObject(parsed) || parsed.version !== SOLVE_PROFILE_STORAGE_VERSION
      || !Array.isArray(parsed.profiles) || parsed.profiles.length > MAX_SOLVE_PROFILES) {
      dropStoredSolveProfiles();
      return [];
    }
    const profiles: StoredSolveProfile[] = [];
    for (const value of parsed.profiles) {
      if (!isObject(value) || typeof value.key !== 'string'
        || typeof value.designId !== 'string' || !value.designId
        || typeof value.lineageId !== 'string' || !value.lineageId) {
        dropStoredSolveProfiles();
        return [];
      }
      const inventory = parseInventory(value.inventory);
      const settings = inventory ? parseSolveSettings(value.settings, inventory) : null;
      if (!inventory || !settings) {
        dropStoredSolveProfiles();
        return [];
      }
      const identity = { designId: value.designId, lineageId: value.lineageId };
      if (value.key !== solveProfileKey(identity, { readable: true, sources: inventory })) {
        dropStoredSolveProfiles();
        return [];
      }
      profiles.push({ key: value.key, ...identity, inventory, settings });
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

function persistedSolveSettings(state: CadReturnState): PersistedSolveSettings {
  return {
    sourceSizesMm: { ...state.sourceSizesMm },
    rigidSizeMm: state.rigidSizeMm,
    transitionMm: state.transitionMm,
    skippedSourceIds: [...state.skippedSourceIds],
    driveChannels: state.driveChannels.map((channel) => ({ ...channel, source_ids: [...channel.source_ids] })),
    exteriorOnly: state.exteriorOnly,
    combineEnabled: state.combineEnabled,
    combineCrossoversHz: { ...state.combineCrossoversHz },
    combineLevelMatch: state.combineLevelMatch,
    combineAlign: state.combineAlign,
    channelDrivers: Object.fromEntries(Object.entries(state.channelDrivers).map(([channelId, form]) => [
      channelId,
      { enabled: form.enabled, fields: { ...form.fields } },
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
  combineEnabled: false,
  combineCrossoversHz: {},
  combineLevelMatch: null,
  combineAlign: null,
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
      combineEnabled: false,
      combineCrossoversHz: {},
      combineLevelMatch: null,
      combineAlign: null,
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
        combineEnabled: false,
        combineCrossoversHz: {},
        combineLevelMatch: null,
        combineAlign: null,
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
      return restored ? 'carried' : 'reset';
    }
    supersedeIngestIntent();
    set((state) => ({
      ...reconcileListing(state, bundle),
      // The new geometry needs its own ingest, and its findings are re-earned:
      // an acknowledgement describes evidence the user saw, not this bundle.
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
    const channels = current.driveChannels.flatMap((channel) => {
      const source_ids = channel.source_ids.filter((id) => !skipped.has(id));
      return source_ids.length ? [{ ...channel, source_ids }] : [];
    });
    set({
      ingestRecord,
      acknowledgedFindingIds: [],
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
  acknowledge: (findingId, value) => set((state) => ({
    acknowledgedFindingIds: value
      ? [...new Set([...state.acknowledgedFindingIds, findingId])]
      : state.acknowledgedFindingIds.filter((id) => id !== findingId),
  })),
  acknowledgeAllBlocking: () => set((state) => ({
    acknowledgedFindingIds: state.ingestRecord?.findings.filter((finding) => finding.blocking).map((finding) => finding.id) ?? [],
  })),
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
  setCombineCrossover: (pairKey, hz) => {
    set((state) => ({ combineCrossoversHz: { ...state.combineCrossoversHz, [pairKey]: hz } }));
    saveSolveProfile(get());
  },
  setCombineLevelMatch: (combineLevelMatch) => { set({ combineLevelMatch }); saveSolveProfile(get()); },
  setCombineAlign: (combineAlign) => { set({ combineAlign }); saveSolveProfile(get()); },
  setChannelDriverEnabled: (channelId, enabled) => {
    set((state) => ({
      channelDrivers: {
        ...state.channelDrivers,
        [channelId]: { enabled, fields: state.channelDrivers[channelId]?.fields ?? {} },
      },
    }));
    saveSolveProfile(get());
  },
  setChannelDriverField: (channelId, field, value) => {
    set((state) => {
      const current = state.channelDrivers[channelId] ?? { enabled: true, fields: {} };
      const fields = { ...current.fields };
      if (value === null || !Number.isFinite(value)) delete fields[field];
      else fields[field] = value;
      return { channelDrivers: { ...state.channelDrivers, [channelId]: { ...current, fields } } };
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

/** The wire driver spec for one channel, or undefined while incomplete. */
export function channelDriverWire(form: ChannelDriverForm | undefined): Record<string, number> | undefined {
  if (!form?.enabled) return undefined;
  if (DRIVER_REQUIRED_KEYS.some((key) => form.fields[key] === undefined)) return undefined;
  const wire: Record<string, number> = {};
  for (const key of DRIVER_FIELD_KEYS) {
    const value = form.fields[key];
    if (value !== undefined) wire[key] = value;
  }
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

export interface CombinePair { key: string; lower: string; upper: string; hz: number }

const ROLE_BAND_RANK: Record<string, number> = { LF: 0, MF: 1, HF: 2 };

/** Chain members ordered lowest band first, from the return's source roles.
 * Channels whose sources carry no banded role keep their listed position at
 * the end, so an unroled return degrades to listing order rather than a
 * guess. */
export function combineMembers(
  state: Pick<CadReturnState, 'driveChannels' | 'selectedBundle'>,
): string[] {
  const sources = state.selectedBundle?.sources ?? [];
  return [...state.driveChannels]
    .map((channel, index) => {
      const ranks = channel.source_ids
        .map((id) => ROLE_BAND_RANK[sources.find((source) => source.id === id)?.role ?? ''])
        .filter((rank): rank is number => rank !== undefined);
      return { id: channel.id, index, rank: ranks.length ? Math.min(...ranks) : Number.POSITIVE_INFINITY };
    })
    .sort((a, b) => a.rank - b.rank || a.index - b.index)
    .map((entry) => entry.id);
}

/** Adjacent chain pairs with log-spaced default crossovers inside the
 * current sweep, so an untouched form is submittable. */
export function combineChain(
  state: Pick<CadReturnState, 'driveChannels' | 'selectedBundle' | 'combineCrossoversHz' | 'frequencyStartHz' | 'frequencyEndHz'>,
): CombinePair[] {
  const members = combineMembers(state);
  if (members.length < 2) return [];
  const logStart = Math.log(Math.max(1, state.frequencyStartHz));
  const logEnd = Math.log(Math.max(state.frequencyStartHz + 1, state.frequencyEndHz));
  return members.slice(0, -1).map((lower, index) => {
    const upper = members[index + 1];
    const key = `${lower}→${upper}`;
    const fallback = Math.round(Math.exp(logStart + ((index + 1) * (logEnd - logStart)) / members.length));
    return { key, lower, upper, hz: state.combineCrossoversHz[key] ?? fallback };
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

export function combineWire(
  state: Pick<CadReturnState, 'combineEnabled' | 'driveChannels' | 'selectedBundle' | 'combineCrossoversHz' | 'combineLevelMatch' | 'combineAlign' | 'channelDrivers' | 'frequencyStartHz' | 'frequencyEndHz'>,
): { members: string[]; crossovers_hz: number[]; level_match: boolean; align: boolean } | undefined {
  if (!state.combineEnabled) return undefined;
  const pairs = combineChain(state);
  if (!pairs.length) return undefined;
  return {
    members: combineMembers(state),
    crossovers_hz: pairs.map((pair) => pair.hz),
    level_match: state.combineLevelMatch ?? combineLevelMatchDefault(state),
    // Null means the user has never made a choice. Preserve the server's
    // existing aligned-sum behaviour while still making the choice explicit
    // on every newly submitted wire.
    align: state.combineAlign ?? true,
  };
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
    combineEnabled: false,
    combineCrossoversHz: {},
    combineLevelMatch: null,
    combineAlign: null,
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
