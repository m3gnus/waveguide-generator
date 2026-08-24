import { useCallback, useEffect, useRef, useState } from 'react';
import {
  CadLinkApiError,
  getFusionCadStatus,
  getIngest,
  ingestReturn,
  getSolveCommand,
  listReturns,
  reportSolveCommandOutcome,
  requestFusionReturn,
  type CadReturnBundle,
  type CadReturnIngestRecord,
  type FusionCadStatus,
} from '../api/cadlink';
import type { CadSetup, JobItem } from '../api/jobsSocket';
import { sendDesignToCad, type WgLinkExportResponse } from '../api/designIo';
import { getOnshapeConnection, getOnshapeStatus, returnOnshapeToWg, type OnshapeConnection, type OnshapeStatus } from '../api/onshape';
import { fromResult, parseWire } from '../results/crossoverSpec';
import { preferencesStore, usePreferences } from '../prefs/preferences';
import { getDriver } from '../api/drivers';
import { useCadPreparationStore } from '../stores/cadPreparation';
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
} from '../stores/cadReturn';
import { recordCommittedAthPolars, subscribeRevision, useDesignStore } from '../stores/design';
import { useDriverLibraryStore } from '../stores/driverLibrary';
import { useDocumentStore, type DesignIdentity } from '../stores/document';
import { documentSettingsSignature } from '../stores/designWire';
import {
  parkedSolveCommandStore,
  refuseParkedSolveCommand,
} from '../stores/solveCommand';
import {
  polarConfigFromUi,
  polarUiFromConfig,
  useSolveOptionsStore,
  type SymmetryMode,
} from '../stores/solveOptions';
import { rememberCadProject, rememberedCadProject } from '../stores/cadProjectMemory';
import { cadProjectName, listCadProjects, newestReturnForProject } from '../api/cadProjects';
import { workspaceModeStore } from '../stores/workspaceMode';
import { createImportedMeshScene } from '../viewport/importedMesh';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { parseMSH } from '../viewport/mshParser';
import { designNameSlug } from '../stores/designName';
import { fusionWorkflowView } from './cadWorkflowView';
import { jobsCoordinatorBridge, SolveEngineUnavailableError } from './JobsCoordinator';
import { workspaceNavigation } from './workspaceNavigation';
import { useModalDialogFocus } from './dialogFocus';

interface RefreshOptions {
  background?: boolean;
  autoOpenNew?: boolean;
}

interface CadLinkCoordinatorSnapshot {
  bundles: CadReturnBundle[];
  loading: boolean;
  ingesting: boolean;
  ingestError: string | null;
  sendingToFusion: boolean;
  pullingFromFusion: boolean;
  error: string | null;
  status: string | null;
  viewportNotice: string | null;
  fusionStatus: FusionCadStatus | null;
  onshapeStatus: OnshapeStatus | null;
  onshapeConnection: OnshapeConnection | null;
  pendingFusionConflict: boolean;
  refresh(options?: RefreshOptions): Promise<void>;
  refreshOnshapeStatus(committed?: DesignIdentity): Promise<void>;
  returnFromOnshape(): Promise<void>;
  selectBundle(bundle: CadReturnBundle, projectLineageId?: string | null): void;
  ingest(): Promise<void>;
  ingestSelected(): Promise<CadReturnIngestRecord>;
  pullFromFusion(): Promise<CadReturnBundle>;
  pullAndSolve(): Promise<'solving' | 'blocked' | 'failed'>;
  /** Start the parked Fusion solve request; blockers are re-reported into it. */
  solveParkedCommand(): Promise<void>;
  /** Refuse the parked Fusion solve request for good. */
  dismissSolveCommand(): Promise<void>;
  /** The one Fusion outbound path: derives open-vs-update and the expected
   * document guard from the live status, and parks on the two-way conflict
   * (returning null) until the user confirms through the coordinator dialog. */
  sendWgToFusion(options?: { confirmed?: boolean }): Promise<WgLinkExportResponse | null>;
  cancelFusionConflict(): void;
  clearFeedback(): void;
  reportError(message: string): void;
  reportStatus(message: string): void;
  reportViewportNotice(message: string | null): void;
  selectFusionInstance(instanceId: string): void;
  selectOnshapeInstance(instanceId: string): void;
}

const unavailable = async () => { throw new Error('CAD Link coordinator is unavailable'); };
const unavailableRefreshOnshape = async (_committed?: DesignIdentity) => unavailable();
let bridgeSnapshot: CadLinkCoordinatorSnapshot = {
  bundles: [],
  loading: true,
  ingesting: false,
  ingestError: null,
  sendingToFusion: false,
  pullingFromFusion: false,
  error: null,
  status: null,
  viewportNotice: null,
  fusionStatus: null,
  onshapeStatus: null,
  onshapeConnection: null,
  pendingFusionConflict: false,
  refresh: unavailable,
  refreshOnshapeStatus: unavailableRefreshOnshape,
  returnFromOnshape: unavailable,
  selectBundle: () => undefined,
  ingest: unavailable,
  ingestSelected: unavailable,
  pullFromFusion: unavailable,
  pullAndSolve: unavailable,
  solveParkedCommand: unavailable,
  dismissSolveCommand: unavailable,
  sendWgToFusion: unavailable,
  cancelFusionConflict: () => undefined,
  clearFeedback: () => undefined,
  reportError: () => undefined,
  reportStatus: () => undefined,
  reportViewportNotice: () => undefined,
  selectFusionInstance: () => undefined,
  selectOnshapeInstance: () => undefined,
};
const bridgeListeners = new Set<() => void>();

function pageIsVisible(): boolean {
  return document.visibilityState !== 'hidden';
}

export const cadLinkCoordinatorBridge = {
  getSnapshot: () => bridgeSnapshot,
  subscribe(listener: () => void) {
    bridgeListeners.add(listener);
    return () => bridgeListeners.delete(listener);
  },
};

function publishBridge(snapshot: CadLinkCoordinatorSnapshot): void {
  bridgeSnapshot = snapshot;
  bridgeListeners.forEach((listener) => listener());
}

/** A step abandoned because newer user intent replaced what it was working on.
 * Its feedback is already on screen, so a composed action stops silently. */
export class SupersededError extends Error {}

export function returnBelongsToAnotherProject(
  bundle: CadReturnBundle,
  designId: string | null | undefined,
): boolean {
  const returned = bundle.designIds ?? [];
  return Boolean(designId && returned.length > 0 && !returned.includes(designId));
}

/** Whether a return is positively linked to the open registry project.
 * Unlinked returns remain available for manual adoption, but must not be
 * guessed into every project merely because they name no other project. */
export function returnBelongsToProject(
  bundle: CadReturnBundle,
  designId: string | null | undefined,
): boolean {
  return Boolean(designId && (bundle.designIds ?? []).includes(designId));
}

export function newestReturnArrival(
  items: CadReturnBundle[],
  previous: Map<string, string> | null,
  nowMs = Date.now(),
): CadReturnBundle | null {
  const recentThreshold = nowMs - 60_000;
  return items.find((item) => item.readable && (
    previous
      ? previous.get(item.bundlePath) !== item.modifiedAt
      : Date.parse(item.modifiedAt) >= recentThreshold
  )) ?? null;
}

