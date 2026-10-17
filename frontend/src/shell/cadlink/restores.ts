import { parseExteriorTransducer } from '../../stores/exteriorTransducer';
import { useEffect, useRef, type MutableRefObject } from 'react';
import { CadLinkApiError, getIngest, listReturns, type CadReturnBundle, type CadReturnIngestRecord } from '../../api/cadlink';
import type { CadSetup } from '../../api/jobsSocket';
import { cadProjectName, listCadProjects, newestReturnForProject } from '../../api/cadProjects';
import { getDriver } from '../../api/drivers';
import { fromResult, parseWire } from '../../results/crossoverSpec';
import {
  DRIVER_FIELD_KEYS,
  bundleIdentity,
  PASSIVE_CARDIOID_DEFAULTS,
  driverBaseFromSpec,
  useCadReturnStore,
  type CadDriveChannel,
  type ChannelDriverForm,
  type DriverBaseUpdate,
  type DriverPreset,
  type PassiveCardioidForm,
} from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, subscribeRevision } from '../../stores/design';
import { useDriverLibraryStore } from '../../stores/driverLibrary';
import { applyWireOptionsToImportedSolveOptions } from '../../stores/solveOptions';
import { rememberedCadProject } from '../../stores/cadProjectMemory';
import { preferencesStore } from '../../prefs/preferences';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { createCadArrivalActions, enterCadWorkspace, type RefreshOptions } from './arrivals';
import { showIngestedMeshInViewport } from './artifactLoader';
import { isCurrentOpen, registerImportedView, type ImportedView } from '../../stores/importedModel';
import type { ImportedSetup } from '../../jobs/importedSetup';

type CadHistorySetup = Pick<ReturnType<typeof useCadReturnStore.getState>,
  'sourceSizesMm' | 'rigidSizeMm' | 'transitionMm' | 'skippedSourceIds'
  | 'driveChannels' | 'exteriorOnly' | 'combineEnabled' | 'combineSpec'
  | 'channelDrivers' | 'passiveCardioid'
  | 'driveVoltageV' | 'maxDriveVoltageV'
  | 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>;

