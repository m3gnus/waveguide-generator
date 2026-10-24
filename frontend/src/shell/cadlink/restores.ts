import type { MutableRefObject } from 'react';
import { CadLinkApiError, getIngest, listReturns, type CadReturnBundle } from '../../api/cadlink';
import { getDriver } from '../../api/drivers';
import {
  bundleIdentity,
  driverBaseFromSpec,
  useCadReturnStore,
  type DriverBaseUpdate,
} from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, subscribeRevision } from '../../stores/design';
import { useDriverLibraryStore } from '../../stores/driverLibrary';
import { durableSettings } from '../../stores/durableSettings';
import type { LastModel } from '../../stores/lastModel';
import { preferencesStore } from '../../prefs/preferences';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { cadProjectName, listCadProjects, newestReturnForProject } from '../../api/cadProjects';
import { enterCadWorkspace, type RefreshOptions } from './arrivals';
import { showIngestedMeshInViewport } from './artifactLoader';
import { isCurrentOpen, registerImportedView, reopenLastModel, type ImportedView } from '../../stores/importedModel';
import { restoreCadProjectSettings, resetCadProjectSettingsLoad, hasCadDraftForModel, migrateCadProfiles } from '../../stores/cadProjectSettings';
import { cadHistorySetup } from '../../stores/cadSetupRestore';
import { seedImportedSolveOptionsFromParametric } from '../../stores/solveOptions';
import { DEFAULT_SWEEP } from '../../stores/solveDefaults';
export { cadHistorySetup } from '../../stores/cadSetupRestore';

export async function refreshChannelDriverBases(fetcher: typeof fetch = fetch): Promise<string[]> {
  const owner = useCadReturnStore.getState();
  const selection = owner.captureSelectionIntent();
  const bundle = owner.selectedBundle ? bundleIdentity(owner.selectedBundle) : null;
  const documentLoad = currentDocumentLoad();
  const forms = useCadReturnStore.getState().channelDrivers;
  const saved = new Map(useDriverLibraryStore.getState().saved.map((driver) => [driver.id, driver]));
  const updates: Record<string, DriverBaseUpdate> = {};
  await Promise.all(Object.entries(forms).map(async ([channelId, form]) => {
    const preset = form.preset;
    if (!preset || preset.source === 'manual') return;
    if (preset.source === 'mine') {
      const driver = saved.get(preset.id);
      if (driver) updates[channelId] = { presetId: preset.id, base: { ...driver.base, ...driver.overrides }, xo_min_hz: driver.xo_min_hz };
      return;
    }
    const hit = await getDriver(preset.id, fetcher).catch(() => null);
    if (!hit) return;
    updates[channelId] = {
      presetId: preset.id,
      base: driverBaseFromSpec(hit.spec),
      xo_min_hz: typeof hit.xo_min_hz === 'number' && Number.isFinite(hit.xo_min_hz) ? hit.xo_min_hz : null,
    };
  }));
  const current = useCadReturnStore.getState();
  if (!current.isCurrentSelectionIntent(selection)
    || current.projectLineageId !== owner.projectLineageId
    || (current.selectedBundle ? bundleIdentity(current.selectedBundle) : null) !== bundle
    || !isCurrentDocumentLoad(documentLoad)) return [];
  return useCadReturnStore.getState().refreshChannelDriverBases(updates);
}

/**
 * The CAD rail as a view of the imported-model store: it shows a model
 * `openImported` reopened (a run or a model) -- its retained ingestion, its
 * setup on the rail and its options, and its mesh in the viewport. The
 * project is the model the server named, never one guessed from what is open.
 */