type CadHistorySetup = Pick<ReturnType<typeof useCadReturnStore.getState>,
  'sourceSizesMm' | 'rigidSizeMm' | 'transitionMm' | 'skippedSourceIds'
  | 'driveChannels' | 'exteriorOnly' | 'combineEnabled' | 'combineSpec'
  | 'channelDrivers' | 'passiveCardioid'
  | 'driveVoltageV' | 'frequencyStartHz' | 'frequencyEndHz' | 'frequencyCount'>;

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

function savedDriveChannels(
  setup: CadSetup | null | undefined,
  record: CadReturnIngestRecord,
): CadDriveChannel[] {
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
    return [{
      id: channel.id,
      source_ids: [...channel.source_ids],
      motion: channel.motion ?? 'normal',
    }];
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
    // A stored setup carries the submitted numbers, not which library row they
    // came from. When it names the driver, the numbers become that driver's
    // base so the name survives the next submission; otherwise this is exactly
    // the hand-entered form it has always been.
    const label = typeof driver.label === 'string' && driver.label.trim() ? driver.label.trim() : null;
    const preset: DriverPreset | null = label
      ? { id: `manual:${channel.id}`, label, source: 'manual', kind: 'unknown', z_ohm: null, xo_min_hz: null, base: fields }
      : null;
    return [[channel.id, { enabled: true, fields: preset ? {} : fields, preset }]];
  }));
}

