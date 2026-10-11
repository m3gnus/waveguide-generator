import { useEffect, useSyncExternalStore } from 'react';
import { getFusionCadStatus } from '../api/cadlink';
import { getOnshapeConnection, getOnshapeStatus } from '../api/onshape';
import { cadCoordinationStore } from '../api/cadCoordination';
import { jobsSocket } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import { useCadConnectionStore } from '../stores/cadConnection';
import { cadReceiptsStore, connectCadReceipts, connectCadReceiptDisplay } from '../stores/cadReceipts';
import { cadRecoveryNoticesStore, connectCadRecoveryNotices } from '../stores/cadRecoveryNotices';
import { useCadReturnStore } from '../stores/cadReturn';
import { cadWorkspace, emptyCadWorkspace } from '../stores/cadWorkspace';
import { cadWorkspaceSelection } from '../stores/cadWorkspaceSelection';
import { subscribeRevision, useDesignStore } from '../stores/design';
import { useDocumentStore } from '../stores/document';
import { documentSettingsSignature } from '../stores/designWire';
import { useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { connectCadObservation } from './cadObservation';
import { createCadArrivalActions, createCadReturnArrivalReader, enterCadWorkspace, SupersededError } from './cadlink/arrivals';
import { showIngestedMeshInViewport } from './cadlink/artifactLoader';
import { createCadOutboundController } from './cadlink/outbound';
import { createCadReturnWaiter } from './cadlink/returnWaiter';
import { createReceiptDisplay } from './cadlink/receiptDisplay';
import { observeCadProjectLoads, observeRememberedCadProject, refreshChannelDriverBases } from './cadlink/restores';
import { solvedCadModels } from './cadlink/solvedModel';
import { jobsCoordinatorBridge } from './JobsCoordinator';
import { solveAttention } from './solveAttention';
import { useModalDialogFocus } from './dialogFocus';

/** Mount independent readers/commands and project their feedback for the UI.
 * Jobs own solves, receipts own Send display, and the waiter alone owns pulls. */
export function mountCadWorkspaceOwners(): () => void {
  const mounted = { current: true };
  const cadFolderConfigured = { current: null as boolean | null };
  const manualSelectionAt = { current: null as number | null };
  const projectOpenPending = { current: false };
  const refusedForeignReturn = { current: false };
  const restoringCadProject = { current: false };
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
  const activityListeners = new Set<() => void>();
  const changed = () => activityListeners.forEach((listener) => listener());
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
  const rereadDrivers = () => { void refreshChannelDriverBases().then((channels) => {
    if (channels.length) reportStatus(`Re-read ${channels.length} driver${channels.length === 1 ? '' : 's'} from the driver library.`);
  }).catch(() => undefined); };
  const arrivals = createCadReturnArrivalReader({
    cadFolderConfigured, manualSelectionAt, projectOpenPending, refusedForeignReturn,
    returnListRequest, seenReturnRevisions, autoIngestSelected, enterCadWorkspace,
    refreshChannelDriverBases, noteCadActivity: changed,
    awaitingReturn: () => waiter.getState().pullingFromFusion,
    ownsReturn: (bundle) => Boolean(bundle.requestId && claimedReturns.has(bundle.requestId)),
    setError: reportError, setStatus: reportStatus,
    setBundles: (bundles) => publish({ bundles }), setLoading: (loading) => publish({ loading }),
  });
  const actions = createCadArrivalActions({
    mounted, ingestRequest, ingestAbortController, manualSelectionAt, refusedForeignReturn, restoringCadProject,
    autoIngestSelected, enterCadWorkspace, rereadDrivers, fusionInstanceId: () => useCadConnectionStore.getState().fusionStatus?.link?.instanceId ?? null,
    onshapeInstanceId: () => onshapeInstance,
    setIngesting: (ingesting) => publish({ ingesting }), setIngestError: (ingestError) => publish({ ingestError }),
    setError: reportError, setStatus: reportStatus, setViewportNotice: reportViewportNotice,
    setBundles: (bundles) => publish({ bundles }), reportViewportNotice,
  });
  const outbound = createCadOutboundController({
    selectedInstanceId: () => fusionInstance, noteCadActivity: changed,
    refresh: () => arrivals.refresh(),
  });
  const waiter = createCadReturnWaiter({
    selectedInstanceId: () => fusionInstance, noteCadActivity: changed,
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
  const selectBundle = (bundle: Parameters<typeof actions.selectBundleAs>[0], project?: string | null) => {
    actions.selectBundleAs(bundle, project);
  };
  const observation = connectCadObservation({
    getActivity: () => ({
      cadApplication: preferencesStore.getSnapshot().cadApplication,
      cadFolderConfigured: cadFolderConfigured.current,
      coordination: cadCoordinationStore.getSnapshot(), workspaceMode: workspaceModeStore.getSnapshot().mode,
      // No progress, receipt cursor or mesh generation in the context key.
      observationKey: JSON.stringify([useDocumentStore.getState().identity, useDesignStore.getState().designRevision,
        documentSettingsSignature(), useCadReturnStore.getState().selectedBundle?.bundlePath, fusionInstance]),
      requests: waiter.awaitedRequestIds().map((id) => ({ id, state: 'processing' })),
      awaitingReturn: waiter.getState().pullingFromFusion,
    }),
    subscribeActivity: (listener) => { activityListeners.add(listener); return () => { activityListeners.delete(listener); }; },
    readFusionStatus: () => getFusionCadStatus(useDesignStore.getState().design, useDocumentStore.getState().identity,
      useCadReturnStore.getState().selectedBundle?.bundlePath ?? null, fetch, fusionInstance),
    reloadReturns: () => arrivals.refresh({ background: true,
      autoOpenNew: useCadConnectionStore.getState().fusionStatus?.addinInboxTransfer !== true }),
    reloadReceipts: async (ids) => {
      await cadReceiptsStore.getState().load();
      return waiter.reconcile(ids);
    },
  });
  const pullAndSolve = async (): Promise<'solving' | 'blocked' | 'failed'> => {
    solveAttention.armSolve();
    try {
      const returned = await waiter.pullFromFusion();
      const choice = useCadReturnStore.getState().captureModelChoice();
      const prepared = await preparation;
      const selected = useCadReturnStore.getState();
      if (!prepared || selected.ingestRecord !== prepared || selected.needsIngest
        || !selected.isCurrentModelChoice(choice) || selected.selectedBundle?.bundlePath !== returned.bundlePath) {
        throw new SupersededError('A newer model selection superseded this geometry request.');
      }
      const outcome = await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
      if (outcome === 'busy') {
        reportStatus('Prepared the Fusion geometry. A solve is already running — press Solve when it finishes.');
        return 'blocked';
      }
      reportStatus('Solving the current Fusion geometry.');
      return 'solving';
    } catch (error) {
      if (error instanceof SupersededError) return 'blocked';
      reportError(error instanceof Error ? error.message : String(error));
      return useCadReturnStore.getState().ingestRecord ? 'blocked' : 'failed';
    }
  };
  const refresh = async (options?: Parameters<typeof arrivals.refresh>[0]) => {
    observation.refresh({ returns: false });
    await Promise.allSettled([arrivals.refresh(options), refreshOnshapeStatus(), cadRecoveryNoticesStore.getState().load(), waiter.refresh()]);
  };
  cadWorkspace.setState({ ...emptyCadWorkspace,
    refresh, refreshOnshapeStatus, selectBundle,
    returnFromOnshape: actions.returnFromOnshape,
    ingest: async () => { await actions.ingestSelected().catch(() => undefined); }, ingestSelected: actions.ingestSelected,
    pullFromFusion: waiter.pullFromFusion, pullAndSolve,
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
  const display = createReceiptDisplay({ enterCadWorkspace, reportViewportNotice, isActive: () => mounted.current,
    refuseAutomaticRestore: () => { refusedForeignReturn.current = true; },
    showIngestedMesh: showIngestedMeshInViewport, manualSelectionAt: () => manualSelectionAt.current });
  const settleModels = () => solvedCadModels.settle(jobsSocket.getSnapshot().jobs, {
    showIngestedMesh: showIngestedMeshInViewport, reportViewportNotice,
  });
  const subscribeContext = () => {
    changed();
    if (preferencesStore.getSnapshot().cadApplication === 'onshape') void refreshOnshapeStatus();
  };
  let context = '';
  const contextChanged = () => {
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
    outbound.subscribe((state, previous) => publish({ sendingToFusion: state.sendingToFusion,
      pendingFusionConflict: state.pendingFusionConflict,
      ...(state.error !== previous.error ? { error: state.error } : {}),
      ...(state.status !== previous.status ? { status: state.status } : {}) })),
    waiter.subscribe((state, previous) => {
      if (state.requestId) claimedReturns.add(state.requestId);
      publish({ pullingFromFusion: state.pullingFromFusion, errorDiagnostics: state.errorDiagnostics,
        ...(state.error !== previous.error ? { error: state.error } : {}),
        ...(state.status !== previous.status ? { status: state.status } : {}) }); changed();
    }),
    useCadConnectionStore.subscribe((state) => publish({ fusionStatus: state.fusionStatus })),
    cadReceiptsStore.subscribe((state, previous) => {
      if (state.feedback === previous.feedback) return;
      const feedback = Object.values(state.feedback).at(-1);
      if (feedback) publish(feedback.outcome === 'accepted' || feedback.outcome === 'superseded'
        ? { status: feedback.message } : { error: feedback.message });
    }),
    connectCadReceiptDisplay(display), connectCadReceipts(), connectCadRecoveryNotices(),
    observeCadProjectLoads({ enterCadWorkspace, manualSelectionAt, projectOpenPending, refusedForeignReturn,
      returnListRequest, seenReturnRevisions, refreshRef: { current: arrivals.refresh }, selectBundleRef: { current: selectBundle },
      setError: reportError, setStatus: reportStatus }),
    observeRememberedCadProject({ restoringCadProject, refusedForeignReturn, selectBundle, reportStatus }),
    jobsSocket.subscribe(settleModels), solvedCadModels.subscribe(settleModels),
    useDesignStore.subscribe(contextChanged), useDocumentStore.subscribe(contextChanged),
    useCadReturnStore.subscribe(contextChanged), useSolveOptionsStore.subscribe(contextChanged),
    workspaceModeStore.subscribe(changed), preferencesStore.subscribe(subscribeContext),
    cadCoordinationStore.subscribe(changed), cadWorkspaceSelection.subscribe(() => observation.refresh()),
    subscribeRevision((event) => { if (event.reason !== 'load') actions.supersedePreparation(); }),
  ];
  contextChanged(); settleModels(); void refreshOnshapeStatus();
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
