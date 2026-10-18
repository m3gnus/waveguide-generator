import type { CadReturnIngestRecord } from '../api/cadlink';
import type { CadSolveSetup, CadSourceInventoryEntry } from '../api/cadOperations';
import { workspaceModeStore } from './workspaceMode';
import { ImportedModelApiError, readCadInventorySetup } from '../api/importedModels';
import type { ImportedSetup } from '../jobs/importedSetup';
import { cadHistorySetup } from './cadSetupRestore';
import { useCadReturnStore, sourceInventorySignature } from './cadReturn';
import { useCadPreparationStore } from './cadPreparation';
import { cadDraftNamespace, durableSettings } from './durableSettings';
import { applyCadOptions, cadOptionsSnapshot, endOpenedSetup } from './importedModel';
import { readLegacySolveProfiles, parseSolveSettings, type PersistedSolveSettings } from './legacyCadProfiles';
import { defaultSolveOptions, importedOptionsFromWire, normalizePersistedSolveOptions, type ImportedOptionsSnapshot } from './solveOptions';
import { gateSolveSettingsEdits, subscribeSolveSettingsEdits } from './solveSettingsEdits';

interface CadDraft {
  baseRevisionId: string | null;
  updatedAt: number;
  settings: PersistedSolveSettings;
  options: ImportedOptionsSnapshot;
  preparation: { symmetryMode: 'auto' | 'full'; areaDriftOverrides: string[] };
  /** Legacy entries are considered only after checking that the server has no setup. */
  legacy?: boolean;
}
const fields = ['sourceSizesMm', 'rigidSizeMm', 'transitionMm', 'skippedSourceIds', 'driveChannels',
  'exteriorOnly', 'exteriorOnlyRegionIds', 'combineEnabled', 'combineSpec', 'channelDrivers',
  'passiveCardioid', 'driveVoltageV', 'maxDriveVoltageV', 'frequencyStartHz', 'frequencyEndHz', 'frequencyCount'] as const;
const baseRevisions = new Map<string, string | null>();
function drafts(): Record<string, CadDraft> {
  const all: Record<string, CadDraft> = {};
  for (const namespace of durableSettings.draftNamespaces()) {
    try {
      for (const [key, draft] of Object.entries(JSON.parse(durableSettings.get(namespace) ?? '{}'))) {
        const owner = JSON.parse(key)?.[0];
        if (typeof owner === 'string' && cadDraftNamespace(owner) === namespace && draft && typeof draft === 'object') all[key] = draft as CadDraft;
      }
    } catch { /* discard corrupt cache */ }
  }
  return all;
}
function writeDrafts(next: Record<string, CadDraft>): void {
  const groups = new Map<ReturnType<typeof cadDraftNamespace>, Record<string, CadDraft>>();
  const entries = Object.entries(next).sort((a, b) => (b[1].updatedAt ?? 0) - (a[1].updatedAt ?? 0)).slice(0, 20);
  for (const [key, draft] of entries) {
    const namespace = cadDraftNamespace(JSON.parse(key)[0]);
    const group = groups.get(namespace) ?? {};
    group[key] = draft; groups.set(namespace, group);
  }
  for (const namespace of new Set([...durableSettings.draftNamespaces(), ...groups.keys()])) {
    const group = groups.get(namespace);
    const raw = group ? JSON.stringify(group) : null;
    if (raw !== durableSettings.get(namespace)) durableSettings.set(namespace, raw);
  }
}
/** Before the first prepare the return itself is the owner, never the open design. */
function unclaimedOwner(): string | null {
  const bundle = useCadReturnStore.getState().selectedBundle;
  return bundle ? `unclaimed:${bundle.documentNativeId || bundle.bundlePath}` : null;
}
export function cadDraftKey(model: string, sources: CadSourceInventoryEntry[]): string {
  return JSON.stringify([model, sourceInventorySignature({ readable: true, sources })]);
}
export function currentCadDraftKey(): string | null {
  const state = useCadReturnStore.getState();
  const model = state.ingestRecord?.project?.lineage_id ?? state.projectLineageId ?? unclaimedOwner();
  return model && state.selectedBundle?.sources.length && state.selectedBundle.bundleOrigin !== 'native'
    ? cadDraftKey(model, state.selectedBundle.sources) : null;
}
/** The publisher journals edits synchronously, including edits it cannot publish yet. */
export function retainCadDraft(): { key: string; raw: string } | null {
  const key = currentCadDraftKey();
  const options = cadOptionsSnapshot();
  if (!key || !options) return null;
  const state = useCadReturnStore.getState();
  const draft: CadDraft = structuredClone({
    baseRevisionId: baseRevisions.get(key) ?? null, updatedAt: Date.now(),
    settings: Object.fromEntries(fields.map(field => [field, state[field]])) as PersistedSolveSettings,
    options,
    preparation: { symmetryMode: useCadPreparationStore.getState().symmetryMode, areaDriftOverrides: state.areaDriftOverrides },
  });
  writeDrafts({ ...drafts(), [key]: draft });
  return { key, raw: JSON.stringify(draft) };
}
/** An older acknowledgement must never clear a newer edit's journal. */
export function clearCadDraft(key: string, raw: string, revisionId: string | null = null): void {
  const next = drafts();
  const acknowledgedBase = baseRevisions.get(key);
  baseRevisions.set(key, revisionId);
  const previous = JSON.parse(raw) as CadDraft;
  const current = next[key];
  if (!current) return;
  // A successful predecessor is now the base of a newer incomplete edit too.
  if (JSON.stringify({ ...current, baseRevisionId: previous.baseRevisionId }) !== raw) {
    if (current.baseRevisionId === previous.baseRevisionId || current.baseRevisionId === acknowledgedBase) {
      current.baseRevisionId = revisionId; writeDrafts(next);
    }
    return;
  }
  delete next[key];
  writeDrafts(next);
}

