import { useEffect, useRef, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { CadLinkApiError, getIngest, listReturns, type CadReturnBundle, type CadReturnIngestRecord } from '../../api/cadlink';
import type { CadSetup, JobItem } from '../../api/jobsSocket';
import { cadProjectName, listCadProjects, newestReturnForProject } from '../../api/cadProjects';
import { getDriver } from '../../api/drivers';
import { fromResult, parseWire } from '../../results/crossoverSpec';
import {
  DRIVER_FIELD_KEYS,
  PASSIVE_CARDIOID_DEFAULTS,
  driverBaseFromSpec,
  driversForChannels,
  projectChannelDrivers,
  useCadReturnStore,
  type CadDriveChannel,
  type ChannelDriverForm,
  type DriverBaseUpdate,
  type DriverPreset,
  type PassiveCardioidForm,
} from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, subscribeRevision } from '../../stores/design';
import { useDriverLibraryStore } from '../../stores/driverLibrary';
import { polarUiFromConfig, useSolveOptionsStore, type SymmetryMode } from '../../stores/solveOptions';
import { rememberedCadProject } from '../../stores/cadProjectMemory';
import { preferencesStore } from '../../prefs/preferences';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import type { RefreshOptions } from './arrivals';

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
    return [{ id: channel.id, source_ids: [...channel.source_ids], motion: channel.motion ?? 'normal' }];
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

/** Translate the exact persisted imported request into the editable CAD rail. */
export function cadHistorySetup(job: JobItem, record: CadReturnIngestRecord): CadHistorySetup {
  const setup = job.cad_setup;
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
  const explicitFrequencies = Array.isArray(job.solve_options.frequencies_hz)
    ? job.solve_options.frequencies_hz.filter((value) => finite(value) !== null)
    : [];
  const range = job.solve_options.frequency_range;
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
    frequencyCount: explicitFrequencies.length || Number(job.solve_options.num_frequencies) || 1,
  };
}

function restoreCadJobSolveOptions(job: JobItem): void {
  const options = job.solve_options;
  const explicit = Array.isArray(options.frequencies_hz) && options.frequencies_hz.length > 0
    ? options.frequencies_hz
    : null;
  const polar = polarUiFromConfig(options.polar_config);
  useSolveOptionsStore.setState((state) => ({
    symmetry: ['auto', 'full', 'half_xz', 'half_yz', 'quarter'].includes(options.symmetry)
      ? options.symmetry as SymmetryMode
      : state.symmetry,
    meshValidationMode: ['warn', 'strict', 'off'].includes(options.mesh_validation_mode)
      ? options.mesh_validation_mode
      : state.meshValidationMode,
    verbose: options.verbose,
    frequencySpacing: options.frequency_spacing === 'linear' ? 'linear' : 'log',
    frequencyMode: explicit ? 'list' : 'range',
    frequencyListText: explicit ? explicit.join('\n') : '',
    polar: polar ?? state.polar,
  }));
}

export async function refreshChannelDriverBases(fetcher: typeof fetch = fetch): Promise<string[]> {
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
  return useCadReturnStore.getState().refreshChannelDriverBases(updates);
}

interface CadJobRestoreIntegration {
  enterCadWorkspace(): void;
  reportStatus(message: string): void;
  reportViewportNotice(message: string | null): void;
  showIngestedMesh(
    record: CadReturnIngestRecord,
    name: string,
    onNotice: ((notice: string) => void) | undefined,
    fetcher: typeof fetch,
    generation: number,
  ): Promise<void>;
}

/** Recall the immutable ingestion, saved setup and viewport behind an archived CAD run. */
export async function restoreCadJobModel(
  job: JobItem,
  integration: CadJobRestoreIntegration,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  if (job.config_summary.geometry_type !== 'imported') return false;
  const ingestId = job.cad_source?.ingest_id;
  const displayName = job.cad_source?.document_name || job.label || `run #${job.run_number}`;
  integration.enterCadWorkspace();
  integration.reportViewportNotice(null);
  if (!ingestId) {
    integration.reportStatus(`Cannot show ${displayName}: this CAD run has no ingestion identity.`);
    return false;
  }
  integration.reportStatus(`Loading ${displayName} from run #${job.run_number}…`);
  const selection = useCadReturnStore.getState().beginIngestIntent();
  const viewportGeneration = importedMeshStore.beginIntent();
  const documentLoad = currentDocumentLoad();
  const superseded = () => !useCadReturnStore.getState().isCurrentIngestIntent(selection)
    || !importedMeshStore.isCurrentGeneration(viewportGeneration)
    || !isCurrentDocumentLoad(documentLoad);
  try {
    const record = await getIngest(ingestId, fetcher);
    if (superseded()) return false;
    const bundle: CadReturnBundle = {
      name: `${displayName}.wgreturn`, bundlePath: '', modifiedAt: record.created_at,
      readable: true, documentName: displayName, requestId: null,
      sourceCount: record.sources.length, instanceCount: null,
      sources: record.sources.map((source) => ({
        id: source.id, role: source.role, required: source.required,
        suggestedResolutionMm: source.suggested_resolution_mm,
        defaultDriveChannelId: source.default_drive_channel_id,
      })),
    };
    const savedSetup = cadHistorySetup(job, record);
    const project = record.project?.lineage_id
      ?? useCadReturnStore.getState().projectLineageId
      ?? rememberedCadProject();
    const projectDrivers = projectChannelDrivers(bundle, project);
    useCadReturnStore.setState({
      selectedBundle: { ...bundle, readable: false, reason: 'Recalled from an archived run; the original return bundle is not active.' },
      ingestRecord: record,
      projectLineageId: project,
      ...savedSetup,
      ...(projectDrivers ? { channelDrivers: driversForChannels(projectDrivers, savedSetup.driveChannels) } : {}),
      areaDriftOverrides: [],
      areaDriftSourceIds: [...new Set((record.role_findings ?? [])
        .filter((finding) => String(finding.kind).includes('area-drift'))
        .map((finding) => String(finding.source_id)))],
      needsIngest: false,
      ingestedBundleIdentity: null,
      ingestStaleReason: null,
    });
    restoreCadJobSolveOptions(job);
    const rereadDrivers = await refreshChannelDriverBases(fetcher);
    if (superseded()) return false;
    await integration.showIngestedMesh(record, displayName, integration.reportViewportNotice, fetcher, viewportGeneration);
    if (superseded()) return false;
    if (importedMeshStore.getSnapshot().cad?.ingestId !== ingestId) {
      integration.reportStatus(`Cannot show ${displayName}: the archived CAD mesh artifacts are no longer available.`);
      return false;
    }
    integration.reportStatus(`Showing ${displayName} from run #${job.run_number}.${
      rereadDrivers.length ? ` Re-read ${rereadDrivers.length} driver${rereadDrivers.length === 1 ? '' : 's'} from the library.` : ''
    }`);
    return true;
  } catch (reason) {
    if (superseded()) return false;
    const missing = reason instanceof CadLinkApiError && reason.status === 404;
    integration.reportStatus(missing
      ? `Cannot show ${displayName}: the archived CAD ingestion and mesh artifacts are no longer available.`
      : `Could not show ${displayName}: ${reason instanceof Error ? reason.message : String(reason)}`);
    return false;
  }
}