export const cadImportedView: ImportedView = {
  hasDraft: hasCadDraftForModel,
  begin({ generation, source, display, fetcher }) {
    const job = source.kind === 'run' ? source.job : null;
    const displayName = job ? job.cad_source?.document_name || job.label || `run #${job.run_number}` : 'this model';
    const from = job ? ` from run #${job.run_number}` : '';
    const enter = display.enterCadWorkspace ?? enterCadWorkspace;
    const reportStatus = display.reportStatus ?? (() => undefined);
    const reportViewportNotice = display.reportViewportNotice ?? (() => undefined);
    const showIngestedMesh = display.showIngestedMesh ?? showIngestedMeshInViewport;
    enter();
    reportViewportNotice(null);
    resetCadProjectSettingsLoad();
    useCadReturnStore.getState().beginSelectionIntent();
    const selection = useCadReturnStore.getState().beginIngestIntent();
    const documentLoad = currentDocumentLoad();
    // A new target owns Solve before any request can fail or stall.
    useCadReturnStore.setState({ ingestRecord: null, needsIngest: true,
      ingestStaleReason: 'Loading the selected run’s retained setup.' });
    reportStatus(`Loading ${displayName}${from}…`);
    // The open's own generation fences this too: an open of the other input
    // (a file) never touches CAD's ingest intent, and must still win.
    const current = () => isCurrentOpen(generation)
      && useCadReturnStore.getState().isCurrentIngestIntent(selection) && isCurrentDocumentLoad(documentLoad);
    return {
      current,
      async fill(opened) {
        if (!opened.setup && source.kind !== 'model') throw new Error('This run’s settings are no longer retained. Select the run again.');
        const record = await getIngest(opened.prepared.ingest_id, fetcher).catch((reason: unknown) => {
          throw reason instanceof CadLinkApiError && reason.status === 404
            ? new Error('The archived CAD model and mesh are no longer available.') : reason;
        });
        if (!current()) return null;
        if (record.ingest_id !== opened.prepared.ingest_id || record.manifest_sha256 !== opened.prepared.manifest_sha256
          || record.artifact_sha256 !== opened.prepared.artifact_sha256) {
          throw new Error('This run’s saved request does not match its retained mesh. Select the run again.');
        }
        const name = job ? displayName : record.project?.document_name || opened.model?.display_name || displayName;
        const bundle: CadReturnBundle = {
          name: `${name}.wgreturn`, bundlePath: '', modifiedAt: record.created_at,
          readable: false, reason: 'Recalled from an archived run; the original return bundle is not active.',
          documentName: name, requestId: null, sourceCount: record.sources.length, instanceCount: null,
          sources: record.sources.map(item => ({ id: item.id, role: item.role, required: item.required,
            suggestedResolutionMm: item.suggested_resolution_mm, defaultDriveChannelId: item.default_drive_channel_id })),
        };
        const project = opened.modelId !== 'unfiled' ? opened.modelId : record.project?.lineage_id ?? null;
        useCadReturnStore.getState().restoreIngest(record, selection, {
          selectedBundle: bundle, ingestRecord: record, projectLineageId: project,
          ...(!opened.setup ? cadHistorySetup({ geometry: {}, options: {
            frequency_range: [DEFAULT_SWEEP.startHz, DEFAULT_SWEEP.endHz], num_frequencies: DEFAULT_SWEEP.points,
          } } as never, record) : {}),
          areaDriftOverrides: [],
          areaDriftSourceIds: [...new Set((record.role_findings ?? [])
            .filter(finding => String(finding.kind).includes('area-drift')).map(finding => String(finding.source_id)))],
          needsIngest: false, ingestedBundleIdentity: null, ingestStaleReason: null,
        });
        // The model's options go to the imported instance only; the persisted
        // parametric store is never written by an imported flow.
        if (!opened.setup) seedImportedSolveOptionsFromParametric();
        await migrateCadProfiles(fetcher);
        if (!current()) return null;
        const draft = restoreCadProjectSettings(opened.setup, record, source.kind === 'model', opened.revisionId);
        if (draft) { opened.setup = null; opened.revisionId = null; }
        await showIngestedMesh(record, name, reportViewportNotice, fetcher, generation);
        if (!current()) return null;
        if (importedMeshStore.getSnapshot().cad?.ingestId !== record.ingest_id) return null;
        reportStatus(`Showing ${name}${from}. ${opened.note ?? 'Stored solve settings restored.'}`);
        return record;
      },
      fail(message) {
        if (!current()) return;
        useCadReturnStore.setState({ ingestRecord: null, needsIngest: true, ingestStaleReason: message });
        reportStatus(`Cannot show ${displayName}: ${message}`);
      },
    };
  },
};
registerImportedView('cad', cadImportedView);

export interface UseCadRestoresOptions {
  enterCadWorkspace(): void;
  manualSelectionAt: MutableRefObject<number | null>;
  projectOpenPending: MutableRefObject<boolean>;
  initialChoiceOwed?: MutableRefObject<boolean>;
  refreshRef: MutableRefObject<(options?: RefreshOptions) => Promise<void>>;
  refusedForeignReturn: MutableRefObject<boolean>;
  returnListRequest: MutableRefObject<number>;
  seenReturnRevisions: MutableRefObject<Map<string, string> | null>;
  selectBundleRef: MutableRefObject<(bundle: CadReturnBundle, projectLineageId?: string | null) => void>;
  setError(value: string | null): void;
  setStatus(value: string | null): void;
}

