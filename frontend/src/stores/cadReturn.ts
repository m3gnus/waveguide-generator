import { create } from 'zustand';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';

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
  setSweep: (update: Partial<Pick<CadReturnState, 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>>) => void;
}

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
  return { selectedBundle, sourceSizesMm, skippedSourceIds, areaDriftOverrides, driveChannels: groupChannels(rows) };
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
    set({
      selectedBundle,
      ingestRecord: null,
      acknowledgedFindingIds: [],
      ...initialFromBundle(selectedBundle),
      areaDriftSourceIds: [],
      exteriorOnly: false,
      combineEnabled: false,
      combineCrossoversHz: {},
      combineLevelMatch: null,
      combineAlign: null,
      channelDrivers: {},
      needsIngest: true,
      ingestedBundleIdentity: null,
      ingestStaleReason: null,
    });
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
  },
  setRigidSize: (rigidSizeMm) => { supersedeIngestIntent(); set({ rigidSizeMm, needsIngest: true }); },
  setTransition: (transitionMm) => { supersedeIngestIntent(); set({ transitionMm, needsIngest: true }); },
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
      return { skippedSourceIds, driveChannels, sourceSizesMm, needsIngest: true };
    });
  },
  setSourceChannel: (sourceId, channelId) => set((state) => {
    const activeIds = (state.selectedBundle?.sources ?? []).map((source) => source.id).filter((id) => !state.skippedSourceIds.includes(id));
    const rows = activeIds.map((id) => {
      const existing = state.driveChannels.find((channel) => channel.source_ids.includes(id));
      return { sourceId: id, channelId: id === sourceId ? channelId : existing?.id ?? id, motion: existing?.motion ?? 'normal' as const };
    });
    return { driveChannels: groupChannels(rows) };
  }),
  setChannelMotion: (channelId, motion) => set((state) => ({
    driveChannels: state.driveChannels.map((channel) => channel.id === channelId ? { ...channel, motion } : channel),
  })),
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
  setExteriorOnly: (exteriorOnly) => set({ exteriorOnly }),
  setCombineEnabled: (combineEnabled) => set({ combineEnabled }),
  setCombineCrossover: (pairKey, hz) => set((state) => ({
    combineCrossoversHz: { ...state.combineCrossoversHz, [pairKey]: hz },
  })),
  setCombineLevelMatch: (combineLevelMatch) => set({ combineLevelMatch }),
  setCombineAlign: (combineAlign) => set({ combineAlign }),
  setChannelDriverEnabled: (channelId, enabled) => set((state) => ({
    channelDrivers: {
      ...state.channelDrivers,
      [channelId]: { enabled, fields: state.channelDrivers[channelId]?.fields ?? {} },
    },
  })),
  setChannelDriverField: (channelId, field, value) => set((state) => {
    const current = state.channelDrivers[channelId] ?? { enabled: true, fields: {} };
    const fields = { ...current.fields };
    if (value === null || !Number.isFinite(value)) delete fields[field];
    else fields[field] = value;
    return { channelDrivers: { ...state.channelDrivers, [channelId]: { ...current, fields } } };
  }),
  setDriveVoltage: (driveVoltageV) => set({ driveVoltageV }),
  setSweep: (update) => set(update),
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
    (channel) => channelDriverWire(state.channelDrivers[channel.id]) !== undefined,
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
    driveVoltageV: 2.83,
    frequencyStartHz: 200,
    frequencyEndHz: 20_000,
    frequencyCount: 24,
    needsIngest: true,
    ingestedBundleIdentity: null,
    ingestStaleReason: null,
  });
}