function savedPassiveCardioid(setup: CadSetup | null | undefined): PassiveCardioidForm {
  const rearVolumeL = finite(setup?.passive_cardioid_rear_volume_l);
  if (rearVolumeL === null) return { ...PASSIVE_CARDIOID_DEFAULTS };
  const portAreaSource = setup?.port_area_source === 'bem_aperture'
    ? 'bem_aperture'
    : 'user';
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

/** Translate the exact persisted imported request into the editable CAD rail.
 * Missing/malformed legacy pieces fall back to the immutable ingestion, never
 * to values left behind by whichever project happened to be open before it. */
export function cadHistorySetup(
  job: JobItem,
  record: CadReturnIngestRecord,
): CadHistorySetup {
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
  // Both crossover generations come back here, in the submitted form rather
  // than a resolved one. `parseWire` keeps a manual gain manual and an
  // explicit polarity explicit; `fromResult` is the fallback that expands a
  // job old enough to carry only `crossovers_hz`, because reading that as "no
  // crossover" would silently drop the setting the run was solved with.
  const combine = object(setup?.combine);
  const combineSpec = parseWire(combine) ?? fromResult(combine ?? undefined);
  const validCombine = combineSpec !== null;
  const explicitFrequencies = Array.isArray(job.solve_options.frequencies_hz)
    ? job.solve_options.frequencies_hz.filter((value) => finite(value) !== null)
    : [];
  const range = job.solve_options.frequency_range;
  const fallbackRange = Array.isArray(range) && range.length === 2
    ? range.map(Number)
    : [200, 20_000];
  const sourceSizes = finiteRecord(mesh?.source_size_mm)
    ?? { ...record.mesh_sizes.source_size_mm };
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
    engine: options.engine || state.engine,
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

/** Show the CAD workspace and focus its panel.
 *
 * Every status line, error and finding on the CAD return leg
 * renders inside `CadLinkPanel`, which exists only in CAD mode — so a return
 * that arrives while WG shows the parametric design is otherwise completely
 * invisible. The mode store adds the dock panel synchronously, which is why
 * the activation on the next line lands instead of returning false. */
export function enterCadWorkspace(): void {
  workspaceModeStore.setMode('cad');
  workspaceNavigation.activate('cadlink');
}

/** Prefer the independently tessellated full CAD display artifact. Older
 * records and advisory display failures fall back to the exact solver mesh. */
export async function showIngestedMeshInViewport(
  record: CadReturnIngestRecord,
  name: string,
  onNotice?: (notice: string) => void,
  fetcher: typeof fetch = fetch,
  generation = importedMeshStore.beginIntent(),
): Promise<void> {
  const ingestId = record.ingest_id;
  const available = importedMeshStore.getSnapshot().cad;
  if (available?.ingestId === ingestId) {
    if (workspaceModeStore.getSnapshot().mode === 'cad') importedMeshStore.showCad(generation);
    return;
  }
  try {
    const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/viewport-mesh`);
    if (!importedMeshStore.isCurrentGeneration(generation)) return;
    if (response.ok) {
      const meshText = await response.text();
      if (!importedMeshStore.isCurrentGeneration(generation)) return;
      importedMeshStore.setCad(createImportedMeshScene(
        name,
        parseMSH(meshText),
        'cad',
        ingestId,
        record.symmetry.cut_planes ?? [],
        {
          fullDomain: true,
          solvedTriangleCount: record.mesh?.stats.triangle_count,
          artifactToken: record.viewport_mesh?.content_sha256 ?? `${ingestId}:viewport`,
        },
      ), generation, workspaceModeStore.getSnapshot().mode === 'cad');
      return;
    }
    if (response.status === 409) {
      onNotice?.('The independent CAD viewport artifact failed verification. Showing the exact solver mesh instead.');
    }
  } catch {
    // The independent display artifact is advisory; try the solver artifact.
  }
  if (!importedMeshStore.isCurrentGeneration(generation)) return;
  try {
    const response = await fetcher(`/api/cadlink/ingest/${encodeURIComponent(ingestId)}/mesh`);
    if (response.ok && importedMeshStore.isCurrentGeneration(generation)) {
      const meshText = await response.text();
      if (!importedMeshStore.isCurrentGeneration(generation)) return;
      importedMeshStore.setCad(createImportedMeshScene(
        name,
        parseMSH(meshText),
        'cad',
        ingestId,
        record.symmetry.cut_planes ?? [],
        {
          solvedTriangleCount: record.mesh?.stats.triangle_count,
          artifactToken: record.mesh_content_sha256 ?? `${ingestId}:solver`,
        },
      ), generation, workspaceModeStore.getSnapshot().mode === 'cad');
      return;
    }
  } catch {
    // Neither artifact could be displayed; fall through to drop the old one.
  }
  // A scene left over from an earlier ingestion would keep claiming to be the
  // geometry on screen — misleading on its own, and enough to refuse every
  // later solve at the viewport-mismatch gate. An empty slot is honest.
  if (importedMeshStore.isCurrentGeneration(generation)
    && importedMeshStore.getSnapshot().cad !== null
    && importedMeshStore.getSnapshot().cad?.ingestId !== ingestId) {
    importedMeshStore.clear('cad');
  }
}

/**
 * Recall the immutable ingestion behind an archived CAD run.
 *
 * The job carries only provenance, so the ingestion record is fetched before
 * it becomes the active CAD context. A synthetic, read-only bundle gives the
 * CAD input surfaces the recalled document name and source inventory without
 * pretending the original return bundle is still available for rebuilding.
 */
/**
 * Re-read every picked driver's own numbers from the library it came from.
 *
 * A preset carries a copy of the row it was picked from, so a library row that
 * gains its T/S later never reaches the channel already naming that driver:
 * the form stays incomplete and the channel solves undriven. This is what
 * makes "I filled in the compression drivers' T/S" reach a project that picked
 * them before, whether its settings were just restored or a run was recalled.
 *
 * Advisory throughout. A library that cannot be read, or a row that is no
 * longer there, leaves the stored numbers exactly as they are: they are what
 * the last solve used, and losing them would be worse than not refreshing.
 */
export async function refreshChannelDriverBases(
  fetcher: typeof fetch = fetch,
): Promise<string[]> {
  const forms = useCadReturnStore.getState().channelDrivers;
  const saved = new Map(useDriverLibraryStore.getState().saved.map((driver) => [driver.id, driver]));
  const updates: Record<string, DriverBaseUpdate> = {};
  await Promise.all(Object.entries(forms).map(async ([channelId, form]) => {
    const preset = form.preset;
    if (!preset || preset.source === 'manual') return;
    if (preset.source === 'mine') {
      const driver = saved.get(preset.id);
      if (driver) {
        updates[channelId] = {
          presetId: preset.id,
          base: { ...driver.base, ...driver.overrides },
          xo_min_hz: driver.xo_min_hz,
        };
      }
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

export async function showCadJobModel(
  job: JobItem,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  if (job.config_summary.geometry_type !== 'imported') return false;
  const ingestId = job.cad_source?.ingest_id;
  const displayName = job.cad_source?.document_name || job.label || `run #${job.run_number}`;
  enterCadWorkspace();
  const coordinator = cadLinkCoordinatorBridge.getSnapshot();
  coordinator.reportViewportNotice(null);
  if (!ingestId) {
    coordinator.reportStatus(`Cannot show ${displayName}: this CAD run has no ingestion identity.`);
    return false;
  }
  coordinator.reportStatus(`Loading ${displayName} from run #${job.run_number}…`);
  try {
    const record = await getIngest(ingestId, fetcher);
    const bundle: CadReturnBundle = {
      name: `${displayName}.wgreturn`,
      bundlePath: '',
      modifiedAt: record.created_at,
      readable: true,
      documentName: displayName,
      requestId: null,
      sourceCount: record.sources.length,
      instanceCount: null,
      sources: record.sources.map((source) => ({
        id: source.id,
        role: source.role,
        required: source.required,
        suggestedResolutionMm: source.suggested_resolution_mm,
        defaultDriveChannelId: source.default_drive_channel_id,
      })),
    };
    useCadReturnStore.getState().beginIngestIntent();
    const savedSetup = cadHistorySetup(job, record);
    const project = record.project?.lineage_id
      ?? useCadReturnStore.getState().projectLineageId
      ?? rememberedCadProject();
    // The mesh, channels and sweep are the run's own -- they describe the
    // geometry being put back on screen. The drivers are not: they are the
    // project's, and the project's are newer. A run stores the numbers it was
    // submitted with rather than which library row they came from, so replaying
    // its own would re-solve with the T/S the library held that day and could
    // never pick up values filled in since.
    const projectDrivers = projectChannelDrivers(bundle, project);
    // Keep the archived source inventory visible, but disable actions that need
    // the original return bundle path.
    useCadReturnStore.setState({
      selectedBundle: {
        ...bundle,
        readable: false,
        reason: 'Recalled from an archived run; the original return bundle is not active.',
      },
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
    // The project's drivers are now on the channels; this is what makes their
    // T/S the library's current numbers rather than the ones they were picked
    // with, which is the whole of re-solving an old run with updated drivers.
    const rereadDrivers = await refreshChannelDriverBases(fetcher);
    const viewportGeneration = importedMeshStore.beginIntent();
    await showIngestedMeshInViewport(record, displayName, coordinator.reportViewportNotice, fetcher, viewportGeneration);
    const shown = importedMeshStore.getSnapshot().cad?.ingestId === ingestId;
    if (!shown) {
      coordinator.reportStatus(`Cannot show ${displayName}: the archived CAD mesh artifacts are no longer available.`);
      return false;
    }
    coordinator.reportStatus(`Showing ${displayName} from run #${job.run_number}.${
      rereadDrivers.length ? ` Re-read ${rereadDrivers.length} driver${rereadDrivers.length === 1 ? '' : 's'} from the library.` : ''
    }`);
    return true;
  } catch (reason) {
    const missing = reason instanceof CadLinkApiError && reason.status === 404;
    coordinator.reportStatus(missing
      ? `Cannot show ${displayName}: the archived CAD ingestion and mesh artifacts are no longer available.`
      : `Could not show ${displayName}: ${reason instanceof Error ? reason.message : String(reason)}`);
    return false;
  }
}

export function CadLinkCoordinator() {
  const preferences = usePreferences();
  const design = useDesignStore((state) => state.design);
  const designRevision = useDesignStore((state) => state.designRevision);
  // Directivity and solver settings ride along in the sent `.cfg` but never
  // touch the geometry revision, so freshness has to watch them separately.
  const documentSettings = useSolveOptionsStore(documentSettingsSignature);
  const identity = useDocumentStore((state) => state.identity);
  const designName = useDocumentStore((state) => state.designName);
  const setCadLink = useDocumentStore((state) => state.setCadLink);
  const selectedBundlePath = useCadReturnStore((state) => state.selectedBundle?.bundlePath ?? null);
  const [bundles, setBundles] = useState<CadReturnBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [sendingToFusion, setSendingToFusion] = useState(false);
  const [pullingFromFusion, setPullingFromFusion] = useState(false);
  const [pendingFusionConflict, setPendingFusionConflict] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [viewportNotice, setViewportNotice] = useState<string | null>(null);
  const [fusionStatus, setFusionStatus] = useState<FusionCadStatus | null>(null);
  const [selectedFusionInstanceId, setSelectedFusionInstanceId] = useState<string | null>(null);
  const [onshapeStatus, setOnshapeStatus] = useState<OnshapeStatus | null>(null);
  const [selectedOnshapeInstanceId, setSelectedOnshapeInstanceId] = useState<string | null>(null);
  const [onshapeConnection, setOnshapeConnection] = useState<OnshapeConnection | null>(null);
  const seenReturnRevisions = useRef<Map<string, string> | null>(null);
  const projectOpenPending = useRef(false);
  const refreshRef = useRef<(options?: RefreshOptions) => Promise<void>>(unavailable);
  const returnListRequest = useRef(0);
  const fusionSendRequest = useRef(0);
  const ingestRequest = useRef(0);
  const ingestAbortController = useRef<AbortController | null>(null);
  const fusionStatusRequest = useRef(0);
  const onshapeStatusRequest = useRef(0);
  const onshapeConnectionRequested = useRef(false);
  const mounted = useRef(true);
  const pendingReturnRequestId = useRef<string | null>(null);
  const pendingReturnRequestedAt = useRef<number | null>(null);
  // Set while a caller awaits one exact correlated arrival. The poll loop is
  // still the only thing that discovers returns; this lets a composed action
  // (pull, then ingest, then solve) continue from that discovery.
  const solveCommandInFlight = useRef(false);
  const solveCommandSeen = useRef<string | null>(null);
  const autoIngestPending = useRef(false);
  const ingestSelectedRef = useRef<() => Promise<CadReturnIngestRecord>>(unavailable);
  const selectBundleRef = useRef<(bundle: CadReturnBundle, projectLineageId?: string | null) => void>(() => undefined);
  const pendingReturnWaiter = useRef<{
    requestId: string;
    settle: (bundle: CadReturnBundle) => void;
    fail: (reason: Error) => void;
  } | null>(null);
  const fusionPullPromise = useRef<Promise<CadReturnBundle> | null>(null);
  const onshape = preferences.cadApplication === 'onshape';

  useEffect(() => {
    setSelectedFusionInstanceId(null);
    setSelectedOnshapeInstanceId(null);
  }, [identity?.designId]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      fusionSendRequest.current += 1;
      ingestRequest.current += 1;
      ingestAbortController.current?.abort();
    };
  }, []);

  /** Start automatic preparation only when no Fusion-authored solve command
   * owns selection/ingestion. A returns poll can finish while the solve-command
   * poll is merely checking; remember that arrival and retry after the check
   * proves there is no command to consume. */
  const autoIngestSelected = useCallback(() => {
    if (parkedSolveCommandStore.getSnapshot().command) {
      autoIngestPending.current = false;
      return;
    }
    if (solveCommandInFlight.current) {
      autoIngestPending.current = true;
      return;
    }
    autoIngestPending.current = false;
    void ingestSelectedRef.current().catch(() => undefined);
  }, []);

  useEffect(() => subscribeRevision((event) => {
    if (event.reason !== 'load') return;
    if (event.loadSource === 'cad-project-switch') {
      // A registry project is a CAD workflow, even before it has a prepared
      // return. Drop the previous project's geometry and make the latest
      // positively-linked return eligible for selection on the next listing.
      projectOpenPending.current = true;
      seenReturnRevisions.current = null;
      returnListRequest.current += 1;
      useCadReturnStore.getState().selectBundle(null);
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
    // A replacement may be the document this return belongs to, so retain the
    // evidence and channel work for the freshness verdict instead of guessing
    // ownership here. The stale gate makes the old geometry unsendable now.
    useCadReturnStore.getState().markIngestStale(
      'The design was replaced after this CAD return was ingested. Re-ingest before solving.',
    );
  }), []);

  const refreshFusionStatus = useCallback(async () => {
    if (preferences.cadApplication !== 'fusion360') return;
    const request = ++fusionStatusRequest.current;
    try {
      const next = await getFusionCadStatus(
        design,
        identity,
        selectedBundlePath,
        fetch,
        selectedFusionInstanceId,
      );
      if (request === fusionStatusRequest.current
        && ['closed', 'addin_offline', 'no_document', 'not_linked', 'instance_selection_required', 'current', 'stale'].includes(next.state)) {
        setFusionStatus(next);
      }
    } catch {
      // Presence is advisory. Workspace and export errors are presented by the
      // actual action; a missed heartbeat must not hide CAD returns.
    }
  }, [design, identity, preferences.cadApplication, selectedBundlePath, selectedFusionInstanceId]);

  const selectFusionInstance = useCallback((instanceId: string) => {
    setSelectedFusionInstanceId(instanceId);
    setFusionStatus(null);
  }, []);

  useEffect(() => {
    setFusionStatus(null);
    if (preferences.cadApplication !== 'fusion360') return undefined;
    if (pageIsVisible()) void refreshFusionStatus();
    const timer = window.setInterval(() => {
      if (pageIsVisible()) void refreshFusionStatus();
    }, 2_500);
    return () => {
      window.clearInterval(timer);
      fusionStatusRequest.current += 1;
    };
  }, [designRevision, documentSettings, preferences.cadApplication, refreshFusionStatus]);

  // `committed` is the identity a send just registered. Without it the refresh
  // that follows a first send would still carry the pre-send identity -- which
  // is null for an unsaved design -- and report the design it had just linked
  // as unlinked until the next render settled.
  const refreshOnshapeStatus = useCallback(async (committed?: DesignIdentity) => {
    const request = ++onshapeStatusRequest.current;
    try {
      const next = await getOnshapeStatus(
        design, committed ?? identity, fetch, selectedOnshapeInstanceId,
      );
      if (request === onshapeStatusRequest.current) setOnshapeStatus(next);
    } catch {
      // Advisory, like the Fusion heartbeat: the send itself reports failures.
    }
  }, [design, identity, selectedOnshapeInstanceId]);

  const selectOnshapeInstance = useCallback((instanceId: string) => {
    setSelectedOnshapeInstanceId(instanceId);
    setOnshapeStatus(null);
  }, []);

  // No interval. This status is derived from WG's own registry and changes
  // only when the design or a send does, both of which re-run this effect.
  useEffect(() => {
    setOnshapeStatus(null);
    if (!onshape) return;
    void refreshOnshapeStatus();
  }, [designRevision, documentSettings, onshape, refreshOnshapeStatus]);

  // The connection route is the only check here that spends Onshape API rate
  // limit. Delay it until Onshape is used, then make at most one request for
  // this always-mounted coordinator's lifetime -- never on a timer.
  useEffect(() => {
    if (!onshape || onshapeConnectionRequested.current) return;
    onshapeConnectionRequested.current = true;
    void getOnshapeConnection()
      .then((next) => { if (mounted.current) setOnshapeConnection(next); })
      .catch(() => { /* the status card already reports an unconfigured link */ });
  }, [onshape]);

  const refresh = useCallback(async (options: RefreshOptions = {}) => {
    const background = options.background === true;
    const request = ++returnListRequest.current;
    // The wgreturn folder belongs exclusively to the Fusion add-in. Keeping
    // the guard inside the command also prevents callers from accidentally
    // reading it while the always-mounted coordinator is in Onshape mode.
    if (preferencesStore.getSnapshot().cadApplication === 'onshape') {
      if (projectOpenPending.current) {
        projectOpenPending.current = false;
        setStatus('Project design loaded. Return it from Onshape to prepare Simulation geometry.');
      }
      setLoading(false);
      return;
    }
    if (!background) { setLoading(true); setError(null); }
    try {
      const response = await listReturns();
      // Poll, manual refresh, and the post-send refresh may overlap. Only the
      // newest request may revise evidence or auto-select a return; otherwise a
      // slow old directory snapshot can roll the whole CAD state backwards.
      if (request !== returnListRequest.current) return;
      setBundles(response.items);
      const previous = seenReturnRevisions.current;
      const next = new Map(response.items.map((item) => [item.bundlePath, item.modifiedAt]));
      const requested = pendingReturnRequestId.current
        ? response.items.find((item) => (
            item.readable && item.requestId === pendingReturnRequestId.current
          )) ?? null
        : null;
      if (
        pendingReturnRequestId.current
        && pendingReturnRequestedAt.current !== null
        && Date.now() - pendingReturnRequestedAt.current > 60_000
      ) {
        pendingReturnRequestId.current = null;
        pendingReturnRequestedAt.current = null;
        const timeout = 'Fusion did not return the requested model within 60 seconds. Check Fusion for a WGLink message, then retry.';
        const waiter = pendingReturnWaiter.current;
        pendingReturnWaiter.current = null;
        // A composed caller owns the message: let it decide how to report.
        if (waiter) waiter.fail(new Error(timeout));
        else setError(timeout);
      }
      const arrived = options.autoOpenNew
        ? requested ?? (
            pendingReturnRequestId.current
              ? null
              : newestReturnArrival(response.items, previous)
          )
        : null;
      seenReturnRevisions.current = next;
      const currentDesignId = useDocumentStore.getState().identity?.designId;
      const initial = previous === null
        ? response.items.find((item) => (
            item.readable && (
              currentDesignId
                ? returnBelongsToProject(item, currentDesignId)
                : (item.designIds ?? []).length === 0
            )
          )) ?? null
        : null;
      const opened = arrived ?? initial;
      const projectMismatch = Boolean(
        opened && returnBelongsToAnotherProject(opened, currentDesignId),
      );
      let continuity: 'initial' | 'carried' | 'reset' = 'initial';
      if (opened && !projectMismatch) {
        // A compatible current or saved source inventory keeps the user's solve
        // setup; a genuinely first listing starts clean without being a reset.
        continuity = arrived
          ? useCadReturnStore.getState().selectArrivedBundle(arrived)
          : (useCadReturnStore.getState().selectBundle(opened), 'initial');
        // Quietly here: these drivers were just restored, so the user has not
        // seen the numbers this replaces, and the arrival owns the status line.
        void refreshChannelDriverBases().catch(() => undefined);
        // Selecting evidence invalidates a load for the previous return, but
        // mode—not return discovery—decides what the viewport displays.
        importedMeshStore.beginIntent();
      }
      if (projectOpenPending.current) {
        projectOpenPending.current = false;
        setStatus(initial
          ? `Project design loaded. Selected the latest matching return from ${initial.documentName ?? initial.name}; prepare it to restore Simulation geometry.`
          : 'Project design loaded. No matching CAD return is available yet; return the project from Fusion or Onshape to prepare Simulation geometry.');
      }
      if (arrived) {
        if (projectMismatch) {
          const reason = `Received ${arrived.documentName ?? arrived.name}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`;
          if (arrived.requestId === pendingReturnRequestId.current) {
            pendingReturnRequestId.current = null;
            pendingReturnRequestedAt.current = null;
          }
          const waiter = pendingReturnWaiter.current;
          if (waiter && arrived.requestId === waiter.requestId) {
            pendingReturnWaiter.current = null;
            waiter.fail(new Error(reason));
          } else {
            setError(reason);
          }
          enterCadWorkspace();
          return;
        }
        const parked = parkedSolveCommandStore.getSnapshot().command;
        if (parked && parked.bundlePath !== arrived.bundlePath) {
          await refuseParkedSolveCommand('Superseded by a newer return from Fusion.');
        }
        if (arrived.requestId === pendingReturnRequestId.current) {
          pendingReturnRequestId.current = null;
          pendingReturnRequestedAt.current = null;
        }
        const waiter = pendingReturnWaiter.current;
        if (waiter && arrived.requestId === waiter.requestId) {
          pendingReturnWaiter.current = null;
          waiter.settle(arrived);
        }
        setStatus(`Received ${arrived.documentName ?? arrived.name} from Fusion 360.${
          continuity === 'carried' ? ' Kept your mesh, channel, and solve settings.' : ''
        }`);
        // An arrival is news the user has to be able to see, so it owns the
        // workspace the same way an Onshape return does. A first listing does
        // not: nothing arrived, and stealing the mode on load would be wrong.
        enterCadWorkspace();
        autoIngestSelected();
      } else if (!initial) {
        const selected = useCadReturnStore.getState().selectedBundle;
        if (!selected) return;
        // An unconfigured listing is not evidence the bundle is gone -- a
        // server that is still starting up answers exactly that, and one such
        // poll used to latch the ingest stale until a manual re-ingest.
        if (!response.cadFolderConfigured) return;
        const current = response.items.find((bundle) => bundle.bundlePath === selected.bundlePath);
        useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
      }
    } catch (reason) {
      if (request === returnListRequest.current && !background) {
        if (projectOpenPending.current) {
          projectOpenPending.current = false;
          setStatus('Project design loaded, but its CAD returns could not be read. Refresh CAD Link to try again.');
        }
        setError(reason instanceof Error ? reason.message : String(reason));
      }
    } finally {
      // A background poll can supersede the initial foreground listing. The
      // latest completion owns the loading flag even when it did not raise it.
      if (request === returnListRequest.current) setLoading(false);
    }
  }, [autoIngestSelected]);
  refreshRef.current = refresh;

  // Entering CAD Link with nothing on screen reopens the project that was
  // there last: the remembered lineage's newest return, selected exactly as
  // the project switcher would, which prepares it and names the project.
  // Anything already selected -- including the initial listing's own pick --
  // wins; this only fills an otherwise empty mode.
  const restoringCadProject = useRef(false);
  useEffect(() => {
    const maybeRestore = () => {
      if (workspaceModeStore.getSnapshot().mode !== 'cad') return;
      if (preferencesStore.getSnapshot().cadApplication === 'onshape') return;
      if (restoringCadProject.current) return;
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
          // Restoring is a convenience; the empty-mode guidance stays the
          // honest fallback when the listing cannot be read.
        } finally {
          restoringCadProject.current = false;
        }
      })();
    };
    maybeRestore();
    return workspaceModeStore.subscribe(maybeRestore);
  }, []);

  /** One outbound Fusion action for every surface. The rail card and CAD Link
   * panel both call this bridge so identity adoption, feedback, and return-list
   * refresh cannot drift into subtly different send paths. */
  const sendToFusion = useCallback(async (target?: { documentId: string; instanceId: string; returnStateHash: string | null }) => {
    const request = ++fusionSendRequest.current;
    setSendingToFusion(true); setError(null); setStatus(null);
    try {
      const polarConfig = polarConfigFromUi(useSolveOptionsStore.getState().polar);
      const result = await sendDesignToCad(
        design,
        designRevision,
        designNameSlug(designName),
        identity,
        fetch,
        undefined,
        target ?? null,
        polarConfig,
      );
      if (request === fusionSendRequest.current && mounted.current) {
        recordCommittedAthPolars(polarConfig);
        if (result.identity) setCadLink(result.identity, 'current');
        setStatus(target
          ? `Update sent to Fusion 360 · sequence ${result.sequence}`
          : `Opening in Fusion 360 · sequence ${result.sequence}`);
        await refresh();
      }
      return result;
    } catch (reason) {
      if (request === fusionSendRequest.current && mounted.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (request === fusionSendRequest.current && mounted.current) setSendingToFusion(false);
    }
  }, [design, designRevision, designName, identity, refresh, setCadLink]);

  // The one Fusion outbound entry point (menu, rail, and panel). Deriving the
  // action and the expected-document guard here means no call site can send an
  // update without them — the drift that let the file menu bypass the two-way
  // conflict confirmation.
  const sendWgToFusion = useCallback(async (options?: { confirmed?: boolean }) => {
    const current = fusionStatus;
    if (current?.state === 'instance_selection_required') {
      const reason = new Error('Choose which linked Fusion instance to update.');
      setError(reason.message);
      throw reason;
    }
    const action = fusionWorkflowView(current).action;
    if (action === 'update' && current?.fusionChangesAvailable && !options?.confirmed) {
      setPendingFusionConflict(true);
      return null;
    }
    setPendingFusionConflict(false);
    return sendToFusion(action === 'update' && current?.documentId && current.link
      ? { documentId: current.documentId, instanceId: current.link.instanceId, returnStateHash: current.link.documentSignatureHash }
      : undefined);
  }, [fusionStatus, sendToFusion]);

  const cancelFusionConflict = useCallback(() => setPendingFusionConflict(false), []);

  // Returns arrive in the workspace's wgreturn folder, which only the Fusion
  // add-in writes. Onshape bundles use WG's data directory and never enter this
  // lifecycle, so there is deliberately no returns poll in Onshape mode.
  useEffect(() => {
    if (onshape) { setLoading(false); return undefined; }
    if (pageIsVisible()) void refresh({ autoOpenNew: true });
    else setLoading(false);
    const timer = window.setInterval(() => {
      if (pageIsVisible()) void refresh({ background: true, autoOpenNew: true });
    }, 2_500);
    return () => {
      window.clearInterval(timer);
      returnListRequest.current += 1;
    };
  }, [onshape, refresh]);

  const expectFusionReturn = useCallback((requestId: string, requestedAt = Date.now()) => {
    pendingReturnRequestId.current = requestId;
    pendingReturnRequestedAt.current = requestedAt;
  }, []);

  /** Ask Fusion for the active document and resolve with the exact correlated
   * return. Composable: the caller decides whether to ingest and solve. */
  const pullFromFusion = useCallback((): Promise<CadReturnBundle> => {
    if (fusionPullPromise.current) return fusionPullPromise.current;
    setPullingFromFusion(true);
    // Failures are reported here as well as thrown, so a fire-and-forget
    // caller still shows them and a composed caller can still stop.
    const fail = (reason: unknown): never => {
      const error = reason instanceof Error ? reason : new Error(String(reason));
      if (!(error instanceof SupersededError) && mounted.current) setError(error.message);
      throw error;
    };
    const operation = (async () => {
      if (!identity?.designId || !fusionStatus?.documentId || !fusionStatus.link) {
        return fail(new Error('Fusion changed documents. Refresh CAD Link and try again.'));
      }
      setError(null);
      const result = await requestFusionReturn({
        designId: identity.designId,
        documentId: fusionStatus.documentId,
        instanceId: fusionStatus.link.instanceId,
        expectedReturnStateHash: fusionStatus.link.documentSignatureHash,
      }).catch(fail);
      expectFusionReturn(result.requestId);
      setStatus(`Requested current geometry from ${result.documentName}. Waiting for Fusion…`);
      const arrival = new Promise<CadReturnBundle>((settle, reject) => {
        pendingReturnWaiter.current = { requestId: result.requestId, settle, fail: reject };
      });
      void refresh({ background: true, autoOpenNew: true });
      return arrival.catch(fail);
    })();
    const tracked = operation.finally(() => {
      if (fusionPullPromise.current !== tracked) return;
      fusionPullPromise.current = null;
      if (mounted.current) setPullingFromFusion(false);
    });
    fusionPullPromise.current = tracked;
    return tracked;
  }, [expectFusionReturn, fusionStatus, identity?.designId, refresh]);

  const reportViewportNotice = useCallback((message: string | null) => setViewportNotice(message), []);

  /** Ingest the selected return and hand back the verified record.
   *
   * The panel button wants a void action that reports into the panel; a
   * composed action (pull, ingest, solve) has to know whether the record
   * exists before it can gate a solve on it. This is the composable half:
   * it reports the same feedback and then throws or returns the record. */
  const ingestSelected = useCallback(async (): Promise<CadReturnIngestRecord> => {
    const current = useCadReturnStore.getState();
    if (!current.selectedBundle) throw new Error('Select a CAD return before preparing a simulation.');
    // This intent covers the ingest record itself. The viewport has a separate
    // token because its follow-up artifact fetch can be superseded independently.
    const ingestGeneration = current.beginIngestIntent();
    const request = ++ingestRequest.current;
    ingestAbortController.current?.abort();
    const abortController = new AbortController();
    ingestAbortController.current = abortController;
    // Intent starts before the network request. A later viewport choice must
    // win even when this ingest's mesh fetch eventually completes.
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setIngestError(null); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const skipped = new Set(current.skippedSourceIds);
      const record = await ingestReturn({
        bundlePath: current.selectedBundle.bundlePath,
        mesh: {
          rigidSizeMm: current.rigidSizeMm,
          transitionMm: current.transitionMm,
          sourceSizeMm: Object.fromEntries(Object.entries(current.sourceSizesMm).filter(([id]) => !skipped.has(id))),
        },
        skippedSourceIds: current.skippedSourceIds,
        areaDriftOverrides: current.areaDriftOverrides,
        expectedDesignId: useDocumentStore.getState().identity?.designId ?? null,
        expectedInstanceId: (() => {
          const bundle = current.selectedBundle;
          const instances = bundle.instances ?? [];
          const liveInstanceId = fusionStatus?.link?.instanceId ?? null;
          if (liveInstanceId && instances.some((item) => item.instanceId === liveInstanceId)) {
            return liveInstanceId;
          }
          const anchor = bundle.solverAnchorInstanceId ?? null;
          return anchor && instances.some((item) => item.instanceId === anchor)
            ? anchor
            : null;
        })(),
        symmetryMode: useCadPreparationStore.getState().symmetryMode,
      }, fetch, abortController.signal);
      if (!useCadReturnStore.getState().applyIngest(record, ingestGeneration)) {
        const superseded = 'Discarded a completed ingest because its selected return or design was superseded. Rebuild the mesh for the current state.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      if (request === ingestRequest.current && mounted.current) {
        rememberCadProject(record.project?.lineage_id);
        setStatus(`Ingested ${record.ingest_id}. Review the verdicts before solving.`);
        // Before the display, so the viewport adopts the CAD slot rather than
        // loading it invisibly behind the parametric design.
        enterCadWorkspace();
        // Awaited rather than fired and forgotten: the solve gate refuses an
        // ingestion whose mesh is not the one on screen, so a composed
        // pull/ingest/solve that raced this fetch failed on every return after
        // the first with a viewport-mismatch the user could not act on.
        await showIngestedMeshInViewport(
          record,
          current.selectedBundle.documentName || current.selectedBundle.name,
          reportViewportNotice,
          fetch,
          viewportGeneration,
        );
      }
      return record;
    } catch (reason) {
      if (reason instanceof SupersededError) throw reason;
      if (!useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        const superseded = 'Discarded an ingest response because its selected return or design was superseded. Rebuild the mesh for the current state.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      const message = reason instanceof Error ? reason.message : String(reason);
      if (request === ingestRequest.current && mounted.current) {
        const structured = reason instanceof CadLinkApiError ? reason.areaDriftSources : [];
        structured.forEach(current.flagAreaDrift);
        if (!structured.length) {
          const drift = /source ['"]([^'"]+)['"] area drift/i.exec(message);
          if (drift) current.flagAreaDrift(drift[1]);
        }
        setError(message);
        setIngestError(message);
      }
      throw reason instanceof Error ? reason : new Error(message);
    } finally {
      if (ingestAbortController.current === abortController) ingestAbortController.current = null;
      if (request === ingestRequest.current && mounted.current) setIngesting(false);
    }
  }, [fusionStatus?.link?.instanceId, reportViewportNotice]);
  ingestSelectedRef.current = ingestSelected;

  /** Re-read restored drivers from the library, and say so when the numbers
   * moved: a simulation input that changes on its own is not a silent event. */
  const rereadDrivers = useCallback(() => {
    void refreshChannelDriverBases().then((channels) => {
      if (!channels.length || !mounted.current) return;
      setStatus(`Re-read ${channels.length} driver${channels.length === 1 ? '' : 's'} from the driver library.`);
    }).catch(() => { /* advisory: the stored numbers stand */ });
  }, []);

  /** Manual list selection is preparation intent too. Selecting immediately
   * advances both generations, then a fresh ingest aborts any older request;
   * late fetch implementations that ignore abort are still rejected by the
   * store generation before they can publish a record or viewport scene. */
  const selectBundle = useCallback((bundle: CadReturnBundle, projectLineageId?: string | null) => {
    if (returnBelongsToAnotherProject(bundle, useDocumentStore.getState().identity?.designId)) {
      setError(`That return belongs to another CAD-linked project. Open it from File → CAD-linked designs first.`);
      enterCadWorkspace();
      return;
    }
    useCadReturnStore.getState().selectBundle(bundle, projectLineageId);
    rereadDrivers();
    importedMeshStore.beginIntent();
    enterCadWorkspace();
    autoIngestSelected();
  }, [autoIngestSelected, rereadDrivers]);
  selectBundleRef.current = selectBundle;

  // The panel's button: same work, feedback already presented, nothing thrown.
  const ingest = useCallback(async () => {
    await ingestSelected().catch(() => undefined);
  }, [ingestSelected]);

  const returnFromOnshape = useCallback(async () => {
    if (!identity?.designId) throw new Error('Send this design to Onshape before returning it.');
    const ingestGeneration = useCadReturnStore.getState().beginIngestIntent();
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const result = await returnOnshapeToWg(
        identity.designId, fetch, selectedOnshapeInstanceId,
      );
      const sources = result.ingest.sources.map((source) => ({
        id: source.id,
        role: source.role,
        required: source.required,
        suggestedResolutionMm: source.suggested_resolution_mm,
        defaultDriveChannelId: source.default_drive_channel_id,
      }));
      const bundle: CadReturnBundle = {
        name: result.bundle.name,
        bundlePath: result.bundle.bundlePath,
        modifiedAt: result.ingest.created_at,
        readable: true,
        documentName: result.bundle.documentName,
        requestId: null,
        sourceCount: result.bundle.sourceCount,
        instanceCount: result.bundle.instanceCount,
        designIds: [identity.designId],
        sources,
      };
      const state = useCadReturnStore.getState();
      // Same-inventory Onshape iterations keep the user's solve setup too.
      state.selectArrivedBundle(bundle);
      const selectedGeneration = state.beginIngestIntent();
      if (!useCadReturnStore.getState().applyIngest(result.ingest, selectedGeneration)) {
        setStatus('Discarded the Onshape return because the selected design changed.');
        return;
      }
      setBundles([bundle]);
      setStatus(`Returned and ingested ${result.bundle.documentName ?? result.bundle.name} from Onshape.`);
      // The CAD Link panel only exists inside the CAD workspace, and the
      // ingested return is now the solve truth — enter the mode that owns it.
      enterCadWorkspace();
      await showIngestedMeshInViewport(
        result.ingest,
        result.bundle.documentName ?? result.bundle.name,
        reportViewportNotice,
        fetch,
        viewportGeneration,
      );
    } catch (reason) {
      if (useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      setIngesting(false);
    }
  }, [identity?.designId, reportViewportNotice, selectedOnshapeInstanceId]);

  /** Ask Fusion for its current geometry, prepare it, and start the solve.
   *
   * Each step is the composable action above, so this adds only the staged
   * status and the decision about where to stop: a blocked readiness gate is
   * reported and left for the user, never solved around. */
  const pullAndSolve = useCallback(async (): Promise<'solving' | 'blocked' | 'failed'> => {
    try {
      await pullFromFusion();
      setStatus('Received the current Fusion geometry. Preparing the simulation…');
      await ingestSelected();
      setStatus('Prepared. Submitting the solve…');
      const outcome = await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
      if (outcome === 'busy') {
        setStatus('Prepared the Fusion geometry. A solve is already running — press Solve when it finishes.');
        return 'blocked';
      }
      setStatus('Solving the current Fusion geometry.');
      return 'solving';
    } catch (reason) {
      // Supersession and the step failures already reported themselves; a
      // readiness refusal is the interesting case and belongs on screen.
      if (reason instanceof SupersededError) return 'blocked';
      const message = reason instanceof Error ? reason.message : String(reason);
      if (mounted.current) {
        setStatus(null);
        setError(message);
      }
      return useCadReturnStore.getState().ingestRecord ? 'blocked' : 'failed';
    }
  }, [ingestSelected, pullFromFusion]);

  /** Start the parked Fusion request from the panel, once its gate is clear. */
  const solveParkedCommand = useCallback(async () => {
    const parked = parkedSolveCommandStore.getSnapshot().command;
    if (!parked) return;
    setError(null);
    try {
      const outcome = await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
      if (outcome === 'busy') {
        parkedSolveCommandStore.setBlockers(parked.commandId, ['a solve is already running']);
        setStatus('A solve is already running. Start the model Fusion sent when it finishes.');
        return;
      }
      setStatus('Solving the model Fusion sent.');
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      parkedSolveCommandStore.setBlockers(parked.commandId, [message]);
      if (mounted.current) setError(message);
    }
  }, []);

  /** Give up on the parked Fusion request. Terminal on purpose: the ledger
   * entry is what deletes the marker, so a dismissed command cannot come back
   * on the next page load. */
  const dismissSolveCommand = useCallback(async () => {
    if (!parkedSolveCommandStore.getSnapshot().command) return;
    await refuseParkedSolveCommand('Dismissed in Waveguide Generator without solving.');
    if (mounted.current) {
      setError(null);
      setStatus('Dismissed the solve Fusion asked for. It will not be offered again.');
    }
  }, []);

  /** Run a Fusion-authored "solve in WG" command exactly once.
   *
   * Idempotency is the server's ledger, not this component: a coordinator
   * remount or a second poll must surface the existing job rather than submit
   * again. A blocked gate is parked, not discarded — the user resolves the
   * blocker and presses Solve, which consumes the same request. */
  const consumeSolveCommand = useCallback(async () => {
    if (solveCommandInFlight.current) return;
    solveCommandInFlight.current = true;
    try {
      // Reading the marker is advisory, like the status heartbeat: an older
      // server or a transient failure must not raise an error banner over a
      // workflow that has not asked for anything.
      const pending = await getSolveCommand().catch(() => null);
      const command = pending?.command;
      if (!pending || !command) return;
      if (pending.outcome) {
        if (solveCommandSeen.current === command.commandId) return;
        solveCommandSeen.current = command.commandId;
        if (pending.outcome.state === 'refused') {
          // A refusal is the user's to see, and it renders only in CAD mode.
          enterCadWorkspace();
          setError(pending.outcome.reason ?? 'Fusion asked WG to solve a return it could not use.');
        } else {
          setStatus('Fusion already asked WG to solve this geometry; its run is in the Jobs rail.');
        }
        return;
      }
      if (solveCommandSeen.current === command.commandId) return;
      solveCommandSeen.current = command.commandId;
      setStatus('Fusion asked WG to solve this model. Preparing…');
      // The request is only actionable from the workspace that renders it.
      enterCadWorkspace();
      const bundle = bundles.find((item) => item.bundlePath === command.bundlePath)
        ?? (await listReturns()).items.find((item) => item.bundlePath === command.bundlePath);
      if (!bundle?.readable) {
        const reason = 'Fusion asked WG to solve a return that is not readable in the workspace.';
        setError(reason);
        await reportSolveCommandOutcome({ commandId: command.commandId, state: 'refused', jobId: null, reason });
        return;
      }
      if (returnBelongsToAnotherProject(bundle, useDocumentStore.getState().identity?.designId)) {
        const reason = 'Fusion asked WG to solve a return from another CAD-linked project. Open that project from File → CAD-linked designs, then send the solve again.';
        setError(reason);
        await reportSolveCommandOutcome({ commandId: command.commandId, state: 'refused', jobId: null, reason });
        return;
      }
      // Parked from here on. Everything below is either terminal or a gate the
      // user can satisfy, and the marker survives a gate — so WG has to keep
      // owning the request until a solve consumes it or the user dismisses it.
      parkedSolveCommandStore.park({
        commandId: command.commandId,
        bundlePath: command.bundlePath,
        blockers: [],
        parkedAt: command.requestedAt || new Date().toISOString(),
      });
      autoIngestPending.current = false;
      const continuity = useCadReturnStore.getState().selectArrivedBundle(bundle);
      await ingestSelected();
      if (continuity === 'reset') {
        const blocker = 'Review the new source inventory and solve settings before solving.';
        parkedSolveCommandStore.setBlockers(command.commandId, [blocker]);
        setStatus('Prepared the model Fusion sent. Its source inventory changed — review mesh, channel, and solve settings, then press Solve.');
        return;
      }
      const outcome = await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
      if (outcome === 'busy') {
        parkedSolveCommandStore.setBlockers(command.commandId, ['a solve is already running']);
        setStatus('Prepared the model Fusion sent. A solve is already running — start it from the CAD Link panel when that one finishes.');
        return;
      }
      setStatus('Solving the model Fusion sent.');
      // The accepted outcome is reported by the solve path itself, which is
      // the only place that knows the job id.
    } catch (reason) {
      if (reason instanceof SupersededError) return;
      const message = reason instanceof Error ? reason.message : String(reason);
      if (mounted.current) setError(`Fusion asked WG to solve this model, but: ${message}`);
      if (reason instanceof SolveEngineUnavailableError) {
        // Imported geometry is solved on Metal by definition, so this command
        // can never succeed here. Refusing it is the only terminal answer;
        // parking it would replay the same failure on every page load.
        await refuseParkedSolveCommand(message);
        return;
      }
      // Not terminal: a gate or a transient failure keeps the request, and the
      // panel offers it back once the user has dealt with the reason.
      const parked = parkedSolveCommandStore.getSnapshot().command;
      if (parked) parkedSolveCommandStore.setBlockers(parked.commandId, [message]);
    } finally {
      solveCommandInFlight.current = false;
      if (autoIngestPending.current && !parkedSolveCommandStore.getSnapshot().command) {
        autoIngestSelected();
      }
    }
  }, [autoIngestSelected, bundles, ingestSelected]);

  // Same cadence as the returns poll, and Fusion-only: Onshape has no marker.
  useEffect(() => {
    if (onshape) return undefined;
    if (pageIsVisible()) void consumeSolveCommand();
    const timer = window.setInterval(() => {
      if (pageIsVisible()) void consumeSolveCommand();
    }, 2_500);
    return () => window.clearInterval(timer);
  }, [consumeSolveCommand, onshape]);

  // Browsers heavily throttle timers in background tabs. Do no pointless I/O
  // while hidden, then reconcile every Fusion-facing channel immediately when
  // the user returns instead of waiting for the next timer slot.
  useEffect(() => {
    if (onshape) return undefined;
    const resume = () => {
      if (!pageIsVisible()) return;
      void refresh({ background: true, autoOpenNew: true });
      void refreshFusionStatus();
      void consumeSolveCommand();
    };
    document.addEventListener('visibilitychange', resume);
    return () => document.removeEventListener('visibilitychange', resume);
  }, [consumeSolveCommand, onshape, refresh, refreshFusionStatus]);

  const clearFeedback = useCallback(() => { setError(null); setStatus(null); }, []);
  const reportError = useCallback((message: string) => setError(message), []);
  const reportStatus = useCallback((message: string) => setStatus(message), []);

  useEffect(() => {
    publishBridge({
      bundles,
      loading,
      ingesting,
      ingestError,
      sendingToFusion,
      pullingFromFusion,
      error,
      status,
      viewportNotice,
      fusionStatus,
      onshapeStatus,
      onshapeConnection,
      pendingFusionConflict,
      refresh,
      refreshOnshapeStatus,
      returnFromOnshape,
      selectBundle,
      ingest,
      ingestSelected,
      pullFromFusion,
      pullAndSolve,
      solveParkedCommand,
      dismissSolveCommand,
      sendWgToFusion,
      cancelFusionConflict,
      clearFeedback,
      reportError,
      reportStatus,
      reportViewportNotice,
      selectFusionInstance,
      selectOnshapeInstance,
    });
    return () => publishBridge({
      ...bridgeSnapshot,
      bundles: [],
      loading: true,
      ingesting: false,
      ingestError: null,
      sendingToFusion: false,
      pullingFromFusion: false,
      error: null,
      status: null,
      viewportNotice: null,
      fusionStatus: null,
      onshapeStatus: null,
      onshapeConnection: null,
      pendingFusionConflict: false,
      refresh: unavailable,
      refreshOnshapeStatus: unavailableRefreshOnshape,
      returnFromOnshape: unavailable,
      selectBundle: () => undefined,
      ingest: unavailable,
      ingestSelected: unavailable,
      pullFromFusion: unavailable,
      pullAndSolve: unavailable,
      solveParkedCommand: unavailable,
      dismissSolveCommand: unavailable,
      sendWgToFusion: unavailable,
      cancelFusionConflict: () => undefined,
      clearFeedback: () => undefined,
      reportError: () => undefined,
      reportStatus: () => undefined,
      reportViewportNotice: () => undefined,
      selectFusionInstance: () => undefined,
      selectOnshapeInstance: () => undefined,
    });
  }, [
    bundles,
    cancelFusionConflict,
    clearFeedback,
    dismissSolveCommand,
    error,
    fusionStatus,
    ingest,
    ingestSelected,
    ingestError,
    ingesting,
    pullAndSolve,
    pullFromFusion,
    pullingFromFusion,
    sendingToFusion,
    solveParkedCommand,
    loading,
    onshapeConnection,
    onshapeStatus,
    pendingFusionConflict,
    refresh,
    refreshOnshapeStatus,
    returnFromOnshape,
    selectBundle,
    selectFusionInstance,
    selectOnshapeInstance,
    reportError,
    reportStatus,
    reportViewportNotice,
    sendWgToFusion,
    status,
    viewportNotice,
  ]);

  const conflictDialog = useModalDialogFocus<HTMLDivElement>({
    open: pendingFusionConflict,
    onClose: cancelFusionConflict,
    initialFocus: '[data-cad-conflict-cancel]',
  });

  // The conflict dialog renders here, not in any one entry point, so the menu,
  // the rail card, and the panel all pass through the same confirmation.
  if (!pendingFusionConflict) return null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) cancelFusionConflict(); }}>
    <div ref={conflictDialog} className="settings-dialog update-dialog" role="dialog" aria-modal="true" aria-labelledby="cad-conflict-title">
      <header><div><h2 id="cad-conflict-title">Both WG and Fusion changed</h2></div></header>
      <div className="update-dialog-body">
        <p>Sending rebuilds only the linked waveguide from WG. Separate cabinet and mid-woofer bodies stay in Fusion, but direct edits to the linked waveguide are replaced.</p>
        <p>To keep the Fusion edits instead, cancel and bring the Fusion geometry into WG first.</p>
        <div className="update-dialog-actions">
          <button data-cad-conflict-cancel onClick={cancelFusionConflict}>Cancel</button>
          <button className="primary" disabled={sendingToFusion} onClick={() => { void sendWgToFusion({ confirmed: true }).catch(() => undefined); }}>Continue: send WG changes</button>
        </div>
      </div>
    </div>
  </div>;
}
