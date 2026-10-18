import type { CadReturnIngestRecord } from '../api/cadlink';
import type { CadSolveSetup, CadSourceInventoryEntry } from '../api/cadOperations';
import { workspaceModeStore } from './workspaceMode';
import { readCadInventorySetup } from '../api/importedModels';
import type { ImportedSetup } from '../jobs/importedSetup';
import { cadHistorySetup } from './cadSetupRestore';
import { useCadReturnStore, sourceInventorySignature } from './cadReturn';
import { useCadPreparationStore } from './cadPreparation';
import { durableSettings } from './durableSettings';
import { applyCadOptions, cadOptionsSnapshot, endOpenedSetup } from './importedModel';
import { readLegacySolveProfiles, dropStoredSolveProfiles, parseSolveSettings, type PersistedSolveSettings } from './legacyCadProfiles';
import { defaultSolveOptions, importedOptionsFromWire, normalizePersistedSolveOptions, type ImportedOptionsSnapshot } from './solveOptions';
import { gateSolveSettingsEdits, subscribeSolveSettingsEdits } from './solveSettingsEdits';

interface CadDraft {
  settings: PersistedSolveSettings;
  options: ImportedOptionsSnapshot;
  preparation: { symmetryMode: 'auto' | 'full'; areaDriftOverrides: string[] };
  /** Legacy entries are considered only after checking that the server has no setup. */
  legacy?: boolean;
}
const fields = ['sourceSizesMm', 'rigidSizeMm', 'transitionMm', 'skippedSourceIds', 'driveChannels',
  'exteriorOnly', 'exteriorOnlyRegionIds', 'combineEnabled', 'combineSpec', 'channelDrivers',
  'passiveCardioid', 'driveVoltageV', 'maxDriveVoltageV', 'frequencyStartHz', 'frequencyEndHz', 'frequencyCount'] as const;
function drafts(): Record<string, CadDraft> {
  try {
    const parsed = JSON.parse(durableSettings.get('cadSetupDrafts') ?? '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch { return {}; }
}
function writeDrafts(next: Record<string, CadDraft>): void {
  durableSettings.set('cadSetupDrafts', Object.keys(next).length ? JSON.stringify(next) : null);
}
export function cadDraftKey(model: string, sources: CadSourceInventoryEntry[]): string {
  return JSON.stringify([model, sourceInventorySignature({ readable: true, sources })]);
}
export function currentCadDraftKey(): string | null {
  const state = useCadReturnStore.getState();
  const model = state.ingestRecord?.project?.lineage_id ?? state.projectLineageId;
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
    settings: Object.fromEntries(fields.map(field => [field, state[field]])) as PersistedSolveSettings,
    options,
    preparation: { symmetryMode: useCadPreparationStore.getState().symmetryMode, areaDriftOverrides: state.areaDriftOverrides },
  });
  writeDrafts({ ...drafts(), [key]: draft });
  return { key, raw: JSON.stringify(draft) };
}
/** An older acknowledgement must never clear a newer edit's journal. */
export function clearCadDraft(key: string, raw: string): void {
  const next = drafts();
  if (JSON.stringify(next[key]) !== raw) return;
  delete next[key];
  writeDrafts(next);
}

/** Move every old entry once; no MRU and no legacy key writes remain. */
function migrateProfiles(): void {
  const legacy = readLegacySolveProfiles();
  const next = drafts();
  for (const profile of legacy) {
    // v1-v3 carry only their design's lineage; retain that provenance rather than guessing a CAD owner.
    const model = profile.owner.startsWith('cad:') ? profile.owner.slice(4) : profile.owner.split(':').at(-1)!;
    const key = cadDraftKey(model, profile.inventory);
    if (next[key]) continue;
    next[key] = {
      settings: profile.settings,
      options: { ...defaultSolveOptions(), sweepStartHz: profile.settings.frequencyStartHz,
        sweepEndHz: profile.settings.frequencyEndHz, sweepCount: profile.settings.frequencyCount,
        ...profile.solveSelection },
      preparation: { symmetryMode: 'auto', areaDriftOverrides: [] }, legacy: true,
    };
  }
  if (legacy.length) writeDrafts(next);
  // The draft journal now owns every unsent edit, including unopened models.
  if (durableSettings.get('cadSolveProfiles') !== null) dropStoredSolveProfiles();
}
export function hasCadDraftForModel(model: string): boolean {
  migrateProfiles();
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
  includeDraft = true): boolean {
  allowLateDraft = includeDraft;
  restoredAtEdit = edits;
  migrateProfiles();
  const state = useCadReturnStore.getState();
  const key = currentCadDraftKey();
  const all = drafts();
  let draft = includeDraft && key ? all[key] : undefined;
  if (draft) {
    const settings = state.selectedBundle && parseSolveSettings(draft.settings, state.selectedBundle.sources, 4);
    if (!settings || !draft.options || typeof draft.options !== 'object'
      || !draft.preparation || !['auto', 'full'].includes(draft.preparation.symmetryMode)
      || !Array.isArray(draft.preparation.areaDriftOverrides)
      || draft.preparation.areaDriftOverrides.some(id => typeof id !== 'string')) draft = undefined;
    else draft = { ...draft, settings, options: { ...draft.options, ...normalizePersistedSolveOptions(draft.options) } };
  }
  if (draft?.legacy && saved && key) {
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
export function resetCadProjectSettingsLoad(): void { readGeneration += 1; loading = Promise.resolve(true); heldEdits.length = 0; allowLateDraft = false; }
export function waitForCadProjectSettings(): Promise<boolean> { return loading; }
/** Selection starts this read immediately; ingestion waits before capturing its settings. */
export function loadCadProjectSettings(fetcher: typeof fetch = fetch): Promise<boolean> {
  const request = ++readGeneration;
  const state = useCadReturnStore.getState();
  const bundle = state.selectedBundle;
  const model = state.projectLineageId;
  if (!bundle || !model || bundle.bundleOrigin === 'native' || !bundle.sources.length) return loading = Promise.resolve(true);
  const selection = state.captureSelectionIntent();
  for (let i = heldEdits.length - 1; i >= 0; i -= 1) if (heldEdits[i].selection !== selection) heldEdits.splice(i, 1);
  useCadReturnStore.setState({ setupLoading: true });
  const current = () => request === readGeneration && useCadReturnStore.getState().isCurrentSelectionIntent(selection)
    && useCadReturnStore.getState().projectLineageId === model;
  loading = (async () => {
    try {
      const saved = await readCadInventorySetup(model, bundle.sources.map(({ id, role, required }) => ({ id, role, required })), fetcher);
      if (!current()) return useCadReturnStore.getState().isCurrentSelectionIntent(selection);
      const held = useCadReturnStore.getState();
      const record = held.ingestRecord ?? { sources: bundle.sources.map(source => ({ ...source,
        default_drive_channel_id: source.defaultDriveChannelId })), skipped_source_ids: held.skippedSourceIds,
        mesh_sizes: { source_size_mm: held.sourceSizesMm, rigid_size_mm: held.rigidSizeMm, transition_mm: held.transitionMm },
      } as unknown as CadReturnIngestRecord;
      restoreCadProjectSettings(saved.setup, record);
      useCadReturnStore.setState({ setupLoading: false });
      releaseEdits(selection, true);
      return true;
    } catch (error) {
      if (current()) useCadReturnStore.setState({ setupLoading: false, needsIngest: true,
        ingestStaleReason: `Could not load model settings: ${error instanceof Error ? error.message : String(error)}` });
      if (request === readGeneration) releaseEdits(selection, false);
      return false;
    }
  })();
  return loading;
}
