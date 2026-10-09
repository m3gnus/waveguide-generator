import { exteriorModeBlocker } from '../jobs/importedSubmission';
import {
  putProjectSetup,
  putSolverSelection,
  type CadSolveSetup,
  type CadSourceInventoryEntry,
} from '../api/cadOperations';
import { useCadPreparationStore } from '../stores/cadPreparation';
import {
  channelAcceptsDriver,
  channelDriverWire,
  combineWire,
  hasPassiveCardioidSurface,
  incompleteDriverChannels,
  passiveCardioidBlocker,
  passiveCardioidWire,
  useCadReturnStore,
} from '../stores/cadReturn';
import { useDocumentStore } from '../stores/document';
import { polarValidationError, useSolveOptionsStore } from '../stores/solveOptions';
import { subscribeSolveSettingsEdits } from '../stores/solveSettingsEdits';
import { workspaceModeStore } from '../stores/workspaceMode';

/**
 * What the backend prepares a Fusion solve of this project from
 * (CAD-OPERATIONS.md, "Project setups"): its settings for exactly this source
 * inventory. Nothing on the backend reads the live UI, so a solve Fusion sends
 * for a project that is not open is prepared from what was recorded here.
 */
export interface CadProjectSetup {
  lineageId: string;
  inventory: CadSourceInventoryEntry[];
  setup: CadSolveSetup;
}

type CadReturnSnapshot = ReturnType<typeof useCadReturnStore.getState>;
type SolveOptionsSnapshot = ReturnType<typeof useSolveOptionsStore.getState>;
type CadPreparationSnapshot = ReturnType<typeof useCadPreparationStore.getState>;

const SETUP_DEBOUNCE_MS = 750;
/** A failed recording is tried again after these waits, then given up on. */
const SETUP_RETRY_DELAYS_MS = [1_000, 4_000];

/** The project these settings belong to, as the solve profile files them. */
function projectLineage(state: CadReturnSnapshot): string | null {
  return state.ingestRecord?.project?.lineage_id
    ?? state.projectLineageId
    ?? useDocumentStore.getState().identity?.lineageId
    ?? null;
}

/**
 * The setup revision content for the open CAD project.
 *
 * The same fields `buildImportedSubmission` sends, minus everything the
 * snapshot contributes (type, ingest and hashes, acknowledged findings) and
 * without widening the polar grid, which the backend does from the snapshot it
 * prepares. The engine is the solver selector's. Null while the settings are
 * not a solve the Solve button would accept either. `lineageId` files it under
 * a project the caller knows better, such as the one the backend names for an
 * operation's snapshot.
 */
export function buildCadProjectSetup(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
  solveStore: SolveOptionsSnapshot = useSolveOptionsStore.getState(),
  preparation: CadPreparationSnapshot = useCadPreparationStore.getState(),
  lineageId: string | null = projectLineage(state),
): CadProjectSetup | null {
  const bundle = state.selectedBundle;
  if (bundle?.bundleOrigin === 'native') return null;
  if (!bundle || !bundle.sources.length || !lineageId) return null;
  if (incompleteDriverChannels(state).length || exteriorModeBlocker(state, solveStore)) return null;
  const cardioidPresent = hasPassiveCardioidSurface(bundle.sources);
  if (cardioidPresent && passiveCardioidBlocker(state)) return null;
  if (polarValidationError(solveStore.polar)) return null;
  let options: Record<string, unknown>;
  try {
    options = { ...solveStore.options() };
  } catch {
    // An unparseable frequency list: there is no sweep to record yet.
    return null;
  }
  if (solveStore.frequencyMode === 'range') {
    options.frequency_range = [state.frequencyStartHz, state.frequencyEndHz];
    options.num_frequencies = state.frequencyCount;
  }
  options.engine = solveStore.engine;
  options.symmetry = 'auto';
  const drivers = new Map(state.driveChannels.map((channel) => [
    channel.id,
    channelAcceptsDriver(channel) ? channelDriverWire(state.channelDrivers[channel.id]) : undefined,
  ]));
  const driven = [...drivers.values()].some(Boolean) || state.driveChannels.some(channel => channel.exterior_transducer);
  const combine = combineWire(state);
  const passiveCardioid = cardioidPresent ? passiveCardioidWire(state.passiveCardioid) : null;
  const driverReferences = Object.fromEntries(state.driveChannels.flatMap((channel) => {
    const preset = state.channelDrivers[channel.id]?.preset;
    return drivers.get(channel.id) && preset
      ? [[channel.id, { driver_id: preset.id, source: preset.source }]]
      : [];
  }));
  return {
    lineageId,
    // The roles exactly as the return states them; the backend owns their
    // canonical form.
    inventory: bundle.sources.map(({ id, role, required }) => ({ id, role, required })),
    setup: {
      schema_version: 1,
      geometry: {
        drive_channels: state.driveChannels.map((channel) => {
          const driver = drivers.get(channel.id);
          return { ...channel, source_ids: [...channel.source_ids], ...(driver ? { driver } : {}) };
        }),
        ...(driven
          ? {
            drive_voltage_v: state.driveVoltageV,
            ...(state.maxDriveVoltageV ? { max_drive_voltage_v: state.maxDriveVoltageV } : {}),
          }
          : {}),
        mesh: {
          rigid_size_mm: state.rigidSizeMm,
          transition_mm: state.transitionMm,
          source_size_mm: Object.fromEntries(
            Object.entries(state.sourceSizesMm).filter(([id]) => !state.skippedSourceIds.includes(id)),
          ),
        },
        skipped_source_ids: [...state.skippedSourceIds],
        exterior_only: state.exteriorOnly,
        ...(combine ? { combine } : {}),
        ...(passiveCardioid ?? {}),
      },
      options,
      preparation: {
        area_drift_overrides: [...state.areaDriftOverrides],
        symmetry_mode: preparation.symmetryMode,
      },
      driver_references: driverReferences,
    },
  };
}

