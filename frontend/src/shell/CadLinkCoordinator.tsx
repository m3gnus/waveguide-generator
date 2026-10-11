import { showIngestedMeshInViewport } from './cadlink/artifactLoader';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  getFusionCadStatus,
  type CadReturnBundle,
  type CadReturnIngestRecord,
  type FusionCadStatus,
} from '../api/cadlink';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import {
  cancelCadOperation,
  prepareCadOperation,
  reconcileCadOperation,
  type CadOperationApprovals,
  type CadOperationSummary,
} from '../api/cadOperations';
import type { WgLinkExportResponse } from '../api/designIo';
import { getOnshapeConnection, getOnshapeStatus, type OnshapeConnection, type OnshapeStatus } from '../api/onshape';
import { usePreferences } from '../prefs/preferences';
import { useCadReturnStore } from '../stores/cadReturn';
import { useDesignStore } from '../stores/design';
import { useDocumentStore, type DesignIdentity } from '../stores/document';
import { documentSettingsSignature } from '../stores/designWire';
import { connectCadOperations, displayedSends, pendingCadOperations, useCadOperationsStore } from '../stores/cadOperations';
import { cadCoordinationOff, cadCoordinationStore } from '../api/cadCoordination';
import { agedFusionStatus, statusExpiresAt } from './cadWorkflowView';
import { solveAttention } from './solveAttention';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';
import { workspaceModeStore } from '../stores/workspaceMode';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { recordOnScreenSettings } from './cadOnScreenSettings';
import { useModalDialogFocus } from './dialogFocus';
import {
  createCadArrivalActions,
  enterCadWorkspace,
  SupersededError,
  useCadReturnArrivals,
  type RefreshOptions,
} from './cadlink/arrivals';
import { useCadSend } from './cadlink/send';
import { solvedCadModels } from './cadlink/solvedModel';
import {
  refreshChannelDriverBases,
  restoreCadJobModel,
  useCadRestores,
} from './cadlink/restores';

export {
  SupersededError,
  newestReturnArrival,
  returnBelongsToAnotherProject,
  returnBelongsToProject,
} from './cadlink/arrivals';
export { cadHistorySetup, refreshChannelDriverBases } from './cadlink/restores';

/** A report belonging to one error message: the protocol-level evidence a
 * surface puts behind a disclosure rather than in the sentence it shows.
 *
 * `message` names the error it belongs to, so a surface renders it only while
 * that exact error is the one on screen. */
export interface ErrorDiagnostics {
  message: string;
  detail: string;
}