export function observeCadProjectLoads(integration: UseCadRestoresOptions): () => void {
  const {
    enterCadWorkspace, manualSelectionAt, projectOpenPending, refreshRef, refusedForeignReturn,
    returnListRequest, seenReturnRevisions, setError, setStatus,
  } = integration;
  return subscribeRevision((event) => {
    if (event.reason !== 'load') return;
    // A crash-recovery restore is the same document returning, not a new one:
    // it must not pull the user out of CAD mode or stale their ingest.
    if (event.loadSource === 'autosave-restore') return;
    if (event.loadSource === 'cad-project-switch') {
      projectOpenPending.current = true;
      manualSelectionAt.current = null;
      refusedForeignReturn.current = false;
      seenReturnRevisions.current = null;
      returnListRequest.current += 1;
      useCadReturnStore.getState().selectBundle(null, null);
      // Clearing the previous model also resets its solve options. Subscribers
      // can still see that model during the reset, so owe the new choice after it.
      if (integration.initialChoiceOwed) integration.initialChoiceOwed.current = true;
      importedMeshStore.beginIntent();
      importedMeshStore.clear('cad');
      setError(null);
      setStatus('Project design loaded. Looking for its latest CAD return…');
      workspaceModeStore.setImportedInput('cad');
      enterCadWorkspace();
      queueMicrotask(() => { void refreshRef.current(); });
      return;
    }
    projectOpenPending.current = false;
    workspaceModeStore.setMode('parametric');
    useCadReturnStore.getState().markIngestStale(
      'The design was replaced after this CAD return was prepared. Prepare it again before solving.',
    );
  });
}

/**
 * Reopen the last model whenever Imported mode is showing and empty: at start,
 * and again when the person switches into it. It never changes the mode, so
 * Parametric opens nothing. It stands down while an open is under way, a
 * foreign return was refused, a `?job=` link is still to be opened, or the
 * view is no longer empty, and it tries each pointer at most once per session.
 * A pointer that only reaches this browser later (the settings server answered
 * after the start-up wait) is tried when it arrives.
 */
export function observeLastModel({
  reportStatus = () => undefined, reportError = () => undefined, fetcher = fetch, selectBundle,
  refusedForeignReturn = { current: false }, jobLinkPending = () => false, settings = durableSettings,
}: {
  reportStatus?(message: string): void;
  reportError?(message: string): void;
  fetcher?: typeof fetch;
  /** The CAD workspace owners' selection, which ingests the return and shows its refusals. A fresh one would do neither. */
  selectBundle?(bundle: CadReturnBundle, project: string): void;
  refusedForeignReturn?: { current: boolean };
  jobLinkPending?(): boolean;
  /** Where a late-hydrated pointer is announced; the shared instance unless a test brings its own. */
  settings?: Pick<typeof durableSettings, 'subscribe'>;
} = {}): () => void {
  let active = true;
  let running = false;
  const tried = new Set<string>();
  const blocked = () => !active || refusedForeignReturn.current || jobLinkPending();
  const openUnsolved = async (pointer: LastModel): Promise<boolean> => {
    if (!selectBundle || preferencesStore.getSnapshot().cadApplication === 'onshape') return false;
    const selection = useCadReturnStore.getState().captureIngestIntent();
    const documentLoad = currentDocumentLoad();
    const empty = () => {
      const current = useCadReturnStore.getState();
      return !current.selectedBundle && !current.ingestRecord && current.isCurrentIngestIntent(selection)
        && isCurrentDocumentLoad(documentLoad) && !blocked() && workspaceModeStore.getSnapshot().mode === 'cad';
    };
    const [projects, returns] = await Promise.all([listCadProjects(fetcher), listReturns(fetcher)]);
    if (!empty()) return false;
    const project = projects.find((item) => item.lineageId === pointer.model_id);
    const bundle = project ? newestReturnForProject(returns.items, project) : null;
    if (!project || !bundle) {
      reportStatus('The last CAD project has no return to show yet. Pull one from CAD.');
      return false;
    }
    // Never solved: no setup to restore, so the return opens on defaults like any new selection.
    selectBundle(bundle, project.lineageId);
    // A foreign return is refused by the selection itself, which shows why.
    if (refusedForeignReturn.current) return false;
    reportStatus(`Reopened ${cadProjectName(project)}.`);
    return true;
  };
  const attempt = () => {
    if (running || blocked() || workspaceModeStore.getSnapshot().mode !== 'cad') return;
    running = true;
    void reopenLastModel(fetcher, {
      shouldTry: (pointer) => {
        const key = `${pointer.input}:${pointer.model_id}`;
        if (blocked() || tried.has(key)) return false;
        tried.add(key);
        return true;
      },
      openUnsolved,
    })
      .then((opened) => { if (opened && active) reportStatus('Reopened the last model.'); })
      .catch((error: unknown) => { if (active) reportError(error instanceof Error ? error.message : String(error)); })
      .finally(() => { running = false; });
  };
  attempt();
  const stops = [
    workspaceModeStore.subscribe(attempt),
    ...(['lastModel', 'meshImport', 'cadProject'] as const).map((namespace) => settings.subscribe(namespace, attempt)),
  ];
  return () => { active = false; stops.forEach((stop) => stop()); };
}