function object(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function finiteRecord(value: unknown): Record<string, number> | null {
  const record = object(value);
  if (!record) return null;
  const entries = Object.entries(record);
  if (entries.some(([key, item]) => !key || finite(item) === null)) return null;
  return Object.fromEntries(entries) as Record<string, number>;
}

function savedDriveChannels(setup: CadSetup | null | undefined, record: CadReturnIngestRecord): CadDriveChannel[] {
  const knownSources = new Set(record.sources.map((source) => source.id));
  const raw = setup?.drive_channels;
  if (!Array.isArray(raw) || raw.length === 0) return [];
  const assigned = new Set<string>();
  const channels = raw.flatMap((channel): CadDriveChannel[] => {
    if (!channel || typeof channel.id !== 'string' || !channel.id.trim()
      || !Array.isArray(channel.source_ids) || channel.source_ids.length === 0
      || (channel.motion !== undefined && channel.motion !== 'normal' && channel.motion !== 'axial')
      || channel.source_ids.some((id) => (
        typeof id !== 'string' || !knownSources.has(id) || assigned.has(id)
      ))) return [];
    channel.source_ids.forEach((id) => assigned.add(id));
    return [{ id: channel.id, source_ids: [...channel.source_ids], motion: channel.motion ?? 'normal',
      ...(channel.exterior_transducer !== undefined ? { exterior_transducer: parseExteriorTransducer(channel.exterior_transducer) } : {}) }];
  });
  return channels.length === raw.length ? channels : [];
}

function savedChannelDrivers(
  setup: CadSetup | null | undefined,
  channels: CadDriveChannel[],
): Record<string, ChannelDriverForm> {
  const rawById = new Map((setup?.drive_channels ?? []).map((channel) => [channel.id, channel]));
  return Object.fromEntries(channels.flatMap((channel): Array<[string, ChannelDriverForm]> => {
    const driver = object(rawById.get(channel.id)?.driver);
    if (!driver) return [];
    const fields = Object.fromEntries(DRIVER_FIELD_KEYS.flatMap((key) => {
      const value = finite(driver[key]);
      return value === null ? [] : [[key, value]];
    }));
    const label = typeof driver.label === 'string' && driver.label.trim() ? driver.label.trim() : null;
    const preset: DriverPreset | null = label
      ? { id: `manual:${channel.id}`, label, source: 'manual', kind: 'unknown', z_ohm: null, xo_min_hz: null, base: fields }
      : null;
    return [[channel.id, { fields: preset ? {} : fields, preset }]];
  }));
}

function savedPassiveCardioid(setup: CadSetup | null | undefined): PassiveCardioidForm {
  const rearVolumeL = finite(setup?.passive_cardioid_rear_volume_l);
  if (rearVolumeL === null) return { ...PASSIVE_CARDIOID_DEFAULTS };
  const portAreaSource = setup?.port_area_source === 'bem_aperture' ? 'bem_aperture' : 'user';
  return {
    enabled: true,
    rearVolumeL,
    portLengthMm: finite(setup?.passive_cardioid_port_length_mm),
    modelPortAreaM2: finite(setup?.model_port_area_m2),
    bemPortAreaM2: finite(setup?.bem_port_area_m2),
    portAreaSource,
    foamResistancePaSM3: finite(setup?.passive_cardioid_foam_resistance_pa_s_m3),
    invertPort: setup?.passive_cardioid_invert_port !== false,
    coupled: setup?.passive_cardioid_coupled === true,
  };
}

/** Translate a setup (a run's or a model's) into the editable CAD rail. */
export function cadHistorySetup(saved: ImportedSetup, record: CadReturnIngestRecord): CadHistorySetup {
  const setup = saved.geometry as unknown as CadSetup;
  const options = saved.options as unknown as Record<string, unknown>;
  const mesh = object(setup?.mesh);
  const channels = savedDriveChannels(setup, record);
  const skippedSourceIds = Array.isArray(setup?.skipped_source_ids)
    && setup.skipped_source_ids.every((id) => typeof id === 'string')
    ? [...setup.skipped_source_ids]
    : [...record.skipped_source_ids];
  const fallbackChannels = (() => {
    const skipped = new Set(skippedSourceIds);
    const grouped = new Map<string, CadDriveChannel>();
    record.sources.filter((source) => !skipped.has(source.id)).forEach((source) => {
      const channel = grouped.get(source.default_drive_channel_id) ?? {
        id: source.default_drive_channel_id,
        source_ids: [],
        motion: 'normal' as const,
      };
      channel.source_ids.push(source.id);
      grouped.set(channel.id, channel);
    });
    return [...grouped.values()];
  })();
  const driveChannels = channels.length ? channels : fallbackChannels;
  const combine = object(setup?.combine);
  const combineSpec = parseWire(combine) ?? fromResult(combine ?? undefined);
  const validCombine = combineSpec !== null;
  const explicitFrequencies = Array.isArray(options.frequencies_hz)
    ? (options.frequencies_hz as unknown[]).filter((value): value is number => finite(value) !== null)
    : [];
  const range = options.frequency_range;
  const fallbackRange = Array.isArray(range) && range.length === 2 ? range.map(Number) : [200, 20_000];
  const sourceSizes = finiteRecord(mesh?.source_size_mm) ?? { ...record.mesh_sizes.source_size_mm };
  return {
    sourceSizesMm: sourceSizes,
    rigidSizeMm: finite(mesh?.rigid_size_mm) ?? record.mesh_sizes.rigid_size_mm,
    transitionMm: finite(mesh?.transition_mm) ?? record.mesh_sizes.transition_mm,
    skippedSourceIds,
    driveChannels,
    exteriorOnly: typeof setup?.exterior_only === 'boolean' ? setup.exterior_only : false,
    combineEnabled: setup ? validCombine : null,
    combineSpec,
    channelDrivers: savedChannelDrivers(setup, driveChannels),
    passiveCardioid: savedPassiveCardioid(setup),
    driveVoltageV: finite(setup?.drive_voltage_v) ?? 2.83,
    maxDriveVoltageV: finite(setup?.max_drive_voltage_v) ?? null,
    frequencyStartHz: explicitFrequencies[0] ?? fallbackRange[0],
    frequencyEndHz: explicitFrequencies.at(-1) ?? fallbackRange[1],
    frequencyCount: explicitFrequencies.length || Number(options.num_frequencies) || 1,
  };
}

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
        if (!opened.setup) throw new Error('This run’s settings are no longer retained. Select the run again.');
        const record = await getIngest(opened.prepared.ingest_id, fetcher).catch((reason: unknown) => {
          throw reason instanceof CadLinkApiError && reason.status === 404
            ? new Error('The archived CAD ingestion and mesh artifacts are no longer available.') : reason;
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
          ...cadHistorySetup(opened.setup, record),
          areaDriftOverrides: [],
          areaDriftSourceIds: [...new Set((record.role_findings ?? [])
            .filter(finding => String(finding.kind).includes('area-drift')).map(finding => String(finding.source_id)))],
          needsIngest: false, ingestedBundleIdentity: null, ingestStaleReason: null,
        });
        // The model's options go to the imported instance only; the persisted
        // parametric store is never written by an imported flow.
        applyWireOptionsToImportedSolveOptions(opened.setup.options);
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

/** Own project-load reactions and remembered-project reopening. */
export function useCadRestores({
  enterCadWorkspace,
  manualSelectionAt,
  projectOpenPending,
  initialChoiceOwed,
  refreshRef,
  refusedForeignReturn,
  returnListRequest,
  seenReturnRevisions,
  selectBundleRef,
  setError,
  setStatus,
}: UseCadRestoresOptions) {
  const restoringCadProject = useRef(false);

  useEffect(() => observeCadProjectLoads({
    enterCadWorkspace, manualSelectionAt, projectOpenPending, initialChoiceOwed, refreshRef, refusedForeignReturn, returnListRequest, seenReturnRevisions, setError, setStatus, selectBundleRef,
  }), [enterCadWorkspace, manualSelectionAt, projectOpenPending, initialChoiceOwed, refreshRef, refusedForeignReturn,
    returnListRequest, seenReturnRevisions, setError, setStatus]);

  useEffect(() => observeRememberedCadProject({
    restoringCadProject, refusedForeignReturn,
    selectBundle: (bundle, project) => selectBundleRef.current(bundle, project), reportStatus: setStatus,
  }), [refusedForeignReturn, selectBundleRef, setStatus]);

  return { restoringCadProject };
}

export interface RememberedCadProjectIntegration {
  selectBundle(bundle: CadReturnBundle, project: string): void;
  reportStatus(message: string): void;
  isAllowed(): boolean;
}

/** Reopen only the empty CAD mode that initiated this read. Captured intent
 * also fences a pick followed by a clear while the listing is delayed. */
export async function restoreRememberedCadProject(
  overrides: Partial<RememberedCadProjectIntegration> = {},
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  const integration: RememberedCadProjectIntegration = {
    selectBundle: (bundle, project) => createCadArrivalActions({}, fetcher).selectBundleAs(bundle, project, true),
    reportStatus: () => undefined, isAllowed: () => true, ...overrides,
  };
  const lineage = rememberedCadProject();
  const selection = useCadReturnStore.getState().captureIngestIntent();
  const documentLoad = currentDocumentLoad();
  const empty = () => {
    const current = useCadReturnStore.getState();
    return !current.selectedBundle && !current.ingestRecord
      && current.isCurrentIngestIntent(selection) && isCurrentDocumentLoad(documentLoad)
      && workspaceModeStore.getSnapshot().mode === 'cad' && workspaceModeStore.getSnapshot().importedInput === 'cad'
      && preferencesStore.getSnapshot().cadApplication !== 'onshape' && integration.isAllowed();
  };
  if (!lineage || !empty()) return false;
  try {
    const [projects, returns] = await Promise.all([listCadProjects(fetcher), listReturns(fetcher)]);
    if (!empty()) return false;
    const project = projects.find((item) => item.lineageId === lineage);
    if (!project) return false;
    const bundle = newestReturnForProject(returns.items, project);
    if (!bundle) return false;
    integration.selectBundle(bundle, project.lineageId);
    integration.reportStatus(`Reopened ${cadProjectName(project)}.`);
    return true;
  } catch {
    // Remembered reopening is advisory; guidance for the empty mode remains.
    return false;
  }
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
      if (integration.initialChoiceOwed) integration.initialChoiceOwed.current = true;
      manualSelectionAt.current = null;
      refusedForeignReturn.current = false;
      seenReturnRevisions.current = null;
      returnListRequest.current += 1;
      useCadReturnStore.getState().selectBundle(null, null);
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
      'The design was replaced after this CAD return was ingested. Re-ingest before solving.',
    );
  });
}