/** Move every old entry once; no MRU and no legacy key writes remain. */
export async function migrateCadProfiles(fetcher: typeof fetch = fetch): Promise<void> {
  const raw = durableSettings.get('cadSolveProfiles');
  const shared = durableSettings.get('cadSetupDrafts');
  const legacy = readLegacySolveProfiles();
  const additions: Record<string, CadDraft> = {};
  // The shared K3 journal has neither base revisions nor verified legacy provenance.
  let verificationFailed = false;
  for (const profile of legacy) {
    const cadOwned = profile.owner.startsWith('cad:');
    const model = cadOwned ? profile.owner.slice(4) : profile.owner.split(':').at(-1)!;
    if (!cadOwned) {
      // The server checks lineage_cad_names; a design's lineage alone proves nothing.
      try {
        const saved = await readCadInventorySetup(model, profile.inventory, fetcher);
        if (!saved.cad_claimed) continue;
      } catch (error) {
        if (!(error instanceof ImportedModelApiError && error.status === 404)) verificationFailed = true;
        continue;
      }
    }
    const key = cadDraftKey(model, profile.inventory);
    additions[key] = {
      baseRevisionId: null, updatedAt: Date.now(), settings: profile.settings,
      options: { ...defaultSolveOptions(), sweepStartHz: profile.settings.frequencyStartHz,
        sweepEndHz: profile.settings.frequencyEndHz, sweepCount: profile.settings.frequencyCount,
        ...profile.solveSelection },
      preparation: { symmetryMode: 'auto', areaDriftOverrides: [] }, legacy: true,
    };
  }
  const next = { ...additions, ...drafts() };
  if (Object.keys(additions).length) writeDrafts(next);
  const destinations = durableSettings.draftNamespaces();
  // Separate namespace writes cannot be atomic: acknowledge destinations before retiring the source.
  if (raw !== null && !verificationFailed) void durableSettings.retireAfterUpload('cadSolveProfiles', destinations, raw);
  if (shared !== null) void durableSettings.retireAfterUpload('cadSetupDrafts', destinations, shared);
}
export async function hasCadDraftForModel(model: string, fetcher: typeof fetch = fetch): Promise<boolean> {
  await migrateCadProfiles(fetcher);
  return Object.keys(drafts()).some(key => {
    try { return JSON.parse(key)[0] === model; } catch { return false; }
  });
}
const restoredListeners = new Set<() => void>();
export function subscribeCadDraftRestores(listener: () => void): () => void {
  restoredListeners.add(listener);
  return () => { restoredListeners.delete(listener); };
}

