import { useEffect, useSyncExternalStore } from 'react';
import { CadLinkApiError, getFusionCadStatus, type CadReturnBundle, type CadReturnIngestRecord } from '../api/cadlink';
import { getOnshapeConnection, getOnshapeStatus } from '../api/onshape';
import { cadCoordinationStore } from '../api/cadCoordination';
import { jobsSocket } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';
import { useCadConnectionStore } from '../stores/cadConnection';
import { cadReceiptsStore, connectCadReceipts, connectCadReceiptDisplay } from '../stores/cadReceipts';
import { cadRecoveryNoticesStore, connectCadRecoveryNotices } from '../stores/cadRecoveryNotices';
import { frameReadInFlight } from '../stores/cadSolverFrame';
import { useCadReturnStore } from '../stores/cadReturn';
import { cadWorkspace, emptyCadWorkspace } from '../stores/cadWorkspace';
import { meshCeilingChoice } from '../stores/meshCeilingChoice';
import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';
import { subscribeRevision, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { documentSettingsSignature } from '../stores/designWire';
import { useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { connectCadObservation } from './cadObservation';
import { createCadArrivalActions, createCadReturnArrivalReader, enterCadWorkspace, SupersededError } from './cadlink/arrivals';
import { showIngestedMeshInViewport } from './cadlink/artifactLoader';
import { createCadOutboundController } from './cadlink/outbound';
import { createCadReturnWaiter } from './cadlink/returnWaiter';
import { createReceiptDisplay } from './cadlink/receiptDisplay';
import { createFusionSolveDisplay } from './cadlink/fusionSolveDisplay';
import { observeCadProjectLoads, observeLastModel, refreshChannelDriverBases } from './cadlink/restores';
import { solvedCadModels } from './cadlink/solvedModel';
import { solveControlStore } from './JobsCoordinator';
import { solveAttention } from './solveAttention';
import { jobIdFromLink } from '../jobs/jobDeepLink';
import { useModalDialogFocus } from './dialogFocus';

/** Mount independent readers/commands and project their feedback for the UI.
 * Jobs own solves, receipts own Send display, and the waiter alone owns pulls. */
export function mountCadWorkspaceOwners(): () => void {
  const mounted = { current: true };
  const cadFolderConfigured = { current: null as boolean | null };
  const manualSelectionAt = { current: null as number | null };
  const projectOpenPending = { current: false };
  const initialChoiceOwed = { current: !useCadReturnStore.getState().selectedBundle };
  const refusedForeignReturn = { current: false };
  const returnListRequest = { current: 0 };
  const seenReturnRevisions = { current: null as Map<string, string> | null };
  const ingestRequest = { current: 0 };
  const ingestAbortController = { current: null as AbortController | null };
  let fusionInstance: string | null = null;
  let onshapeInstance: string | null = null;
  let onshapeRead = 0;
  let designId = useDocumentStore.getState().identity?.designId;
  let connectionRequested = false;
  const claimedReturns = new Set<string>();
  let receiptDisplaysInFlight = 0;
  const fusionActive = () => mounted.current && preferencesStore.getSnapshot().cadApplication === 'fusion360';
  const awaitingReceiptDisplay = () => receiptDisplaysInFlight > 0
    || Object.keys(cadReceiptsStore.getState().receipts).length > 0;
  let activityRevision = 0;
  const activityListeners = new Set<() => void>();
  const changed = () => activityListeners.forEach((listener) => listener());
  const noteCadActivity = () => { activityRevision += 1; changed(); };
  const publish = (patch: Partial<typeof emptyCadWorkspace>) => {
    if (mounted.current) cadWorkspace.setState(patch);
  };
  const reportError = (error: string | null) => publish({ error });
  const reportStatus = (status: string | null) => publish({ status });
  const reportViewportNotice = (viewportNotice: string | null) => publish({ viewportNotice });
  const refreshOnshapeStatus = async (committed?: ReturnType<typeof useDocumentStore.getState>['identity']) => {
    if (preferencesStore.getSnapshot().cadApplication !== 'onshape') return;
    const started = ++onshapeRead;
    try {
      const status = await getOnshapeStatus(useDesignStore.getState().design,
        committed ?? useDocumentStore.getState().identity, fetch, onshapeInstance);
      if (started === onshapeRead) publish({ onshapeStatus: status });
    } catch { /* Presence is advisory; commands report their failures. */ }
    if (!connectionRequested) {
      connectionRequested = true;
      void getOnshapeConnection().then((onshapeConnection) => publish({ onshapeConnection })).catch(() => undefined);
    }
  };
  const autoIngestSelected = () => { void actions.ingestSelected().catch(() => undefined); };
  const ownsSolveReturn = (bundle: CadReturnBundle) => fusionSolves.ownsReturn(bundle.bundlePath, jobsSocket.getSnapshot().jobs, true);
  const rereadDrivers = () => { void refreshChannelDriverBases().then((channels) => {
    if (channels.length) reportStatus(`Re-read ${channels.length} driver${channels.length === 1 ? '' : 's'} from the driver library.`);
  }).catch(() => undefined); };
  const arrivals = createCadReturnArrivalReader({
    cadFolderConfigured, manualSelectionAt, projectOpenPending, refusedForeignReturn,
    returnListRequest, seenReturnRevisions, initialChoiceOwed, autoIngestSelected, enterCadWorkspace,
    refreshChannelDriverBases, noteCadActivity,
    awaitingReturn: () => waiter.getState().pullingFromFusion,
    awaitingReceiptDisplay,
    sendOwnersKnown: () => cadReceiptsStore.getState().loadedAt !== null,
    ownsSolveReturn,
    ownsReturn: (bundle) => Boolean((bundle.requestId && claimedReturns.has(bundle.requestId))
      || fusionSolves.ownsReturn(bundle.bundlePath, jobsSocket.getSnapshot().jobs)
      || Object.values(cadReceiptsStore.getState().receipts).some((receipt) => receipt.bundle_path === bundle.bundlePath)
      || cadReceiptsStore.getState().frontedBundles.some((entry) => entry.path === bundle.bundlePath)),
    setError: reportError, setStatus: reportStatus,
    setBundles: (bundles) => publish({ bundles }), setLoading: (loading) => publish({ loading }),
  });
  let firstReturnRead = true;
  const startupReceipts = cadReceiptsStore.getState().load();
  void startupReceipts.catch(() => undefined);
  const refreshReturns = async (options?: Parameters<typeof arrivals.refresh>[0]) => {
    if (!fusionActive()) { publish({ loading: false }); return; }
    // Discover the Send identities before any folder entry can be selected.
    // A failed feed read still lists files for manual use, but cannot auto-ingest a Send.
    let sendOwnersKnown = true;
    try {
      const state = cadReceiptsStore.getState();
      // Pushes invalidate loadedAt by starting a read. Background listings can
      // reuse a complete read briefly when they cannot auto-open a new arrival.
      const fresh = state.loadedAt !== null && Date.now() - state.loadedAt < 30_000;
      const read = firstReturnRead && !options?.autoOpenNew ? startupReceipts
        : options?.background && !options.autoOpenNew && fresh ? null : state.load();
      firstReturnRead = false; await read;
    } catch { sendOwnersKnown = false; }
    if (fusionActive()) await arrivals.refresh(sendOwnersKnown ? options
      : { ...options, autoOpenNew: false, deferNewArrivals: true });
  };
  const actions = createCadArrivalActions({
    mounted, ingestRequest, ingestAbortController, manualSelectionAt, refusedForeignReturn,
    autoIngestSelected, enterCadWorkspace, rereadDrivers,
    onshapeInstanceId: () => onshapeInstance,
    setIngesting: (ingesting) => publish({ ingesting,
      ...(ingesting ? { preparationIntent: useCadReturnStore.getState().captureIngestIntent() } : {}),
    }), setIngestError: (ingestError) => publish({ ingestError }),
    setError: reportError, setStatus: reportStatus, setViewportNotice: reportViewportNotice,
    setBundles: (bundles) => publish({ bundles }), reportViewportNotice,
  });
  const outbound = createCadOutboundController({
    selectedInstanceId: () => fusionInstance, noteCadActivity,
    refresh: () => refreshReturns(),
  });
  const waiter = createCadReturnWaiter({
    selectedInstanceId: () => fusionInstance, noteCadActivity,
    onArrival: (bundle) => {
      if (bundle.requestId) claimedReturns.add(bundle.requestId);
      // Automatic continuity is shared with receipts; this callback selects
      // once. A plain pull prepares here; pull-and-solve joins that preparation.
      useCadReturnStore.getState().selectArrivedBundle(bundle);
      enterCadWorkspace();
      rereadDrivers();
      preparation = actions.ingestSelected();
      void preparation.catch(() => undefined);
    },
  });
  let preparation: ReturnType<typeof actions.ingestSelected> | null = null;
  const selectBundle = (bundle: Parameters<typeof actions.selectBundleAs>[0], project?: string | null, automatic = false) => {
    actions.selectBundleAs(bundle, project, automatic);
  };
  const observation = connectCadObservation({
    observeOperation: outbound.observeOperation,
    getActivity: () => ({
      activityRevision,
      cadApplication: preferencesStore.getSnapshot().cadApplication,
      cadFolderConfigured: cadFolderConfigured.current,
      coordination: cadCoordinationStore.getSnapshot(), workspaceMode: workspaceModeStore.getSnapshot().mode,
      // No progress, receipt cursor or mesh generation in the context key.
      observationKey: JSON.stringify([useDocumentStore.getState().identity, useDesignStore.getState().designRevision,
        documentSettingsSignature(), useCadReturnStore.getState().selectedBundle?.bundlePath, fusionInstance]),
      requests: [
        ...Object.entries(outbound.getState().requests).map(([id, state]) => ({ id, state })),
        ...waiter.awaitedRequestIds().map((id) => ({ id, state: 'processing' })),
      ],
      awaitingReturn: waiter.getState().pullingFromFusion,
    }),
    subscribeActivity: (listener) => { activityListeners.add(listener); return () => { activityListeners.delete(listener); }; },
    readFusionStatus: () => getFusionCadStatus(useDesignStore.getState().design, useDocumentStore.getState().identity,
      useCadReturnStore.getState().selectedBundle?.bundlePath ?? null, fetch, fusionInstance),
    reloadReturns: () => refreshReturns({ background: true,
      // Folder arrivals open by themselves unless the add-in reports that its
      // requests travel through the inbox; an older add-in, or one that has
      // not reported (coordination off), keeps opening them.
      autoOpenNew: useCadConnectionStore.getState().fusionStatus?.addinInboxTransfer !== true }),
    reloadReceipts: async (ids) => {
      await cadReceiptsStore.getState().load();
      const pulls = await waiter.reconcile(ids);
      const outboundIds = ids.filter((id) => !pulls.has(id) && Object.hasOwn(outbound.getState().requests, id));
      const sends = await outbound.reconcile(outboundIds);
      return new Set([...pulls, ...sends]);
    },
  });
  /** The one composed Solve after a preparation: the same public Solve the
   * card's button presses, once the prepared mesh is still the selection. */
  const solvePrepared = async (
    prepared: CadReturnIngestRecord | null,
    bundlePath: string,
    choice: ReturnType<ReturnType<typeof useCadReturnStore.getState>['captureModelChoice']>,
  ): Promise<'solving' | 'blocked'> => {
    // This composed command already holds a solve intention. Let the card's
    // initial frame read answer before invoking the same public Solve.
    if (prepared) await frameReadInFlight(prepared.ingest_id);
    const selected = useCadReturnStore.getState();
    if (!prepared || selected.ingestRecord !== prepared || selected.needsIngest
      || !selected.isCurrentModelChoice(choice) || selected.selectedBundle?.bundlePath !== bundlePath) {
      throw new SupersededError('A newer model selection superseded this geometry request.');
    }
    reportStatus('Prepared. Submitting the solve…');
    const control = solveControlStore.getSnapshot();
    if (!control) throw new Error('Solve coordinator is unavailable');
    const outcome = await control.solve();
    if (outcome === 'busy') {
      reportStatus('Prepared the geometry. A solve is already running — press Solve when it finishes.');
      return 'blocked';
    }
    if (outcome === 'blocked') { reportStatus(control.title); return 'blocked'; }
    reportStatus('Solving the current geometry.');
    return 'solving';
  };
  /** A Solve the triangle ceiling stopped waits for the pop-up, whose
   * Solve anyway prepares and solves; it is not a failure. */
  const ceilingStopped = (error: unknown, bundlePath: string | undefined): boolean => {
    if (!(error instanceof CadLinkApiError) || error.code !== 'mesh_too_large' || !bundlePath) return false;
    meshCeilingChoice.solveAfter(bundlePath);
    reportStatus(null);
    return true;
  };
  const pullAndSolve = async (): Promise<'solving' | 'blocked' | 'failed'> => {
    solveAttention.armSolve();
    let bundlePath: string | undefined;
    try {
      const returned = await waiter.pullFromFusion();
      bundlePath = returned.bundlePath;
      reportStatus('Received the current Fusion geometry. Preparing the simulation…');
      const choice = useCadReturnStore.getState().captureModelChoice();
      const prepared = await preparation;
      return await solvePrepared(prepared, returned.bundlePath, choice);
    } catch (error) {
      if (error instanceof SupersededError) return 'blocked';
      if (ceilingStopped(error, bundlePath)) return 'blocked';
      reportStatus(null);
      reportError(error instanceof Error ? error.message : String(error));
      return useCadReturnStore.getState().ingestRecord ? 'blocked' : 'failed';
    }
  };
  const prepareAndSolve = async (options: { meshLimitOverride?: boolean } = {}): Promise<'solving' | 'blocked' | 'failed'> => {
    solveAttention.armSolve();
    const bundlePath = useCadReturnStore.getState().selectedBundle?.bundlePath;
    try {
      if (!bundlePath) throw new Error('Select a CAD return before preparing a simulation.');
      const choice = useCadReturnStore.getState().captureModelChoice();
      const prepared = await actions.ingestSelected(options);
      return await solvePrepared(prepared, bundlePath, choice);
    } catch (error) {
      if (error instanceof SupersededError) return 'blocked';
      if (ceilingStopped(error, bundlePath)) return 'blocked';
      reportStatus(null);
      // A failed preparation already reported itself in CAD Link.
      if (!(error instanceof CadLinkApiError)) reportError(error instanceof Error ? error.message : String(error));
      return useCadReturnStore.getState().ingestRecord ? 'blocked' : 'failed';
    }
  };
  const refresh = async (options?: Parameters<typeof arrivals.refresh>[0]) => {
    observation.refresh({ returns: false });
    await Promise.allSettled([refreshReturns(options), refreshOnshapeStatus(), cadRecoveryNoticesStore.getState().load(), waiter.refresh()]);
  };
  cadWorkspace.setState({ ...emptyCadWorkspace,
    loading: fusionActive() && document.visibilityState !== 'hidden',
    refresh, refreshOnshapeStatus, selectBundle,
    returnFromOnshape: actions.returnFromOnshape,
    ingest: async (options) => { await actions.ingestSelected(options).catch(() => undefined); }, ingestSelected: actions.ingestSelected,
    pullFromFusion: waiter.pullFromFusion, pullAndSolve, prepareAndSolve,
    sendWgToFusion: outbound.sendWgToFusion, cancelFusionConflict: outbound.cancelFusionConflict,
    clearFeedback: () => {
      reportError(null); reportStatus(null);
      outbound.setState({ error: null, status: null }); waiter.setState({ error: null, status: null });
    },
    reportError, reportStatus, reportViewportNotice,
    selectFusionInstance: (id) => {
      if (id === fusionInstance) return;
      fusionInstance = id; actions.supersedePreparation(); waiter.supersede(); changed();
    },
    selectOnshapeInstance: (id) => {
      if (id === onshapeInstance) return;
      onshapeInstance = id; actions.supersedePreparation(); publish({ onshapeStatus: null }); void refreshOnshapeStatus();
    },
  });
  const fusionSolves = createFusionSolveDisplay({ isActive: fusionActive, enterCadWorkspace, reportStatus, reportViewportNotice,
    showIngestedMesh: showIngestedMeshInViewport,
    newestSendAt: () => Math.max(cadReceiptsStore.getState().newestSentAt,
      ...Object.values(cadReceiptsStore.getState().receipts).map((receipt) => Date.parse(receipt.sent_at))),
    onIntent: () => { initialChoiceOwed.current = false; },
  });
  const display = createReceiptDisplay({ enterCadWorkspace, reportViewportNotice, isActive: fusionActive,
    automaticSelectionAt: fusionSolves.latestSentAt,
    refuseAutomaticRestore: () => { refusedForeignReturn.current = true; },
    showIngestedMesh: showIngestedMeshInViewport, manualSelectionAt: () => manualSelectionAt.current });
  let initialRefreshQueued = false;
  const refreshOwedInitialChoice = () => {
    if (initialRefreshQueued || !fusionActive() || !initialChoiceOwed.current || awaitingReceiptDisplay()) return;
    initialRefreshQueued = true;
    queueMicrotask(() => {
      initialRefreshQueued = false;
      if (fusionActive() && initialChoiceOwed.current && !awaitingReceiptDisplay()) {
        void refreshReturns({ background: true });
      }
    });
  };
  const displayReceipt: typeof display = async (receipt) => {
    receiptDisplaysInFlight += 1;
    try { return await display(receipt); }
    finally { receiptDisplaysInFlight -= 1; refreshOwedInitialChoice(); }
  };
  let stopDisplay = connectCadReceiptDisplay(displayReceipt, cadReceiptsStore, fusionActive);
  let application = preferencesStore.getSnapshot().cadApplication;
  const settleModels = () => solvedCadModels.settle(jobsSocket.getSnapshot().jobs, {
    showIngestedMesh: showIngestedMeshInViewport, reportViewportNotice,
  });
  const followFusionSolve = () => { void fusionSolves.observe(jobsSocket.getSnapshot().jobs); };
  const subscribeContext = () => {
    const nextApplication = preferencesStore.getSnapshot().cadApplication;
    if (nextApplication !== application) {
      application = nextApplication;
      initialChoiceOwed.current = !useCadReturnStore.getState().selectedBundle;
      actions.supersedePreparation(); waiter.supersede(); arrivals.cancel();
      stopDisplay(); stopDisplay = connectCadReceiptDisplay(displayReceipt, cadReceiptsStore, fusionActive);
      if (!fusionActive()) publish({ loading: false });
      else fusionSolves.resume();
    }
    changed();
    if (preferencesStore.getSnapshot().cadApplication === 'onshape') void refreshOnshapeStatus();
  };
  let context = '';
  const contextChanged = () => {
    if (useCadReturnStore.getState().selectedBundle) initialChoiceOwed.current = false;
    const nextDesign = useDocumentStore.getState().identity?.designId;
    if (nextDesign !== designId) {
      designId = nextDesign; fusionInstance = onshapeInstance = null; onshapeRead += 1;
    }
    const next = JSON.stringify([useDocumentStore.getState().identity, useDesignStore.getState().designRevision,
      documentSettingsSignature(), useCadReturnStore.getState().selectedBundle?.bundlePath]);
    if (next === context) return;
    context = next; subscribeContext();
  };
  const stops = [
    outbound.subscribe((state, previous) => { changed(); publish({ sendingToFusion: state.sendingToFusion,
      pendingFusionConflict: state.pendingFusionConflict,
      ...(state.error !== previous.error ? { error: state.error } : {}),
      ...(state.status !== previous.status ? { status: state.status } : {}) }); }),
    waiter.subscribe((state, previous) => {
      claimedReturns.clear();
      if (state.requestId) claimedReturns.add(state.requestId);
      const selectedRequest = useCadReturnStore.getState().selectedBundle?.requestId;
      if (selectedRequest) claimedReturns.add(selectedRequest);
      publish({ pullingFromFusion: state.pullingFromFusion, errorDiagnostics: state.errorDiagnostics,
        ...(state.error !== previous.error ? { error: state.error } : {}),
        ...(state.status !== previous.status ? { status: state.status } : {}) }); changed();
    }),
    useCadConnectionStore.subscribe((state) => publish({ fusionStatus: state.fusionStatus })),
    cadReceiptsStore.subscribe((state, previous) => {
      if (Object.keys(previous.receipts).length > 0 && Object.keys(state.receipts).length === 0) refreshOwedInitialChoice();
      if (state.feedback === previous.feedback) return;
      const feedback = Object.values(state.feedback).at(-1);
      if (feedback) publish(feedback.outcome === 'accepted' || feedback.outcome === 'superseded'
        ? { status: feedback.message } : { error: feedback.message });
    }),
    () => stopDisplay(), connectCadReceipts(),
    useCadDeliveryHealthStore.subscribe((state, previous) => {
      if (state.outboundOperations !== previous.outboundOperations) Object.values(state.outboundOperations).forEach(outbound.observeOperation);
    }), connectCadRecoveryNotices(),
    observeCadProjectLoads({ enterCadWorkspace, manualSelectionAt, projectOpenPending, initialChoiceOwed, refusedForeignReturn,
      returnListRequest, seenReturnRevisions, refreshRef: { current: refreshReturns }, selectBundleRef: { current: selectBundle },
      setError: reportError, setStatus: reportStatus }),
    observeLastModel({ reportStatus, reportError, refusedForeignReturn, selectBundle: (bundle, project) => selectBundle(bundle, project, true), jobLinkPending: () => jobIdFromLink(window.location.search) !== null }),
    jobsSocket.subscribe(followFusionSolve), () => fusionSolves.dispose(),
    jobsSocket.subscribe(settleModels), solvedCadModels.subscribe(settleModels),
    useDesignStore.subscribe(contextChanged), useDocumentStore.subscribe(contextChanged),
    useCadReturnStore.subscribe(contextChanged), useSolveOptionsStore.subscribe(contextChanged), useImportedSolveOptionsStore.subscribe(contextChanged),
    workspaceModeStore.subscribe(changed), preferencesStore.subscribe(subscribeContext),
    cadCoordinationStore.subscribe(changed), cadWorkspaceSelection.subscribe(() => observation.refresh()),
    subscribeRevision((event) => { if (event.reason !== 'load') actions.supersedePreparation(); }),
  ];
  Object.values(useCadDeliveryHealthStore.getState().outboundOperations).forEach(outbound.observeOperation);
  contextChanged(); followFusionSolve(); settleModels(); void refreshOnshapeStatus();
  return () => {
    mounted.current = false;
    stops.forEach((stop) => stop()); observation(); arrivals.cancel();
    waiter.dispose(); outbound.dispose(); actions.supersedePreparation();
    cadWorkspace.setState(emptyCadWorkspace);
  };
}

export function CadWorkspaceOwners() {
  useEffect(mountCadWorkspaceOwners, []);
  const state = useSyncExternalStore(cadWorkspace.subscribe, cadWorkspace.getSnapshot, cadWorkspace.getSnapshot);
  const conflictDialog = useModalDialogFocus<HTMLDivElement>({ open: state.pendingFusionConflict,
    onClose: state.cancelFusionConflict, initialFocus: '[data-cad-conflict-cancel]' });
  if (!state.pendingFusionConflict) return null;
  return <div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) state.cancelFusionConflict(); }}>
    <div ref={conflictDialog} className="settings-dialog update-dialog" role="dialog" aria-modal="true" aria-labelledby="cad-conflict-title">
      <header><div><h2 id="cad-conflict-title">Both WG and Fusion changed</h2></div></header>
      <div className="update-dialog-body">
        <p>Sending rebuilds only the linked waveguide from WG. Separate cabinet and mid-woofer bodies stay in Fusion, but direct edits to the linked waveguide are replaced.</p>
        <p>To keep the Fusion edits instead, cancel and bring the Fusion geometry into WG first.</p>
        <div className="update-dialog-actions">
          <button data-cad-conflict-cancel onClick={state.cancelFusionConflict}>Cancel</button>
          <button className="primary" disabled={state.sendingToFusion} onClick={() => { void state.sendWgToFusion({ confirmed: true }).catch(() => undefined); }}>Continue: send WG changes</button>
        </div>
      </div>
    </div>
  </div>;
}