interface UseCadRestoresOptions {
  enterCadWorkspace(): void;
  manualSelectionAt: MutableRefObject<number | null>;
  projectOpenPending: MutableRefObject<boolean>;
  refreshRef: MutableRefObject<(options?: RefreshOptions) => Promise<void>>;
  refusedForeignReturn: MutableRefObject<boolean>;
  returnListRequest: MutableRefObject<number>;
  seenReturnRevisions: MutableRefObject<Map<string, string> | null>;
  selectBundleRef: MutableRefObject<(bundle: CadReturnBundle, projectLineageId?: string | null) => void>;
  setError: Dispatch<SetStateAction<string | null>>;
  setStatus: Dispatch<SetStateAction<string | null>>;
}

/** Own project-load reactions and remembered-project reopening. */
export function useCadRestores({
  enterCadWorkspace,
  manualSelectionAt,
  projectOpenPending,
  refreshRef,
  refusedForeignReturn,
  returnListRequest,
  seenReturnRevisions,
  selectBundleRef,
  setError,
  setStatus,
}: UseCadRestoresOptions) {
  const restoringCadProject = useRef(false);

  useEffect(() => subscribeRevision((event) => {
    if (event.reason !== 'load') return;
    if (event.loadSource === 'cad-project-switch') {
      projectOpenPending.current = true;
      manualSelectionAt.current = null;
      refusedForeignReturn.current = false;
      seenReturnRevisions.current = null;
      returnListRequest.current += 1;
      useCadReturnStore.getState().selectBundle(null, null);
      importedMeshStore.beginIntent();
      importedMeshStore.clear('cad');
      setError(null);
      setStatus('Project design loaded. Looking for its latest CAD return…');
      enterCadWorkspace();
      queueMicrotask(() => { void refreshRef.current(); });
      return;
    }
    projectOpenPending.current = false;
    workspaceModeStore.setMode('parametric');
    useCadReturnStore.getState().markIngestStale(
      'The design was replaced after this CAD return was ingested. Re-ingest before solving.',
    );
  }), [enterCadWorkspace, manualSelectionAt, projectOpenPending, refreshRef, refusedForeignReturn,
    returnListRequest, seenReturnRevisions, setError, setStatus]);

  useEffect(() => {
    const maybeRestore = () => {
      if (workspaceModeStore.getSnapshot().mode !== 'cad') return;
      if (preferencesStore.getSnapshot().cadApplication === 'onshape') return;
      if (restoringCadProject.current || refusedForeignReturn.current) return;
      const lineage = rememberedCadProject();
      if (!lineage) return;
      const current = useCadReturnStore.getState();
      if (current.selectedBundle || current.ingestRecord) return;
      restoringCadProject.current = true;
      void (async () => {
        try {
          const [projects, returns] = await Promise.all([listCadProjects(), listReturns()]);
          const project = projects.find((item) => item.lineageId === lineage);
          if (!project) return;
          const bundle = newestReturnForProject(returns.items, project);
          if (!bundle) return;
          const latest = useCadReturnStore.getState();
          if (latest.selectedBundle || latest.ingestRecord) return;
          if (workspaceModeStore.getSnapshot().mode !== 'cad') return;
          selectBundleRef.current(bundle, project.lineageId);
          setStatus(`Reopened ${cadProjectName(project)}.`);
        } catch {
          // Restoring is a convenience; empty-mode guidance remains honest.
        } finally {
          restoringCadProject.current = false;
        }
      })();
    };
    maybeRestore();
    return workspaceModeStore.subscribe(maybeRestore);
  }, [refusedForeignReturn, selectBundleRef, setStatus]);

  return { restoringCadProject };
}