/** Shared by model reopen and return selection/arrival. Runs keep their immutable setup. */
export function restoreCadProjectSettings(saved: ImportedSetup | null, record: CadReturnIngestRecord,
  includeDraft = true, revisionId: string | null = null): boolean {
  allowLateDraft = includeDraft;
  restoredAtEdit = edits;
  const state = useCadReturnStore.getState();
  const key = currentCadDraftKey();
  const all = drafts();
  // Claiming a return transfers its journal before any setup can be published.
  const unclaimed = unclaimedOwner();
  const unclaimedKey = unclaimed && state.selectedBundle ? cadDraftKey(unclaimed, state.selectedBundle.sources) : null;
  if (key && unclaimedKey && key !== unclaimedKey && all[unclaimedKey]) {
    if (!all[key] && !saved) all[key] = all[unclaimedKey];
    delete all[unclaimedKey]; writeDrafts(all);
  }
  if (key) baseRevisions.set(key, revisionId);
  let draft = includeDraft && key ? all[key] : undefined;
  if (draft) {
    const settings = state.selectedBundle && parseSolveSettings(draft.settings, state.selectedBundle.sources, 4);
    if (!settings || !draft.options || typeof draft.options !== 'object'
      || !draft.preparation || !['auto', 'full'].includes(draft.preparation.symmetryMode)
      || !Array.isArray(draft.preparation.areaDriftOverrides)
      || draft.preparation.areaDriftOverrides.some(id => typeof id !== 'string')) draft = undefined;
    else draft = { ...draft, settings, options: { ...draft.options, ...normalizePersistedSolveOptions(draft.options) } };
  }
  if (draft && key && ((draft.legacy && saved) || (!draft.legacy && draft.baseRevisionId !== revisionId))) {
    delete all[key]; writeDrafts(all); draft = undefined;
  }
  if (saved) {
    const setup = saved as unknown as CadSolveSetup;
    useCadReturnStore.setState({ ...cadHistorySetup(saved, record),
      exteriorOnlyRegionIds: Array.isArray(setup.geometry.exterior_only_region_ids)
        ? setup.geometry.exterior_only_region_ids as string[] : null,
      areaDriftOverrides: setup.preparation?.area_drift_overrides ?? [],
    });
    const options = cadOptionsSnapshot() ?? { ...defaultSolveOptions(), sweepStartHz: 50, sweepEndHz: 20_000, sweepCount: 36 };
    applyCadOptions(importedOptionsFromWire(saved.options, options));
    useCadPreparationStore.setState({ symmetryMode: setup.preparation?.symmetry_mode ?? 'auto' });
  }
  if (draft) {
    useCadReturnStore.setState({ ...draft.settings, areaDriftOverrides: draft.preparation.areaDriftOverrides });
    applyCadOptions(draft.options);
    useCadPreparationStore.setState({ symmetryMode: draft.preparation.symmetryMode });
    endOpenedSetup('cad');
    // The publisher is the only writer, and resumes unsent valid edits too.
    const selection = state.captureSelectionIntent();
    queueMicrotask(() => {
      if (useCadReturnStore.getState().isCurrentSelectionIntent(selection) && currentCadDraftKey() === key) {
        restoredListeners.forEach(listener => listener());
      }
    });
  } else if (!saved) {
    useCadPreparationStore.setState({ symmetryMode: 'auto' });
  }
  // A stored mesh choice different from the retained mesh requires preparation.
  const current = useCadReturnStore.getState();
  if (state.ingestRecord && (JSON.stringify(current.sourceSizesMm) !== JSON.stringify(record.mesh_sizes.source_size_mm)
    || current.rigidSizeMm !== record.mesh_sizes.rigid_size_mm || current.transitionMm !== record.mesh_sizes.transition_mm
    || JSON.stringify(current.skippedSourceIds) !== JSON.stringify(record.skipped_source_ids))) {
    useCadReturnStore.setState({ needsIngest: true, ingestStaleReason: 'Prepare the mesh for the restored model settings.' });
  }
  return !!draft;
}

