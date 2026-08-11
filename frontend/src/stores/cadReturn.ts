import { create } from 'zustand';
import type { CadReturnBundle, CadReturnIngestRecord } from '../api/cadlink';

export interface CadDriveChannel {
  id: string;
  source_ids: string[];
  motion: 'normal' | 'axial';
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
  frequencyStartHz: number;
  frequencyEndHz: number;
  frequencyCount: number;
  needsIngest: boolean;
  ingestedBundleIdentity: string | null;
  ingestStaleReason: string | null;
  selectBundle: (bundle: CadReturnBundle | null) => void;
  refreshSelectedBundle: (bundle: CadReturnBundle | null) => void;
  applyIngest: (record: CadReturnIngestRecord) => void;
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
  frequencyStartHz: 200,
  frequencyEndHz: 20_000,
  frequencyCount: 24,
  needsIngest: true,
  ingestedBundleIdentity: null,
  ingestStaleReason: null,
  selectBundle: (selectedBundle) => set({
    selectedBundle,
    ingestRecord: null,
    acknowledgedFindingIds: [],
    ...initialFromBundle(selectedBundle),
    areaDriftSourceIds: [],
    exteriorOnly: false,
    needsIngest: true,
    ingestedBundleIdentity: null,
    ingestStaleReason: null,
  }),
  refreshSelectedBundle: (selectedBundle) => set((state) => {
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
  }),
  applyIngest: (ingestRecord) => {
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
  },
  acknowledge: (findingId, value) => set((state) => ({
    acknowledgedFindingIds: value
      ? [...new Set([...state.acknowledgedFindingIds, findingId])]
      : state.acknowledgedFindingIds.filter((id) => id !== findingId),
  })),
  acknowledgeAllBlocking: () => set((state) => ({
    acknowledgedFindingIds: state.ingestRecord?.findings.filter((finding) => finding.blocking).map((finding) => finding.id) ?? [],
  })),
  setSourceSize: (sourceId, value) => set((state) => ({ sourceSizesMm: { ...state.sourceSizesMm, [sourceId]: value }, needsIngest: true })),
  setRigidSize: (rigidSizeMm) => set({ rigidSizeMm, needsIngest: true }),
  setTransition: (transitionMm) => set({ transitionMm, needsIngest: true }),
  setSkipped: (sourceId, skipped) => set((state) => {
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
  }),
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
  setAreaDriftOverride: (sourceId, enabled) => set((state) => ({
    areaDriftOverrides: enabled
      ? [...new Set([...state.areaDriftOverrides, sourceId])]
      : state.areaDriftOverrides.filter((id) => id !== sourceId),
    needsIngest: true,
  })),
  flagAreaDrift: (sourceId) => set((state) => ({ areaDriftSourceIds: [...new Set([...state.areaDriftSourceIds, sourceId])] })),
  setExteriorOnly: (exteriorOnly) => set({ exteriorOnly }),
  setSweep: (update) => set(update),
}));

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
  useCadReturnStore.setState({
    selectedBundle: null,
    ingestRecord: null,
    acknowledgedFindingIds: [],
    ...initialFromBundle(null),
    areaDriftSourceIds: [],
    exteriorOnly: false,
    frequencyStartHz: 200,
    frequencyEndHz: 20_000,
    frequencyCount: 24,
    needsIngest: true,
    ingestedBundleIdentity: null,
    ingestStaleReason: null,
  });
}
