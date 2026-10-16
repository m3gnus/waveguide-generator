import { useEffect } from 'react';
import { CadLinkApiError } from '../api/cadlink';
import { create } from 'zustand';
import { exteriorModeBlocker, resolveCadImportedSetup } from '../jobs/importedSubmission';
import {
  putProjectSetup,
  putSolverSelection,
  type CadSolveSetup,
  type CadSourceInventoryEntry,
} from '../api/cadOperations';
import { useCadPreparationStore } from '../stores/cadPreparation';
import {
  hasPassiveCardioidSurface,
  incompleteDriverChannels,
  passiveCardioidBlocker,
  useCadReturnStore,
} from '../stores/cadReturn';
import { useDocumentStore } from '../stores/document';
import { useCadConnectionStore } from '../stores/cadConnection';
import { useCadReceiptsStore } from '../stores/cadReceipts';
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
  let resolved: ReturnType<typeof resolveCadImportedSetup>;
  try { resolved = resolveCadImportedSetup(state, solveStore); }
  catch { return null; }
  const driverReferences = Object.fromEntries(state.driveChannels.flatMap((channel) => {
    const preset = state.channelDrivers[channel.id]?.preset;
    return resolved.geometry.drive_channels.find(item => item.id === channel.id)?.driver && preset
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
        ...resolved.geometry,
        ...(state.exteriorOnly ? { exterior_only_region_ids: state.exteriorOnlyRegionIds ? [...state.exteriorOnlyRegionIds] : null } : {}),
      },
      options: { ...resolved.options },
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
 * parametric design is not the retained CAD project's. The job submission records
 * the same setup on demand when Solve is pressed.
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
  let failed = false;
  let solverFailed = false;
  let reportedFailure = false;
  const reportSaveState = () => {
    const next = failed || solverFailed;
    if (next === reportedFailure) return;
    reportedFailure = next;
    onSaveState(next ? 'failed' : 'saved');
  };
  type SolverSelection = { engine: string; accuracy: 'fast' | 'accurate' };
  let solverSelection: SolverSelection | null = null;
  let savedSolverSelection: string | null = null;
  let solverInFlight = false;
  let solverRetry: ReturnType<typeof setTimeout> | null = null;
  // The recording that failed and waits for its next attempt.
  let retrying: { next: CadProjectSetup; key: string; attempt: number; timer: ReturnType<typeof setTimeout> } | null = null;
  let stopped = false;

  const send = (next: CadProjectSetup, key: string, attempt: number, keepalive: boolean) => {
    retrying = null;
    putProjectSetup(next, fetcher, { keepalive }).then(() => {
      if (failed && published === key) { failed = false; reportSaveState(); }
    }, () => {
      // A newer edit has taken over: it is what needs recording now.
      if (published !== key) return;
      if (attempt < retryDelays.length && !stopped) {
        const wait = setTimeout(() => send(next, key, attempt + 1, false), retryDelays[attempt]);
        retrying = { next, key, attempt: attempt + 1, timer: wait };
        return;
      }
      published = null;
      if (!failed) { failed = true; reportSaveState(); }
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

  // One outstanding write: the backend must finish an older choice before
  // it receives the latest one. Changes during a write replace the queued
  // choice rather than publishing intermediate selections out of order.
  const sendSolverSelection = (next: SolverSelection, attempt = 0) => {
    solverInFlight = true;
    const key = JSON.stringify(next);
    const latest = () => solverSelection !== null && JSON.stringify(solverSelection) === key;
    void putSolverSelection(next, fetcher).then(() => {
      solverInFlight = false;
      if (stopped) return;
      savedSolverSelection = key;
      if (!latest()) { sendSolverSelection(solverSelection!); return; }
      solverFailed = false;
      reportSaveState();
    }, (error: unknown) => {
      solverInFlight = false;
      if (stopped) return;
      // An unsuccessful response may follow a committed write. Reassert the
      // current choice even when it matches a previously successful save.
      savedSolverSelection = null;
      if (!latest()) { sendSolverSelection(solverSelection!); return; }
      const status = error instanceof CadLinkApiError ? error.status : null;
      const transient = status === null || status >= 500 || status === 408 || status === 429;
      if (transient && attempt < retryDelays.length) {
        solverRetry = setTimeout(() => {
          solverRetry = null;
          if (!stopped) sendSolverSelection(solverSelection!, attempt + 1);
        }, retryDelays[attempt]);
        return;
      }
      solverFailed = true;
      reportSaveState();
    });
  };

  const observeSolverSelection = () => {
    const { engine, accuracy } = useSolveOptionsStore.getState();
    const next = { engine, accuracy };
    const key = JSON.stringify(next);
    const changed = key !== JSON.stringify(solverSelection);
    solverSelection = next;
    if (solverInFlight) return;
    if (solverRetry !== null) {
      if (!changed) return;
      clearTimeout(solverRetry);
      solverRetry = null;
    }
    if (key !== savedSolverSelection) sendSolverSelection(next);
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
    if (solverRetry !== null) clearTimeout(solverRetry);
    solverRetry = null;
    solverSelection = null;
    if (retrying) clearTimeout(retrying.timer);
    retrying = null;
  };
}

/** Save feedback remains available when the CAD workspace owners are absent. */
export const useCadSetupPublisherStore = create<{ error: string | null }>(() => ({ error: null }));

/** All CAD feedback surfaces give action errors precedence over setup-save failures. */
export function useCadFeedbackError(actionError: string | null): string | null {
  const reconnectError = useCadConnectionStore((state) => state.recoveryError);
  const receiptError = useCadReceiptsStore((state) => state.recoveryError);
  return useCadSetupPublisherStore((state) => actionError ?? reconnectError
    ?? (receiptError ? `WG could not check Fusion Sends (${receiptError}). Refresh CAD Link to try again.` : null) ?? state.error);
}

export function CadSetupPublisher() {
  useEffect(() => startCadSetupPublisher({
    onSaveState: (state) => useCadSetupPublisherStore.setState({
      error: state === 'failed' ? 'Settings not saved. Edit a setting to retry.' : null,
    }),
  }), []);
  return null;
}
