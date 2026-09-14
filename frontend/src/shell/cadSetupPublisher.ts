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
  projectChannelDrivers,
  useCadReturnStore,
} from '../stores/cadReturn';
import { useDocumentStore } from '../stores/document';
import { polarValidationError, useSolveOptionsStore } from '../stores/solveOptions';

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
 * not a solve the Solve button would accept either.
 */
export function buildCadProjectSetup(
  state: CadReturnSnapshot = useCadReturnStore.getState(),
  solveStore: SolveOptionsSnapshot = useSolveOptionsStore.getState(),
  preparation: CadPreparationSnapshot = useCadPreparationStore.getState(),
): CadProjectSetup | null {
  const bundle = state.selectedBundle;
  const lineageId = projectLineage(state);
  if (!bundle || !bundle.sources.length || !lineageId) return null;
  if (incompleteDriverChannels(state).length) return null;
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
  const driven = [...drivers.values()].some(Boolean);
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
 * Record the open project's setup as its settings change, and the solver
 * selection whenever it changes.
 *
 * A setup is recorded when the user changes a setting, or when a model is
 * selected whose project already has saved settings. A model selected for the
 * first time starts from defaults nobody chose, so it is left for the backend
 * to answer `setup_required` about rather than recorded as the project's.
 */
export function startCadSetupPublisher(
  options: { fetcher?: typeof fetch; debounceMs?: number } = {},
): () => void {
  const fetcher = options.fetcher ?? fetch;
  const debounceMs = options.debounceMs ?? SETUP_DEBOUNCE_MS;
  let selection: string | null = null;
  let observed: string | null = null;
  let published: string | null = null;
  let pending: CadProjectSetup | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let engine: string | null = null;

  const flush = () => {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    const next = pending;
    pending = null;
    if (!next) return;
    const key = JSON.stringify(next);
    if (key === published) return;
    published = key;
    // Advisory: a failed recording is retried by the next change, and until
    // then the backend waits for a setup rather than guessing one.
    void putProjectSetup(next, fetcher).catch(() => { if (published === key) published = null; });
  };

  const schedule = (next: CadProjectSetup) => {
    pending = next;
    if (timer !== null) clearTimeout(timer);
    timer = setTimeout(flush, debounceMs);
  };

  const observe = () => {
    const state = useCadReturnStore.getState();
    const built = buildCadProjectSetup(state);
    const key = built ? JSON.stringify(built) : null;
    const nextSelection = selectionKey(state);
    if (nextSelection !== selection) {
      // The previous project's last edit is still its own.
      flush();
      selection = nextSelection;
      observed = key;
      const bundle = state.selectedBundle;
      if (built && bundle && projectChannelDrivers(bundle, state.projectLineageId) !== null) schedule(built);
      return;
    }
    if (!built || key === observed) return;
    observed = key;
    schedule(built);
  };

  const observeEngine = () => {
    const next = useSolveOptionsStore.getState().engine;
    if (next === engine) return;
    engine = next;
    void putSolverSelection(next, fetcher).catch(() => { if (engine === next) engine = null; });
  };

  const unsubscribers = [
    useCadReturnStore.subscribe(observe),
    useCadPreparationStore.subscribe(observe),
    useDocumentStore.subscribe(observe),
    useSolveOptionsStore.subscribe(() => { observeEngine(); observe(); }),
  ];
  observeEngine();
  observe();
  return () => {
    unsubscribers.forEach((unsubscribe) => unsubscribe());
    flush();
  };
}