/** Which project, inventory and return the settings on screen belong to. */
function selectionKey(state: CadReturnSnapshot): string | null {
  const bundle = state.selectedBundle;
  const lineageId = projectLineage(state);
  if (!bundle || !lineageId) return null;
  return JSON.stringify([
    lineageId,
    bundle.sources.map(({ id, role, required }) => [id, role, required]),
    bundle.bundlePath,
  ]);
}

/**
 * Record the open project's setup when the user changes its settings, and the
 * solver selection whenever it changes.
 *
 * Only an edit the user makes in the CAD workspace is recorded. A selection,
 * an ingestion or a recalled run changes the same stores without anyone
 * choosing anything -- a first-time model's defaults, or one project's
 * settings carried into the next -- and a solve option edited for the
 * parametric design is not the retained CAD project's. "Use these settings and
 * solve" records the same setup on demand (CadLinkCoordinator).
 */
export function startCadSetupPublisher(
  options: {
    fetcher?: typeof fetch;
    debounceMs?: number;
    /** Waits before each retry of a failed recording; its length bounds the attempts. */
    retryDelaysMs?: number[];
    /** `failed` once every attempt at an edit has failed; `saved` when a later one lands. */
    onSaveState?: (state: 'failed' | 'saved') => void;
  } = {},
): () => void {
  const fetcher = options.fetcher ?? fetch;
  const debounceMs = options.debounceMs ?? SETUP_DEBOUNCE_MS;
  const retryDelays = options.retryDelaysMs ?? SETUP_RETRY_DELAYS_MS;
  const onSaveState = options.onSaveState ?? (() => undefined);
  let selection = selectionKey(useCadReturnStore.getState());
  let published: string | null = null;
  let pending: CadProjectSetup | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let solverSelection: string | null = null;
  let failed = false;
  // The recording that failed and waits for its next attempt.
  let retrying: { next: CadProjectSetup; key: string; attempt: number; timer: ReturnType<typeof setTimeout> } | null = null;
  let stopped = false;

  const send = (next: CadProjectSetup, key: string, attempt: number, keepalive: boolean) => {
    retrying = null;
    putProjectSetup(next, fetcher, { keepalive }).then(() => {
      if (failed && published === key) { failed = false; onSaveState('saved'); }
    }, () => {
      // A newer edit has taken over: it is what needs recording now.
      if (published !== key) return;
      if (attempt < retryDelays.length && !stopped) {
        const wait = setTimeout(() => send(next, key, attempt + 1, false), retryDelays[attempt]);
        retrying = { next, key, attempt: attempt + 1, timer: wait };
        return;
      }
      published = null;
      if (!failed) { failed = true; onSaveState('failed'); }
    });
  };

  const flush = (keepalive = false) => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    const next = pending;
    pending = null;
    if (!next) return;
    const key = JSON.stringify(next);
    if (key === published) return;
    if (retrying) clearTimeout(retrying.timer);
    retrying = null;
    published = key;
    send(next, key, 0, keepalive);
  };

  // The page is going away or out of sight: nothing later will retry for us.
  const leave = () => {
    flush(true);
    if (retrying) {
      const { next, key, attempt, timer: wait } = retrying;
      clearTimeout(wait);
      send(next, key, attempt, true);
    }
  };
  const onVisibility = () => { if (document.visibilityState === 'hidden') leave(); };

  const schedule = (next: CadProjectSetup) => {
    pending = next;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(() => flush(), debounceMs);
  };

  const onEdit = () => {
    if (workspaceModeStore.getSnapshot().mode !== 'cad') return;
    const built = buildCadProjectSetup();
    if (built) schedule(built);
  };

  // An edit still waiting belongs to the project it was made in.
  const onSelection = () => {
    const next = selectionKey(useCadReturnStore.getState());
    if (next === selection) return;
    selection = next;
    flush();
  };

  const observeSolverSelection = () => {
    const { engine, accuracy } = useSolveOptionsStore.getState();
    const next = JSON.stringify({ engine, accuracy });
    if (next === solverSelection) return;
    solverSelection = next;
    void putSolverSelection({ engine, accuracy }, fetcher).catch(() => {
      if (solverSelection === next) solverSelection = null;
    });
  };

  const unsubscribers = [
    subscribeSolveSettingsEdits(onEdit),
    useCadReturnStore.subscribe(onSelection),
    useSolveOptionsStore.subscribe(observeSolverSelection),
  ];
  if (typeof document !== 'undefined') document.addEventListener('visibilitychange', onVisibility);
  if (typeof window !== 'undefined') window.addEventListener('pagehide', leave);
  observeSolverSelection();
  return () => {
    unsubscribers.forEach((unsubscribe) => unsubscribe());
    if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', onVisibility);
    if (typeof window !== 'undefined') window.removeEventListener('pagehide', leave);
    flush();
    stopped = true;
    if (retrying) clearTimeout(retrying.timer);
    retrying = null;
  };
}