export function observeRememberedCadProject({
  restoringCadProject = { current: false }, refusedForeignReturn = { current: false },
  selectBundle = (bundle, project) => createCadArrivalActions().selectBundleAs(bundle, project, true),
  reportStatus = () => undefined,
}: Partial<Pick<RememberedCadProjectIntegration, 'selectBundle' | 'reportStatus'>> & {
  restoringCadProject?: { current: boolean };
  refusedForeignReturn?: { current: boolean };
} = {}): () => void {
  let active = true;
  const maybeRestore = () => {
    if (workspaceModeStore.getSnapshot().mode !== 'cad' || workspaceModeStore.getSnapshot().importedInput !== 'cad') return;
    if (preferencesStore.getSnapshot().cadApplication === 'onshape') return;
    if (restoringCadProject.current || refusedForeignReturn.current) return;
    const lineage = rememberedCadProject();
    if (!lineage) return;
    const current = useCadReturnStore.getState();
    if (current.selectedBundle || current.ingestRecord) return;
    restoringCadProject.current = true;
    void restoreRememberedCadProject({
      selectBundle: (bundle, project) => selectBundle(bundle, project),
      reportStatus,
      isAllowed: () => active && !refusedForeignReturn.current,
    }).finally(() => { restoringCadProject.current = false; });
  };
  maybeRestore();
  const stop = workspaceModeStore.subscribe(maybeRestore);
  return () => { active = false; stop(); };
}