interface CadLinkCoordinatorSnapshot {
  bundles: CadReturnBundle[];
  loading: boolean;
  ingesting: boolean;
  ingestError: string | null;
  sendingToFusion: boolean;
  pullingFromFusion: boolean;
  error: string | null;
  errorDiagnostics: ErrorDiagnostics | null;
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
  /** Solve now: the backend prepares a CAD operation from its project's own
   * setup and submits it. */
  solveOperation(operationId: string): Promise<void>;
  /** Approve the blocking findings the user reviewed, on the preparation
   * that reported them, and solve. */
  approveOperation(operationId: string, approvals: CadOperationApprovals): Promise<void>;
  dismissOperation(operationId: string): Promise<void>;
  /** Re-read Fusion evidence for a mutation that stopped after it began. */
  reconcileOperation(operationId: string): Promise<void>;
  /** Record the settings on screen as the model's project setup, then
   * prepare the operation with exactly that revision. */
  solveOperationWithSettings(operationId: string): Promise<void>;
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
  errorDiagnostics: null,
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
  solveOperation: unavailable,
  approveOperation: unavailable,
  dismissOperation: unavailable,
  reconcileOperation: unavailable,
  solveOperationWithSettings: unavailable,
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

/** Operations parked on the user -- a solve at one of its gates, a Fusion edit
 * waiting for recovery -- change only when the user acts or a push arrives.
 * Neither the returns listing nor Fusion's status can move them, so with WG's
 * coordination gate off they are not a reason to read either on a clock. */
const PARKED_ON_THE_USER: ReadonlySet<string> = new Set(['needs_user_input', 'recovery_required']);
export function movingOnItsOwn(operation: CadOperationSummary): boolean {
  return !PARKED_ON_THE_USER.has(operation.state);
}

/** Only ever a hint. WG ships as a WebView2 window, not a browser tab, and a
 * window sitting behind other windows still reports `visible` — so this saves
 * no work at all in the packaged app. It is kept because it is correct and
 * free in a real browser; nothing below may depend on it to stay idle. */
function pageIsVisible(): boolean {
  return document.visibilityState !== 'hidden';
}

/** Poll cadences, in milliseconds.
 *
 * Mutable and exported so tests can shorten the quiet window: backoff is a
 * property of elapsed time, and a test that had to wait out the real one would
 * have to advance half a minute of fake time for every case.
 *
 * The `*Idle` figures are what an app nobody is using costs. The base figures
 * are what CAD work costs, and only those are latency the user can feel. */
export const cadPollIntervals = {
  returnsMs: 2_500,
  returnsIdleMs: 30_000,
  fusionStatusMs: 2_500,
  fusionStatusIdleMs: 30_000,
  /** How long without any sign of CAD activity before the polls widen. */
  quietMs: 30_000,
};

const cadPollDefaults = { ...cadPollIntervals };

export function resetCadPollIntervals(): void {
  Object.assign(cadPollIntervals, cadPollDefaults);
}

/** A timer whose period is recomputed immediately before every tick, and which
 * can suspend itself entirely by asking for `null`.
 *
 * `window.setInterval` fixes its period when it is created, so a poll that
 * wants to widen while nothing is happening would have to tear its timer down
 * and rebuild it on every input the cadence depends on. A self-rescheduling
 * timeout asks `period()` again each time it fires, so backing off and snapping
 * back are the same mechanism seen from two answers.
 *
 * The next tick is scheduled *before* the current one runs, exactly as
 * `setInterval` does: a slow poll must not stretch the interval behind it.
 *
 * `restart` is registered so that any sign of activity can drop a long idle
 * delay on the floor — the user must never wait out an interval that was
 * chosen while they were away. */
function startAdaptivePoll(
  restarts: Set<() => void>,
  tick: () => void,
  period: () => number | null,
): () => void {
  let timer: number | null = null;
  const schedule = () => {
    const delay = period();
    timer = delay === null ? null : window.setTimeout(fire, delay);
  };
  const fire = () => {
    timer = null;
    schedule();
    tick();
  };
  const restart = () => {
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
    schedule();
  };
  restarts.add(restart);
  schedule();
  return () => {
    restarts.delete(restart);
    if (timer !== null) window.clearTimeout(timer);
    timer = null;
  };
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

/** What a waiting solve is, in a progress line: the user's own solve when it
 * was started from Simulation, otherwise the model Fusion sent. */
function operationSubject(operationId: string): string {
  return operationId.startsWith('manual-solve:') ? 'your solve' : 'the model Fusion sent';
}

/** Show the CAD workspace and focus its panel.
 *
 * Every status line, error and finding on the CAD return leg
 * renders inside `CadLinkPanel`, which exists only in CAD mode — so a return
 * that arrives while WG shows the parametric design is otherwise completely
 * invisible. The mode store adds the dock panel synchronously, which is why
 * the activation on the next line lands instead of returning false. */
export { enterCadWorkspace } from './cadlink/arrivals';

export { showIngestedMeshInViewport, showIngestedSolverMeshInViewport } from './cadlink/artifactLoader';

/** Restore the immutable model and setup behind an archived CAD run. */
export function showCadJobModel(
  job: JobItem,
  fetcher: typeof fetch = fetch,
): Promise<boolean> {
  const coordinator = cadLinkCoordinatorBridge.getSnapshot();
  return restoreCadJobModel(job, {
    enterCadWorkspace,
    reportStatus: coordinator.reportStatus,
    reportViewportNotice: coordinator.reportViewportNotice,
    showIngestedMesh: showIngestedMeshInViewport,
  }, fetcher);
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
  const [ingesting, setIngesting] = useState(false);
  const [ingestError, setIngestError] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Evidence for one error, kept beside it rather than in it: the message it
  // belongs to is stored with it, so a later unrelated error hides this
  // without every setError call site having to remember to clear it.
  const [errorDiagnostics, setErrorDiagnostics] = useState<ErrorDiagnostics | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [viewportNotice, setViewportNotice] = useState<string | null>(null);
  const [fusionStatus, setFusionStatus] = useState<FusionCadStatus | null>(null);
  const [selectedFusionInstanceId, setSelectedFusionInstanceId] = useState<string | null>(null);
  const [onshapeStatus, setOnshapeStatus] = useState<OnshapeStatus | null>(null);
  const [selectedOnshapeInstanceId, setSelectedOnshapeInstanceId] = useState<string | null>(null);
  const [onshapeConnection, setOnshapeConnection] = useState<OnshapeConnection | null>(null);
  const seenReturnRevisions = useRef<Map<string, string> | null>(null);
  const projectOpenPending = useRef(false);
  // When the user last picked a return from the list. A return Fusion wrote,
  // or was asked for, before that pick is older intent than the pick and
  // never takes the selection from it (see `refresh`). Reset by every
  // project switch.
  const manualSelectionAt = useRef<number | null>(null);
  /** How many returns the user has picked by hand: exact where a clock is
   * not -- a pick in the same millisecond a display began still counts. */
  const manualSelections = useRef(0);
  // A return was refused because it names a design other than the open one.
  // Until the user acts on that, WG must not put some third project's geometry
  // on screen in its place: entering CAD Link is how a refusal is shown, and
  // the remembered-project restore below would otherwise take that as an empty
  // mode to fill -- preparing an unrelated, older return and clearing the
  // refusal off the screen on its way past.
  const refusedForeignReturn = useRef(false);
  const refreshRef = useRef<(options?: RefreshOptions) => Promise<void>>(unavailable);
  const returnListRequest = useRef(0);
  const ingestRequest = useRef(0);
  const ingestAbortController = useRef<AbortController | null>(null);
  const fusionStatusRequest = useRef(0);
  // Set from `refreshFusionStatus` below, which the send path needs before it
  // is declared. Same shape as `refreshRef`.
  const fusionStatusReader = useRef<() => Promise<FusionCadStatus | null>>(async () => null);
  const onshapeStatusRequest = useRef(0);
  const onshapeConnectionRequested = useRef(false);
  const mounted = useRef(true);
  const pendingReturnRequestId = useRef<string | null>(null);
  const pendingReturnRequestedAt = useRef<number | null>(null);
  // Set while a caller awaits one exact correlated arrival. The poll loop is
  // still the only thing that discovers returns; this lets a composed action
  // (pull, then ingest, then solve) continue from that discovery.
  const ingestSelectedRef = useRef<() => Promise<CadReturnIngestRecord>>(unavailable);
  const selectBundleRef = useRef<(bundle: CadReturnBundle, projectLineageId?: string | null) => void>(() => undefined);
  const pendingReturnWaiter = useRef<{
    requestId: string;
    settle: (bundle: CadReturnBundle) => void;
    fail: (reason: Error) => void;
  } | null>(null);
  const fusionPullPromise = useRef<Promise<CadReturnBundle> | null>(null);
  const onshape = preferences.cadApplication === 'onshape';

  // --- Poll cadence -------------------------------------------------------
  // Two polls are mounted for the whole life of the app, because the
  // coordinator is: a return or a status change may arrive from outside WG at
  // any moment, and nothing else is listening. At their base rates that is
  // most of a request a second, which is what an idle window with no CAD
  // application anywhere near it used to cost.
  //
  // So the cadence is evidence-driven rather than constant. `null` suspends a
  // poll outright; the wake paths below are what bring it back.
  /** What the last listing said about a CAD workspace folder; null until one
   * has answered. Unknown deliberately polls: an older or still-starting
   * server must not be mistaken for an unconfigured one and silenced. */
  const cadFolderConfigured = useRef<boolean | null>(null);
  /** Whether Fusion itself is running. Someone with Fusion open is doing CAD
   * work even when WG shows the parametric design, and the polls exist to be
   * quick for them. */
  const fusionProcessLive = useRef(false);
  const lastFusionState = useRef<string | null>(null);
  const lastCadActivityAt = useRef(Date.now());
  const pollRestarts = useRef(new Set<() => void>());

  /** Reasons to hold every poll at its base rate, checked at each tick rather
   * than captured, so a mode change or a pending return takes effect on the
   * next tick even if nothing thought to wake the timer. */
  const cadFlowActive = useCallback(() => (
    workspaceModeStore.getSnapshot().mode === 'cad'
    || fusionProcessLive.current
    || pendingReturnRequestId.current !== null
    || pendingReturnWaiter.current !== null
    || fusionPullPromise.current !== null
  ), []);

  /** CAD work the user is waiting on: a request to Fusion still unanswered, or
   * an operation that has not reached an end. Unlike `cadFlowActive`, neither
   * the workspace mode nor a running Fusion counts -- those are standing
   * conditions, and polling on them is exactly the unconditional coordination
   * the gate turns off. */
  const cadWorkInFlight = useCallback(() => (
    pendingReturnRequestId.current !== null
    || pendingReturnWaiter.current !== null
    || fusionPullPromise.current !== null
    || pendingCadOperations(useCadOperationsStore.getState().operations).some(movingOnItsOwn)
  ), []);

  /** Restart every poll at its base rate. Called for anything that means the
   * user is back in the CAD flow — entering the workspace, sending, expecting
   * a return, a listing or status that actually changed, the window regaining
   * focus — so an idle interval chosen minutes ago is never in the way. */
  const noteCadActivity = useCallback(() => {
    lastCadActivityAt.current = Date.now();
    pollRestarts.current.forEach((restart) => restart());
  }, []);

  /** `unconfiguredMs` is what a poll costs while no CAD workspace folder is
   * selected: `null` for the Fusion heartbeat, which has nothing to say until one is, and
   * the idle rate for the returns listing, which is also how WG finds out that
   * a folder has since been chosen without making the user restart. */
  const pollDelayMs = useCallback((
    baseMs: number,
    idleMs: number,
    unconfiguredMs: number | null,
    listing = false,
  ): number | null => {
    // WG's coordination gate (api/cadCoordination). Off: nothing here runs on
    // a clock unless CAD work is in flight -- a Send or pull the user started,
    // or an operation that has not finished. Everything else is event-driven:
    // mount, focus, entering CAD mode, a folder chosen, and operation pushes.
    // One exception: an add-in that does not declare the inbox transfer (an
    // older add-in, or one WG has not heard from) publishes a plain Send only as
    // a return in the folder, and the listing is the only thing that finds it,
    // so the listing keeps today's cadence for it.
    if (cadCoordinationOff() && !(listing && !addinDeclaresInbox.current)) {
      return cadWorkInFlight() ? baseMs : null;
    }
    if (cadFlowActive()) return baseMs;
    if (cadFolderConfigured.current === false) return unconfiguredMs;
    return Date.now() - lastCadActivityAt.current >= cadPollIntervals.quietMs ? idleMs : baseMs;
  }, [cadFlowActive, cadWorkInFlight]);
  /** The connected add-in sends through WG's request inbox (its heartbeat says
   * so); until WG knows that, the listing keeps looking for plain Sends. */
  const addinDeclaresInbox = useRef(false);

  useEffect(() => {
    setSelectedFusionInstanceId(null);
    setSelectedOnshapeInstanceId(null);
  }, [identity?.designId]);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      ingestRequest.current += 1;
      ingestAbortController.current?.abort();
    };
  }, []);

  /** Prepare the selected return. Fusion's solve commands are prepared by the
   * backend from their own retained snapshots, so nothing here waits on one. */
  const autoIngestSelected = useCallback(() => {
    void ingestSelectedRef.current().catch(() => undefined);
  }, []);

  const {
    bundles,
    setBundles,
    loading,
    pullingFromFusion,
    refresh,
    pullFromFusion,
  } = useCadReturnArrivals({
    autoIngestSelected,
    cadFolderConfigured,
    enterCadWorkspace,
    fusionStatus,
    identityDesignId: identity?.designId,
    manualSelectionAt,
    mounted,
    noteCadActivity,
    onshape,
    pageIsVisible,
    pollDelayMs,
    pollRestarts,
    projectOpenPending,
    refreshChannelDriverBases,
    refusedForeignReturn,
    refreshRef,
    returnListRequest,
    seenReturnRevisions,
    pendingReturnRequestId,
    pendingReturnRequestedAt,
    pendingReturnWaiter,
    fusionPullPromise,
    returnsIdleMs: cadPollIntervals.returnsIdleMs,
    returnsMs: cadPollIntervals.returnsMs,
    setError,
    setErrorDiagnostics,
    setStatus,
    startAdaptivePoll,
  });

  const { restoringCadProject } = useCadRestores({
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
  });

  const {
    sendingToFusion,
    pendingFusionConflict,
    sendWgToFusion,
    cancelFusionConflict,
  } = useCadSend({
    designName,
    identity,
    setCadLink,
    fusionStatus,
    mounted,
    noteCadActivity,
    readFusionStatus: fusionStatusReader,
    refresh,
    setError,
    setStatus,
  });

  /** Read Fusion's status, publish it, and answer with what was read.
   *
   * The answer is for a caller that changed the world and must not go on
   * deciding from the status it held before -- the send path, after the folder
   * picker. `null` means there is no status to act on, which is not the same
   * as `closed` or `not_linked`: those are answers.
   */
  const refreshFusionStatus = useCallback(async (): Promise<FusionCadStatus | null> => {
    if (preferences.cadApplication !== 'fusion360') return null;
    const request = ++fusionStatusRequest.current;
    try {
      const next = await getFusionCadStatus(
        design,
        identity,
        selectedBundlePath,
        fetch,
        selectedFusionInstanceId,
      );
      if (!['closed', 'addin_offline', 'addin_outdated', 'no_document', 'not_linked', 'instance_selection_required', 'current', 'stale'].includes(next.state)) {
        return null;
      }
      // A newer read has already answered, so this one is not published. It is
      // not handed back either: a caller that decided from it would be acting
      // on a status the panel is not showing and the newer read has replaced,
      // which is the disagreement between decision and truth that the send
      // guards exist to prevent. `null` sends it back to ask again.
      if (request !== fusionStatusRequest.current) return null;
      setFusionStatus(next);
      fusionProcessLive.current = next.processRunning === true;
      const declares = next.addinInboxTransfer === true;
      if (declares !== addinDeclaresInbox.current) {
        addinDeclaresInbox.current = declares;
        pollRestarts.current.forEach((restart) => restart());
      }
      // A heartbeat that keeps saying `closed` is the evidence for backing
      // off; the first one that says anything else is Fusion arriving, and
      // everything downstream of it wants the base rate again.
      if (next.state !== lastFusionState.current) {
        lastFusionState.current = next.state;
        noteCadActivity();
      }
      return next;
    } catch {
      // Presence is advisory. Workspace and export errors are presented by the
      // actual action; a missed heartbeat must not hide CAD returns.
      return null;
    }
  }, [design, identity, noteCadActivity, preferences.cadApplication, selectedBundlePath, selectedFusionInstanceId]);
  fusionStatusReader.current = refreshFusionStatus;

  // A status held here is aged by the page itself: once its
  // observation passes the freshness window it says what it is -- Fusion's
  // last report, and when -- instead of going on claiming the document matches.
  // One timer per status held, and no request. This applies under either gate:
  // a hidden page or stalled gate-on request is not a fresh observation.
  useEffect(() => {
    const held = fusionStatus;
    const expires = statusExpiresAt(held);
    if (held === null || expires === null) return undefined;
    const timer = window.setTimeout(() => {
      setFusionStatus((current) => (current === held ? agedFusionStatus(held) : current));
    }, Math.max(0, expires - Date.now()));
    return () => window.clearTimeout(timer);
  }, [fusionStatus]);

  // The delivery loop pushes this explicit event when the heartbeat switches
  // add-in session or declaration. A jobs reconnect is the same invalidation.
  // Forget first, so an M1 -> pin transition immediately re-enables legacy
  // Send discovery while the authoritative status read is in flight.
  useEffect(() => {
    if (onshape) return undefined;
    let revision = useCadOperationsStore.getState().addinStatusRevision;
    return useCadOperationsStore.subscribe((state) => {
      if (state.addinStatusRevision === revision) return;
      revision = state.addinStatusRevision;
      addinDeclaresInbox.current = false;
      pollRestarts.current.forEach((restart) => restart());
      if (pageIsVisible()) {
        void refresh({ background: true, autoOpenNew: true });
        void refreshFusionStatus();
      }
    });
  }, [onshape, refresh, refreshFusionStatus]);

  const selectFusionInstance = useCallback((instanceId: string) => {
    setSelectedFusionInstanceId(instanceId);
    setFusionStatus(null);
  }, []);

  useEffect(() => {
    setFusionStatus(null);
    // Forget the last heartbeat with the status it described. A stale "Fusion
    // is running" would otherwise hold every poll at its base rate after the
    // user switched to Onshape, where nothing reads it again to clear it.
    fusionProcessLive.current = false;
    lastFusionState.current = null;
    if (preferences.cadApplication !== 'fusion360') return undefined;
    if (pageIsVisible()) void refreshFusionStatus();
    // Suspended outright while no CAD workspace folder is selected: the add-in
    // writes its heartbeat into that folder, so there is nothing to read.
    const stop = startAdaptivePoll(
      pollRestarts.current,
      () => { if (pageIsVisible()) void refreshFusionStatus(); },
      () => pollDelayMs(
        cadPollIntervals.fusionStatusMs, cadPollIntervals.fusionStatusIdleMs, null,
      ),
    );
    return () => {
      stop();
      fusionStatusRequest.current += 1;
    };
  }, [designRevision, documentSettings, pollDelayMs, preferences.cadApplication, refreshFusionStatus]);

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
    if (instanceId === selectedOnshapeInstanceId) return;
    actions().supersedePreparation();
    setSelectedOnshapeInstanceId(instanceId);
    setOnshapeStatus(null);
  }, [selectedOnshapeInstanceId]);

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

  const reportViewportNotice = useCallback((message: string | null) => setViewportNotice(message), []);

  // The run the user's CAD solve claims brings the model it prepared on screen
  // (cadlink/solvedModel). Subscribed rather than rendered: every progress
  // message would otherwise re-render this coordinator.
  useEffect(() => {
    const settle = () => solvedCadModels.settle(jobsSocket.getSnapshot().jobs, {
      showIngestedMesh: showIngestedMeshInViewport,
      reportViewportNotice,
    });
    settle();
    const stopJobs = jobsSocket.subscribe(settle);
    const stopClaims = solvedCadModels.subscribe(settle);
    return () => { stopJobs(); stopClaims(); };
  }, [reportViewportNotice]);

  const actions = () => createCadArrivalActions({
    ingestRequest, ingestAbortController, mounted, manualSelectionAt, manualSelections,
    refusedForeignReturn, restoringCadProject, sendIntent,
    setIngesting, setIngestError, setError, setStatus, setViewportNotice, setBundles,
    reportViewportNotice, enterCadWorkspace, autoIngestSelected, rereadDrivers: () => rereadDrivers(),
    noteCadActivity, refresh, autoSelectBundleRef,
    fusionInstanceId: () => fusionStatus?.link?.instanceId ?? null,
    onshapeInstanceId: () => selectedOnshapeInstanceId,
  });

  /** Ingest the selected return and hand back the verified record.
   *
   * The panel button wants a void action that reports into the panel; a
   * composed action (pull, ingest, solve) has to know whether the record
   * exists before it can gate a solve on it. This is the composable half:
   * it reports the same feedback and then throws or returns the record. */
  const ingestSelected = useCallback(async (): Promise<CadReturnIngestRecord> => actions().ingestSelected(), [fusionStatus?.link?.instanceId, reportViewportNotice]);
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
  const selectBundleAs = useCallback((bundle: CadReturnBundle, projectLineageId: string | null | undefined, automatic: boolean) => actions().selectBundleAs(bundle, projectLineageId, automatic), [autoIngestSelected, rereadDrivers]);
  const selectBundle = useCallback(
    (bundle: CadReturnBundle, projectLineageId?: string | null) => selectBundleAs(bundle, projectLineageId, false),
    [selectBundleAs],
  );
  selectBundleRef.current = selectBundle;
  const autoSelectBundleRef = useRef(selectBundleAs);
  autoSelectBundleRef.current = selectBundleAs;

  // The panel's button: same work, feedback already presented, nothing thrown.
  const ingest = useCallback(async () => {
    await ingestSelected().catch(() => undefined);
  }, [ingestSelected]);

  const returnFromOnshape = useCallback(async () => actions().returnFromOnshape(), [identity?.designId, reportViewportNotice, selectedOnshapeInstanceId]);

  /** Ask Fusion for its current geometry, prepare it, and start the solve.
   *
   * Each step is the composable action above, so this adds only the staged
   * status and the decision about where to stop: a blocked readiness gate is
   * reported and left for the user, never solved around. */
  const pullAndSolve = useCallback(async (): Promise<'solving' | 'blocked' | 'failed'> => {
    // Pressed now; the solve it ends in reveals its results if the user is
    // still waiting for them by then (shell/solveAttention).
    solveAttention.armSolve();
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

  /** Act on a CAD operation the backend holds. The backend does the work and
   * reports it on the jobs channel; the answer here is only what it recorded
   * when asked, merged like any other update. */
  const actOnOperation = useCallback(async (
    action: () => Promise<CadOperationSummary>,
    done: string | ((summary: CadOperationSummary) => string),
  ): Promise<void> => {
    setError(null);
    try {
      const summary = await action();
      useCadOperationsStore.getState().apply(summary);
      if (mounted.current) setStatus(typeof done === 'string' ? done : done(summary));
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : String(reason));
      // The card that asked offers its action again.
      throw reason;
    }
  }, []);

  /** Solve now: the backend prepares the operation from the setup its project
   * recorded -- never from whatever is open here -- and submits it. */
  const solveOperation = useCallback((operationId: string) => actOnOperation(
    () => { solveAttention.armOperation(operationId); return prepareCadOperation(operationId); },
    `Preparing ${operationSubject(operationId)}. Its run appears in the Jobs rail once it is submitted.`,
  ), [actOnOperation]);

  /** Approve and solve: the findings the user reviewed, on the one preparation
   * that reported them. A new preparation needs its own review. */
  const approveOperation = useCallback((operationId: string, approvals: CadOperationApprovals) => actOnOperation(
    () => { solveAttention.armOperation(operationId); return prepareCadOperation(operationId, { approvals }); },
    `Approved the reviewed findings for this preparation. Preparing and solving ${operationSubject(operationId)}.`,
  ), [actOnOperation]);

  /** Dismiss: the backend reconciles with the jobs first, so a solve whose job
   * already exists follows its job instead of being dismissed. */
  const dismissOperation = useCallback((operationId: string) => actOnOperation(
    () => cancelCadOperation(operationId),
    (summary) => (summary.state === 'accepted'
      ? 'Its solve had already started, so it now follows its job in the Jobs rail. Cancel the job there to stop it.'
      : summary.kind === 'insert_link' || summary.kind === 'update_link'
        ? 'Dismissed this recovery notice. Fusion still needs Undo or repair before you continue modelling.'
        : 'Dismissed the solve Fusion asked for.'),
  ), [actOnOperation]);

  /** Check Fusion again: settle only from document evidence, then refresh the
   * physical-document warning independently of the durable operation card. */
  const reconcileOperation = useCallback(async (operationId: string): Promise<void> => {
    await actOnOperation(
      () => reconcileCadOperation(operationId),
      (summary) => summary.state === 'accepted'
        ? 'Fusion evidence confirms that the update completed.'
        : 'WG found no current Fusion evidence that the update completed. Use Undo in Fusion or repair the link before continuing.',
    );
    await refreshFusionStatus();
  }, [actOnOperation, refreshFusionStatus]);

  /** Use these settings and solve: the settings on screen become the setup of
   * the model's project -- the one the backend names for the snapshot, when it
   * knows -- and the operation is prepared with exactly that revision. */
  const solveOperationWithSettings = useCallback((operationId: string) => actOnOperation(async () => {
    solveAttention.armOperation(operationId);
    const setupRevisionId = await recordOnScreenSettings(operationId);
    return prepareCadOperation(operationId, { setupRevisionId });
  }, 'Recorded these settings for the model’s project. Preparing and solving it; its run appears in the Jobs rail once it is submitted.'),
  [actOnOperation]);

  useEffect(() => {
    const disconnect = connectCadOperations();
    void useCadOperationsStore.getState().load().catch(() => undefined);
    return disconnect;
  }, []);

  // Coming back to WG is the strongest possible sign the user is about to do
  // something, so it both reconciles every Fusion-facing channel at once and
  // resets the cadence — waiting out an idle interval chosen while they were
  // away would be exactly the latency the base rates exist to avoid.
  //
  // `focus` is here because `visibilitychange` is close to useless in the
  // packaged app: WG runs in a WebView2 window, which stays `visible` while it
  // sits behind other windows, so alt-tabbing away fires nothing. It is also
  // the event that fires when the native folder picker Settings opens closes
  // again, which is how choosing a CAD workspace resumes the polls at once
  // instead of on the returns listing's next idle tick.
  useEffect(() => {
    if (onshape) return undefined;
    const resume = () => {
      if (!pageIsVisible()) return;
      addinDeclaresInbox.current = false;
      noteCadActivity();
      void refresh({ background: true, autoOpenNew: true });
      void refreshFusionStatus();
    };
    document.addEventListener('visibilitychange', resume);
    window.addEventListener('focus', resume);
    return () => {
      document.removeEventListener('visibilitychange', resume);
      window.removeEventListener('focus', resume);
    };
  }, [noteCadActivity, onshape, refresh, refreshFusionStatus]);

  // Entering the CAD workspace is the user saying they are working in CAD now.
  // The cadence checks the mode on every tick, but a poll that is already
  // sitting on a thirty-second delay would not notice for thirty seconds.
  useEffect(() => workspaceModeStore.subscribe(() => {
    if (workspaceModeStore.getSnapshot().mode !== 'cad') return;
    noteCadActivity();
    // With the gate off no clock will read them, so entering the workspace
    // reads the returns and Fusion's status once, as the event it is.
    if (cadCoordinationOff() && !onshape) {
      void refresh({ background: true, autoOpenNew: true });
      void refreshFusionStatus();
    }
  }), [noteCadActivity, onshape, refresh, refreshFusionStatus]);

  // The gate arrives with the first returns listing, and its answer
  // re-evaluates every poll's cadence. So does CAD work starting or ending:
  // with the gate off, an operation that is still unfinished is what keeps the
  // reads running, and the last one finishing is what lets them stop.
  useEffect(() => {
    const restartAll = () => pollRestarts.current.forEach((restart) => restart());
    const unsubscribeGate = cadCoordinationStore.subscribe(restartAll);
    let working = pendingCadOperations(useCadOperationsStore.getState().operations).some(movingOnItsOwn);
    const unsubscribeOperations = useCadOperationsStore.subscribe((state) => {
      const next = pendingCadOperations(state.operations).some(movingOnItsOwn);
      if (next === working) return;
      working = next;
      if (cadCoordinationOff()) restartAll();
    });
    return () => {
      unsubscribeGate();
      unsubscribeOperations();
    };
  }, []);

  // An accepted Send is displayed from its event (M1 transfer contract, C4):
  // the backend only retains it, and with the coordination gate off no
  // listing poll would ever notice. The listing is read once, as the event it
  // is, with its usual auto-open: its own record of what it has seen is the
  // dedupe between this event and the listing's sighting of the same arrival,
  // and nothing here is a permanent "already seen" set -- a later Send with a
  // new id naming the same bundle re-selects that model and brings it to the
  // front (E2). A Send WG refused is said, not dropped.
  const handledSends = useRef(new Set<string>());
  // Displays are ordered by when each Send was made, and fenced across every
  // await: an older Send -- a recovery that lands late, two recovered at once
  // -- never replaces a newer arrival, and nothing replaces a return the user
  // selected after the display began (review F3).
  const sendIntent = useRef<{ acceptedSeq: number | null; sentAt: number; picks: number } | null>(null);
  const displaySend = useCallback(async (operation: CadOperationSummary) => actions().displaySend(operation), [noteCadActivity, refresh]);
  useEffect(() => {
    if (onshape) return undefined;
    const handle = (operations: Record<string, CadOperationSummary>) => {
      for (const operation of Object.values(operations)) {
        if (operation.kind !== 'receive_snapshot' || handledSends.current.has(operation.operationId)) continue;
        // Shown before a reload: never shown again (a recovery re-applies it).
        if (displayedSends.has(operation.operationId)) {
          handledSends.current.add(operation.operationId);
          continue;
        }
        if (operation.state === 'rejected') {
          handledSends.current.add(operation.operationId);
          displayedSends.add(operation.operationId);
          const name = operation.snapshot?.documentName ?? 'a model';
          setError(`Fusion sent ${name}, but WG could not take it: ${operation.message ?? operation.reason ?? 'refused'}`);
          enterCadWorkspace();
          continue;
        }
        if (operation.state !== 'accepted') continue;
        handledSends.current.add(operation.operationId);
        displayedSends.add(operation.operationId);
        void displaySend(operation);
      }
    };
    handle(useCadOperationsStore.getState().operations);
    return useCadOperationsStore.subscribe((state) => handle(state.operations));
  }, [displaySend, onshape]);

  // Choosing a CAD workspace folder is the one event that has to reach a
  // coordinator which has switched itself off, and the `focus` above only
  // catches half of it: the native picker takes focus away and gives it back,
  // but the manual path field beside it never leaves the window.
  //
  // `cadFolderConfigured` goes back to unknown rather than straight to
  // configured -- the next listing is what has the authority to say, and until
  // it answers, unknown is the state that keeps polling. Reconciling here
  // rather than only restarting the timers is what makes the CAD Link panel
  // populate on the click that configured it, instead of a poll interval later.
  useEffect(() => {
    if (onshape) return undefined;
    return cadWorkspaceSelection.subscribe(() => {
      cadFolderConfigured.current = null;
      noteCadActivity();
      void refresh({ background: true, autoOpenNew: true });
      void refreshFusionStatus();
    });
  }, [noteCadActivity, onshape, refresh, refreshFusionStatus]);

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
      errorDiagnostics,
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
      solveOperation,
      approveOperation,
      dismissOperation,
      reconcileOperation,
      solveOperationWithSettings,
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
      errorDiagnostics: null,
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
      solveOperation: unavailable,
      approveOperation: unavailable,
      dismissOperation: unavailable,
      reconcileOperation: unavailable,
      solveOperationWithSettings: unavailable,
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
    approveOperation,
    dismissOperation,
    reconcileOperation,
    solveOperationWithSettings,
    error,
    errorDiagnostics,
    fusionStatus,
    ingest,
    ingestSelected,
    ingestError,
    ingesting,
    pullAndSolve,
    pullFromFusion,
    pullingFromFusion,
    sendingToFusion,
    solveOperation,
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