let edits = 0;
let restoredAtEdit = 0;
let allowLateDraft = false;
const heldEdits: Array<{ selection: number; inputRevision: number; apply: () => unknown }> = [];
gateSolveSettingsEdits((source, apply) => {
  const state = useCadReturnStore.getState();
  if (!state.setupLoading || source === 'solveOptions' || source === 'meshImport'
    || (source === 'importedSolveOptions' && workspaceModeStore.getSnapshot().importedInput !== 'cad')) return false;
  heldEdits.push({ selection: state.captureSelectionIntent(), inputRevision: workspaceModeStore.getSnapshot().inputRevision, apply });
  return true;
});
function releaseEdits(selection: number, apply: boolean): void {
  const queued = heldEdits.filter(edit => edit.selection === selection);
  for (let i = heldEdits.length - 1; i >= 0; i -= 1) {
    if (heldEdits[i].selection === selection) heldEdits.splice(i, 1);
  }
  if (apply) queued.forEach(edit => {
    if (edit.inputRevision === workspaceModeStore.getSnapshot().inputRevision) edit.apply();
  });
}
export function observeCadDraftHydration(settings: Pick<typeof durableSettings, 'subscribe'> = durableSettings): () => void {
  const refresh = () => {
    if (allowLateDraft && restoredAtEdit === edits && currentCadDraftKey()) void loadCadProjectSettings();
  };
  const stops = [settings.subscribe('cadSetupDrafts', refresh), settings.subscribe('cadSolveProfiles', refresh)];
  return () => stops.forEach(stop => stop());
}
observeCadDraftHydration();
subscribeSolveSettingsEdits(edit => { if (edit?.source !== 'solveOptions') edits += 1; });
let readGeneration = 0;
let loading: Promise<boolean> = Promise.resolve(true);
export function resetCadProjectSettingsLoad(): void {
  readGeneration += 1; loading = Promise.resolve(true); heldEdits.length = 0; allowLateDraft = false;
  useCadReturnStore.setState({ setupLoading: false });
}
export function waitForCadProjectSettings(): Promise<boolean> { return loading; }
/** Selection starts this read immediately; ingestion waits before capturing its settings. */
export function loadCadProjectSettings(fetcher: typeof fetch = fetch): Promise<boolean> {
  const request = ++readGeneration;
  const state = useCadReturnStore.getState();
  const bundle = state.selectedBundle;
  const model = state.projectLineageId;
  if (!bundle || bundle.bundleOrigin === 'native' || !bundle.sources.length) {
    resetCadProjectSettingsLoad(); return loading;
  }
  if (!model) {
    resetCadProjectSettingsLoad();
    restoreCadProjectSettings(null, { sources: bundle.sources, skipped_source_ids: state.skippedSourceIds,
      mesh_sizes: { source_size_mm: state.sourceSizesMm, rigid_size_mm: state.rigidSizeMm, transition_mm: state.transitionMm },
    } as unknown as CadReturnIngestRecord);
    return loading;
  }
  const selection = state.captureSelectionIntent();
  for (let i = heldEdits.length - 1; i >= 0; i -= 1) if (heldEdits[i].selection !== selection) heldEdits.splice(i, 1);
  useCadReturnStore.setState({ setupLoading: true });
  const current = () => request === readGeneration && useCadReturnStore.getState().isCurrentSelectionIntent(selection)
    && useCadReturnStore.getState().projectLineageId === model;
  loading = (async () => {
    try {
      if (durableSettings.get('cadSolveProfiles') !== null || durableSettings.get('cadSetupDrafts') !== null) await migrateCadProfiles(fetcher);
      const saved = await readCadInventorySetup(model, bundle.sources.map(({ id, role, required }) => ({ id, role, required })), fetcher);
      if (!current()) return useCadReturnStore.getState().isCurrentSelectionIntent(selection);
      const held = useCadReturnStore.getState();
      const record = held.ingestRecord ?? { sources: bundle.sources.map(source => ({ ...source,
        default_drive_channel_id: source.defaultDriveChannelId })), skipped_source_ids: held.skippedSourceIds,
        mesh_sizes: { source_size_mm: held.sourceSizesMm, rigid_size_mm: held.rigidSizeMm, transition_mm: held.transitionMm },
      } as unknown as CadReturnIngestRecord;
      restoreCadProjectSettings(saved.setup, record, true, saved.revision_id ?? null);
      useCadReturnStore.setState({ setupLoading: false });
      releaseEdits(selection, true);
      return true;
    } catch (error) {
      if (current()) useCadReturnStore.setState({ setupLoading: false, needsIngest: true,
        ingestStaleReason: `Could not load model settings: ${error instanceof Error ? error.message : String(error)}` });
      if (request === readGeneration) releaseEdits(selection, false);
      return false;
    } finally {
      if (request === readGeneration) {
        useCadReturnStore.setState({ setupLoading: false });
        releaseEdits(selection, false);
      }
    }
  })();
  return loading;
}
