import { useCallback, useEffect, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { currentDocumentLoad, isCurrentDocumentLoad } from '../../stores/design';
import { returnOnshapeToWg } from '../../api/onshape';
import type { CadOperationSummary } from '../../api/cadOperations';
import { useCadPreparationStore } from '../../stores/cadPreparation';
import { rememberCadProject } from '../../stores/cadProjectMemory';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { workspaceNavigation } from '../workspaceNavigation';
import { showIngestedMeshInViewport } from './artifactLoader';
import { cadCoordinationStore } from '../../api/cadCoordination';
import {
  CadLinkApiError,
  ingestReturn,
  type CadReturnIngestRecord,
  listReturns,
  requestFusionReturn,
  type CadReturnBundle,
  type FusionCadStatus,
} from '../../api/cadlink';
import { preferencesStore } from '../../prefs/preferences';
import { useDocumentStore } from '../../stores/document';
import { useCadReturnStore } from '../../stores/cadReturn';
import { useCadOperationsStore } from '../../stores/cadOperations';
import { isPendingCadOperation } from '../../api/cadOperations';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { presentCadRefusal, refusalSentence } from './refusals';

export interface RefreshOptions {
  background?: boolean;
  autoOpenNew?: boolean;
}

/** A step abandoned because newer user intent replaced what it was working on.
 * Its feedback is already on screen, so a composed action stops silently. */
export class SupersededError extends Error {}

/** Whether a return names a design other than the one that is open.
 *
 * A return that names a design belongs to that design even when the open
 * document has no CAD identity. Returns naming no design remain manually
 * adoptable CAD-authored models. */
export function returnBelongsToAnotherProject(
  bundle: CadReturnBundle,
  designId: string | null | undefined,
): boolean {
  const returned = bundle.designIds ?? [];
  if (returned.length === 0) return false;
  return !designId || !returned.includes(designId);
}

/** Whether a return is positively linked to the open registry project.
 * Unlinked returns remain available for manual adoption, but are never
 * guessed into a project merely because they name no other project. */
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

export type CadReturnArrivalReaderIntegration = Pick<UseCadReturnArrivalsOptions,
  'autoIngestSelected' | 'cadFolderConfigured' | 'enterCadWorkspace' | 'manualSelectionAt'
  | 'noteCadActivity' | 'projectOpenPending' | 'refreshChannelDriverBases' | 'refusedForeignReturn'
  | 'returnListRequest' | 'seenReturnRevisions' | 'pendingReturnRequestId' | 'pendingReturnRequestedAt'
  | 'pendingReturnWaiter' | 'setError' | 'setErrorDiagnostics' | 'setStatus'> & {
    setBundles(items: CadReturnBundle[]): void;
    setLoading(value: boolean): void;
  };

/** A single listing read, with fencing even for fetch implementations that ignore abort. */
export async function refreshCadReturnArrivals(
  integration: CadReturnArrivalReaderIntegration,
  options: RefreshOptions = {},
  fetcher: typeof fetch = fetch,
): Promise<void> {
  const {
    autoIngestSelected, cadFolderConfigured, enterCadWorkspace, manualSelectionAt, noteCadActivity,
    projectOpenPending, refreshChannelDriverBases, refusedForeignReturn, returnListRequest,
    seenReturnRevisions, pendingReturnRequestId, pendingReturnRequestedAt, pendingReturnWaiter,
    setError, setErrorDiagnostics, setStatus, setBundles, setLoading,
  } = integration;
  const background = options.background === true;
  const request = ++returnListRequest.current;
  const selection = useCadReturnStore.getState().captureIngestIntent();
  const documentLoad = currentDocumentLoad();
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
    const response = await listReturns(fetcher);
    if (request !== returnListRequest.current
      || !useCadReturnStore.getState().isCurrentIngestIntent(selection)
      || !isCurrentDocumentLoad(documentLoad)) return;
    setBundles(response.items);
    const previous = seenReturnRevisions.current;
    const next = new Map(response.items.map((item) => [item.bundlePath, item.modifiedAt]));
    const wasConfigured = cadFolderConfigured.current;
    cadFolderConfigured.current = response.cadFolderConfigured;
    cadCoordinationStore.set(response.coordination === 'on' ? 'on' : 'off');
    const listingChanged = previous === null
      || previous.size !== next.size
      || [...next].some(([path, modifiedAt]) => previous.get(path) !== modifiedAt);
    if (listingChanged || response.cadFolderConfigured !== wasConfigured) noteCadActivity();
    const requested = pendingReturnRequestId.current
      ? response.items.find((item) => (
          item.readable && item.requestId === pendingReturnRequestId.current
        )) ?? null
      : null;
    // Before the wall clock: Fusion may have answered already, and refused.
    // A return request's operation id *is* its request id
    // (server/cadlink/fusion_return.py hands `request_id` to
    // `accept_operation`), so the refusal WG recorded is in hand under the
    // id this pull is waiting on. Waiting the full minute and then saying
    // Fusion did not answer would be stating something WG can see is false.
    const answered = pendingReturnRequestId.current
      ? useCadOperationsStore.getState().operations[pendingReturnRequestId.current] ?? null
      : null;
    // A bundle that did arrive wins: the arrival path below settles it, and
    // nothing here overtakes geometry the user can already use.
    if (!requested && answered && !isPendingCadOperation(answered) && answered.state !== 'accepted') {
      pendingReturnRequestId.current = null;
      pendingReturnRequestedAt.current = null;
      const presented = presentCadRefusal(answered.message);
      const sentence = presented
        ? refusalSentence(presented)
        : `Fusion ${answered.state === 'cancelled' ? 'did not carry out' : 'refused'} the request for this model’s geometry, and reported no detail.`;
      const waiter = pendingReturnWaiter.current;
      pendingReturnWaiter.current = null;
      if (presented) setErrorDiagnostics({ message: sentence, detail: presented.diagnostics });
      if (waiter) waiter.fail(new Error(sentence));
      else setError(sentence);
    } else if (
      pendingReturnRequestId.current
      && pendingReturnRequestedAt.current !== null
      && Date.now() - pendingReturnRequestedAt.current > 60_000
    ) {
      pendingReturnRequestId.current = null;
      pendingReturnRequestedAt.current = null;
      const timeout = 'Fusion did not return the requested model within 60 seconds. Check Fusion for a WGLink message, then retry.';
      const waiter = pendingReturnWaiter.current;
      pendingReturnWaiter.current = null;
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
    const destination = projectOpenPending.current
      ? useDocumentStore.getState().identity?.lineageId ?? null
      : undefined;
    const requestedArrival = arrived !== null && arrived === requested
      && pendingReturnRequestId.current === arrived.requestId;
    const selected = useCadReturnStore.getState().selectedBundle;
    const arrivalRequestedAt = arrived
      ? (requestedArrival ? pendingReturnRequestedAt.current : Date.parse(arrived.modifiedAt))
      : null;
    const heldForManualPick = Boolean(
      arrived && !projectMismatch && selected && selected.bundlePath !== arrived.bundlePath
      && manualSelectionAt.current !== null
      && arrivalRequestedAt !== null && Number.isFinite(arrivalRequestedAt)
      && arrivalRequestedAt <= manualSelectionAt.current,
    );
    let continuity: 'initial' | 'carried' | 'reset' = 'initial';
    if (opened && !projectMismatch && !heldForManualPick) {
      continuity = arrived
        ? useCadReturnStore.getState().selectArrivedBundle(arrived, destination)
        : (useCadReturnStore.getState().selectBundle(opened, destination), 'initial');
      void refreshChannelDriverBases().catch(() => undefined);
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
        refusedForeignReturn.current = true;
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
      if (arrived.requestId === pendingReturnRequestId.current) {
        pendingReturnRequestId.current = null;
        pendingReturnRequestedAt.current = null;
      }
      const arrivedName = arrived.documentName ?? arrived.name;
      const keptName = selected ? selected.documentName ?? selected.name : null;
      const waiter = pendingReturnWaiter.current;
      if (waiter && arrived.requestId === waiter.requestId) {
        pendingReturnWaiter.current = null;
        if (heldForManualPick) {
          waiter.fail(new SupersededError(`Received ${arrivedName} from Fusion 360, but you selected ${keptName} after asking for it.`));
        } else {
          waiter.settle(arrived);
        }
      }
      setStatus(heldForManualPick
        ? `Received ${arrivedName} from Fusion 360. You selected ${keptName} after it was sent, so ${keptName} stays selected; select ${arrivedName} from the return list to use it.`
        : `Received ${arrivedName} from Fusion 360.${
          continuity === 'carried' ? ' Kept your mesh, channel, and solve settings.' : ''
        }`);
      enterCadWorkspace();
      if (!heldForManualPick) autoIngestSelected();
    } else if (!initial) {
      const currentSelected = useCadReturnStore.getState().selectedBundle;
      if (!currentSelected) return;
      if (currentSelected.bundleOrigin === 'native') return;
      if (!response.cadFolderConfigured) return;
      const current = response.items.find((bundle) => bundle.bundlePath === currentSelected.bundlePath);
      useCadReturnStore.getState().refreshSelectedBundle(current ?? null);
    }
  } catch (reason) {
    if (request === returnListRequest.current
      && useCadReturnStore.getState().isCurrentIngestIntent(selection)
      && isCurrentDocumentLoad(documentLoad) && !background) {
      if (projectOpenPending.current) {
        projectOpenPending.current = false;
        setStatus('Project design loaded, but its CAD returns could not be read. Refresh CAD Link to try again.');
      }
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  } finally {
    if (request === returnListRequest.current) setLoading(false);
  }
}

interface UseCadReturnArrivalsOptions {
  autoIngestSelected(): void;
  cadFolderConfigured: MutableRefObject<boolean | null>;
  enterCadWorkspace(): void;
  fusionStatus: FusionCadStatus | null;
  identityDesignId: string | null | undefined;
  manualSelectionAt: MutableRefObject<number | null>;
  mounted: MutableRefObject<boolean>;
  noteCadActivity(): void;
  onshape: boolean;
  pageIsVisible(): boolean;
  pollDelayMs(baseMs: number, idleMs: number, unconfiguredMs: number | null, listing?: boolean): number | null;
  pollRestarts: MutableRefObject<Set<() => void>>;
  projectOpenPending: MutableRefObject<boolean>;
  refreshChannelDriverBases(): Promise<unknown>;
  refusedForeignReturn: MutableRefObject<boolean>;
  refreshRef: MutableRefObject<(options?: RefreshOptions) => Promise<void>>;
  returnListRequest: MutableRefObject<number>;
  seenReturnRevisions: MutableRefObject<Map<string, string> | null>;
  pendingReturnRequestId: MutableRefObject<string | null>;
  pendingReturnRequestedAt: MutableRefObject<number | null>;
  pendingReturnWaiter: MutableRefObject<{
    requestId: string;
    settle: (bundle: CadReturnBundle) => void;
    fail: (reason: Error) => void;
  } | null>;
  fusionPullPromise: MutableRefObject<Promise<CadReturnBundle> | null>;
  returnsIdleMs: number;
  returnsMs: number;
  setError: Dispatch<SetStateAction<string | null>>;
  setErrorDiagnostics: Dispatch<SetStateAction<{ message: string; detail: string } | null>>;
  setStatus: Dispatch<SetStateAction<string | null>>;
  startAdaptivePoll(
    restarts: Set<() => void>,
    tick: () => void,
    period: () => number | null,
  ): () => void;
}

export function useCadReturnArrivals({
  autoIngestSelected,
  cadFolderConfigured,
  enterCadWorkspace,
  fusionStatus,
  identityDesignId,
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
  returnsIdleMs,
  returnsMs,
  setError,
  setErrorDiagnostics,
  setStatus,
  startAdaptivePoll,
}: UseCadReturnArrivalsOptions) {
  const [bundles, setBundles] = useState<CadReturnBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [pullingFromFusion, setPullingFromFusion] = useState(false);

  const refresh = useCallback((options: RefreshOptions = {}) => refreshCadReturnArrivals({
    autoIngestSelected, cadFolderConfigured, enterCadWorkspace, manualSelectionAt, noteCadActivity,
    projectOpenPending, refreshChannelDriverBases, refusedForeignReturn, returnListRequest,
    seenReturnRevisions, pendingReturnRequestId, pendingReturnRequestedAt, pendingReturnWaiter,
    setError, setErrorDiagnostics, setStatus, setBundles, setLoading,
  }, options), [autoIngestSelected, cadFolderConfigured, enterCadWorkspace, manualSelectionAt, noteCadActivity,
    projectOpenPending, refreshChannelDriverBases, refusedForeignReturn, setError,
    setErrorDiagnostics, setStatus]);
  refreshRef.current = refresh;

  // Returns arrive in the workspace's wgreturn folder, which only the Fusion
  // add-in writes. Onshape bundles use WG's data directory and never enter this
  // lifecycle, so there is deliberately no returns poll in Onshape mode.
  useEffect(() => {
    if (onshape) { setLoading(false); return undefined; }
    if (pageIsVisible()) void refresh({ autoOpenNew: true });
    else setLoading(false);
    // This listing is the one poll that never suspends. When CAD Link has no
    // configured folder, it remains the signal that Settings has since gained
    // one, but widens to the idle cadence.
    const stop = startAdaptivePoll(
      pollRestarts.current,
      () => { if (pageIsVisible()) void refresh({ background: true, autoOpenNew: true }); },
      () => pollDelayMs(returnsMs, returnsIdleMs, returnsIdleMs, true),
    );
    return () => {
      stop();
      returnListRequest.current += 1;
    };
  }, [onshape, pageIsVisible, pollDelayMs, pollRestarts, refresh, returnListRequest,
    returnsIdleMs, returnsMs, startAdaptivePoll]);

  const expectFusionReturn = useCallback((requestId: string, requestedAt = Date.now()) => {
    pendingReturnRequestId.current = requestId;
    pendingReturnRequestedAt.current = requestedAt;
    noteCadActivity();
  }, [noteCadActivity]);

  const pullFromFusion = useCallback((): Promise<CadReturnBundle> => {
    if (fusionPullPromise.current) return fusionPullPromise.current;
    setPullingFromFusion(true);
    const fail = (reason: unknown): never => {
      const error = reason instanceof Error ? reason : new Error(String(reason));
      if (!(error instanceof SupersededError) && mounted.current) setError(error.message);
      throw error;
    };
    const operation = (async () => {
      if (!identityDesignId || !fusionStatus?.documentId || !fusionStatus.link) {
        return fail(new Error('Fusion changed documents. Refresh CAD Link and try again.'));
      }
      setError(null);
      const askedAt = Date.now();
      const result = await requestFusionReturn({
        designId: identityDesignId,
        documentId: fusionStatus.documentId,
        instanceId: fusionStatus.link.instanceId,
        expectedReturnStateHash: fusionStatus.link.documentSignatureHash,
      }).catch(fail);
      expectFusionReturn(result.requestId, askedAt);
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
  }, [expectFusionReturn, fusionStatus, identityDesignId, mounted, refresh, setError, setStatus]);

  return {
    bundles,
    setBundles,
    loading,
    pullingFromFusion,
    refresh,
    pullFromFusion,
  };
}

export function enterCadWorkspace(): void {
  workspaceModeStore.setMode('cad');
  workspaceNavigation.activate('cadlink');
}

type Ref<T> = { current: T };
export interface CadArrivalActionsIntegration {
  ingestRequest: Ref<number>;
  ingestAbortController: Ref<AbortController | null>;
  mounted: Ref<boolean>;
  manualSelectionAt: Ref<number | null>;
  manualSelections: Ref<number>;
  refusedForeignReturn: Ref<boolean>;
  restoringCadProject: Ref<boolean>;
  sendIntent: Ref<{ acceptedSeq: number | null; sentAt: number; picks: number } | null>;
  setIngesting(value: boolean): void;
  setIngestError(value: string | null): void;
  setError(value: string | null): void;
  setStatus(value: string | null): void;
  setViewportNotice(value: string | null): void;
  setBundles(value: CadReturnBundle[]): void;
  reportViewportNotice(value: string | null): void;
  enterCadWorkspace(): void;
  autoIngestSelected(): void;
  rereadDrivers(): void;
  noteCadActivity(): void;
  refresh(options?: RefreshOptions): Promise<void>;
  autoSelectBundleRef: Ref<(bundle: CadReturnBundle, project: string | null | undefined, automatic: boolean) => void>;
  fusionInstanceId(): string | null;
  onshapeInstanceId(): string | null;
  showIngestedMesh: typeof showIngestedMeshInViewport;
}

/** Selection actions are independent of React. Supply feedback and lifecycle adapters
 * for a mounted panel; omitted adapters permit background and archived-model use. */
export function createCadArrivalActions(
  integration: Partial<CadArrivalActionsIntegration> = {},
  fetcher: typeof fetch = fetch,
) {
  const noop = () => undefined;
  const defaults: CadArrivalActionsIntegration = {
    ingestRequest: { current: 0 }, ingestAbortController: { current: null }, mounted: { current: true },
    manualSelectionAt: { current: null }, manualSelections: { current: 0 },
    refusedForeignReturn: { current: false }, restoringCadProject: { current: false }, sendIntent: { current: null },
    setIngesting: noop, setIngestError: noop, setError: noop, setStatus: noop,
    setViewportNotice: noop, setBundles: noop, reportViewportNotice: noop,
    enterCadWorkspace, autoIngestSelected: noop, rereadDrivers: noop, noteCadActivity: noop,
    refresh: async () => undefined,
    autoSelectBundleRef: { current: (bundle, project) => { selectBundleAs(bundle, project, true); } },
    fusionInstanceId: () => null, onshapeInstanceId: () => null,
    showIngestedMesh: showIngestedMeshInViewport,
  };
  const adapter = { ...defaults, ...integration };
  const { ingestRequest, ingestAbortController, mounted, manualSelectionAt, manualSelections,
    refusedForeignReturn, restoringCadProject, setIngesting, setIngestError, setError, setStatus,
    setViewportNotice, setBundles, reportViewportNotice, enterCadWorkspace: activateWorkspace,
    autoIngestSelected, rereadDrivers, noteCadActivity, refresh,
    fusionInstanceId, onshapeInstanceId, showIngestedMesh } = adapter;
  const ingestSelected = async (): Promise<CadReturnIngestRecord> => {
    const current = useCadReturnStore.getState();
    if (!current.selectedBundle) throw new Error('Select a CAD return before preparing a simulation.');
    if (current.selectedBundle.bundleOrigin === 'native') throw new Error('Edit the native source assembly and prepare it again.');
    // This intent covers the ingest record itself. The viewport has a separate
    // token because its follow-up artifact fetch can be superseded independently.
    const ingestGeneration = current.beginIngestIntent();
    const documentLoad = currentDocumentLoad();
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
        // A workspace listing item has no origin and is a WGLink return; an
        // Onshape return carries its own. The server resolves each against
        // its own root, so this is the only thing that has to be dispatched.
        bundleOrigin: current.selectedBundle.bundleOrigin ?? 'wglink',
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
          const liveInstanceId = fusionInstanceId() ?? null;
          if (liveInstanceId && instances.some((item) => item.instanceId === liveInstanceId)) {
            return liveInstanceId;
          }
          const anchor = bundle.solverAnchorInstanceId ?? null;
          return anchor && instances.some((item) => item.instanceId === anchor)
            ? anchor
            : null;
        })(),
        symmetryMode: useCadPreparationStore.getState().symmetryMode,
      }, fetcher, abortController.signal);
      if (!isCurrentDocumentLoad(documentLoad)
        || !useCadReturnStore.getState().applyIngest(record, ingestGeneration)) {
        const superseded = 'Discarded a completed ingest because its selected return or design was superseded. Rebuild the mesh for the current state.';
        if (request === ingestRequest.current && mounted.current) setStatus(superseded);
        throw new SupersededError(superseded);
      }
      if (request === ingestRequest.current && mounted.current) {
        rememberCadProject(record.project?.lineage_id);
        const origin = current.selectedBundle.bundleOrigin === 'onshape' ? 'Onshape' : 'Fusion';
        setStatus(`Received ${current.selectedBundle.documentName || current.selectedBundle.name} from ${origin}.`);
        // Before the display, so the viewport adopts the CAD slot rather than
        // loading it invisibly behind the parametric design.
        activateWorkspace();
        // Awaited rather than fired and forgotten: the solve gate refuses an
        // ingestion whose mesh is not the one on screen, so a composed
        // pull/ingest/solve that raced this fetch failed on every return after
        // the first with a viewport-mismatch the user could not act on.
        await showIngestedMesh(
          record,
          current.selectedBundle.documentName || current.selectedBundle.name,
          reportViewportNotice,
          fetcher,
          viewportGeneration,
        );
      }
      return record;
    } catch (reason) {
      if (reason instanceof SupersededError) throw reason;
      if (!isCurrentDocumentLoad(documentLoad)
        || !useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
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
  };

  const supersedePreparation = () => {
    useCadReturnStore.getState().beginIngestIntent();
    importedMeshStore.beginIntent();
    ingestRequest.current += 1;
    ingestAbortController.current?.abort();
    setIngesting(false);
  };

  const selectBundleAs = (
    bundle: CadReturnBundle,
    projectLineageId: string | null | undefined = undefined,
    automatic = false,
  ) => {
    if (returnBelongsToAnotherProject(bundle, useDocumentStore.getState().identity?.designId)) {
      refusedForeignReturn.current = true;
      setError(`That return belongs to another CAD-linked project. Open it from File → CAD-linked designs first.`);
      activateWorkspace();
      return;
    }
    // A selection the user made themselves is the acknowledgement a standing
    // refusal was waiting for, and newer intent than any return already sent;
    // the restore's own selection is neither, and nor is displaying a Send.
    if (!automatic && !restoringCadProject.current) {
      refusedForeignReturn.current = false;
      manualSelectionAt.current = Date.now();
      manualSelections.current += 1;
      ingestRequest.current += 1;
      ingestAbortController.current?.abort();
      setIngesting(false);
    }
    useCadReturnStore.getState().selectBundle(bundle, projectLineageId);
    rereadDrivers();
    importedMeshStore.beginIntent();
    activateWorkspace();
    autoIngestSelected();
  };

  const returnFromOnshape = async () => {
    const identity = useDocumentStore.getState().identity;
    if (!preferencesStore.isOnshapeAvailable()) throw new Error('Onshape is unavailable in this build.');
    if (!identity?.designId) throw new Error('Send this design to Onshape before returning it.');
    let ingestGeneration = useCadReturnStore.getState().beginIngestIntent();
    const documentLoad = currentDocumentLoad();
    const instanceId = onshapeInstanceId();
    const request = ++ingestRequest.current;
    ingestAbortController.current?.abort();
    const viewportGeneration = importedMeshStore.beginIntent();
    setIngesting(true); setError(null); setStatus(null); setViewportNotice(null);
    try {
      const result = await returnOnshapeToWg(
        identity.designId, fetcher, instanceId,
      );
      // Translation may finish after a project/model change or a newer return.
      // Check the original intent before selection creates a fresh generation.
      if (!mounted.current || request !== ingestRequest.current || !isCurrentDocumentLoad(documentLoad)
        || onshapeInstanceId() !== instanceId
        || useDocumentStore.getState().identity?.designId !== identity.designId
        || !useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) return;
      const sources = result.ingest.sources.map((source) => ({
        id: source.id,
        role: source.role,
        required: source.required,
        suggestedResolutionMm: source.suggested_resolution_mm,
        defaultDriveChannelId: source.default_drive_channel_id,
      }));
      const bundle: CadReturnBundle = {
        name: result.bundle.name,
        // Both halves travel together, and neither is a filesystem location:
        // the path is relative and the origin says which permitted area it is
        // relative to. Keeping the origin here is what lets Rebuild mesh
        // re-ingest this return -- the local mesh controls are displayed for
        // it, and without the origin the request is refused as a WGLink path.
        bundlePath: result.bundle.bundlePath,
        bundleOrigin: result.bundle.bundleOrigin ?? 'onshape',
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
      ingestGeneration = state.beginIngestIntent();
      if (!useCadReturnStore.getState().applyIngest(result.ingest, ingestGeneration)) {
        setStatus('Discarded the Onshape return because the selected design changed.');
        return;
      }
      setBundles([bundle]);
      setStatus(`Returned and ingested ${result.bundle.documentName ?? result.bundle.name} from Onshape.`);
      // The CAD Link panel only exists inside the CAD workspace, and the
      // ingested return is now the solve truth — enter the mode that owns it.
      activateWorkspace();
      await showIngestedMesh(
        result.ingest,
        result.bundle.documentName ?? result.bundle.name,
        reportViewportNotice,
        fetcher,
        viewportGeneration,
      );
    } catch (reason) {
      if (mounted.current && request === ingestRequest.current && isCurrentDocumentLoad(documentLoad)
        && onshapeInstanceId() === instanceId
        && useDocumentStore.getState().identity?.designId === identity.designId
        && useCadReturnStore.getState().isCurrentIngestIntent(ingestGeneration)) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (request === ingestRequest.current && mounted.current) setIngesting(false);
    }
  };

  const displaySend = async (operation: CadOperationSummary) => {
    const bundlePath = operation.snapshot?.bundlePath;
    if (!bundlePath) return;
    const sentAt = Date.parse(operation.createdAt ?? operation.updatedAt ?? '');
    const order = Number.isFinite(sentAt) ? sentAt : 0;
    const acceptedSeq = Number.isSafeInteger(operation.acceptedSeq) ? Number(operation.acceptedSeq) : null;
    const heldIntent = adapter.sendIntent.current;
    if (heldIntent !== null && (
      (acceptedSeq !== null && heldIntent.acceptedSeq !== null && acceptedSeq < heldIntent.acceptedSeq)
      || (acceptedSeq === null && heldIntent.acceptedSeq !== null)
      || (acceptedSeq === null && heldIntent.acceptedSeq === null && order <= heldIntent.sentAt)
    )) return;
    const intent = { acceptedSeq, sentAt: order, picks: manualSelections.current };
    adapter.sendIntent.current = intent;
    const superseded = () => adapter.sendIntent.current !== intent || manualSelections.current !== intent.picks;
    noteCadActivity();
    await refresh({ background: true, autoOpenNew: true }).catch(() => undefined);
    if (superseded()) return;
    if (useCadReturnStore.getState().selectedBundle?.bundlePath === bundlePath) {
      activateWorkspace();
      return;
    }
    // A return the user picked by hand after this was sent stays selected,
    // as the listing's own arrival rule keeps it.
    if (manualSelectionAt.current !== null && Number.isFinite(sentAt) && manualSelectionAt.current > sentAt) {
      const name = operation.snapshot?.documentName ?? 'the model Fusion sent';
      setStatus(`Received ${name} from Fusion 360. You selected another return after it was sent, so that one stays selected; select ${name} from the return list to use it.`);
      return;
    }
    const selection = useCadReturnStore.getState().captureIngestIntent();
    const documentLoad = currentDocumentLoad();
    const listed = await listReturns(fetcher).catch(() => null);
    if (superseded() || !useCadReturnStore.getState().isCurrentIngestIntent(selection)
      || !isCurrentDocumentLoad(documentLoad)) return;
    const bundle = listed?.items.find((item) => item.bundlePath === bundlePath && item.readable);
    if (bundle) {
      // Automatic: displaying a Send is not the user's pick.
      adapter.autoSelectBundleRef.current(bundle, undefined, true);
      return;
    }
    // Accepted, and WG holds its copy, but there is nothing here to open: said,
    // not left as a Send that silently did nothing.
    const name = operation.snapshot?.documentName ?? 'the model Fusion sent';
    setStatus(listed === null
      ? `Received ${name} from Fusion 360, but WG could not read the WGLink folder to open it. Refresh CAD Link to try again.`
      : `Received ${name} from Fusion 360, but it is no longer in the WGLink folder, so WG cannot open it here. Send it again from Fusion.`);
    activateWorkspace();
  };

  return { ingestSelected, selectBundleAs, returnFromOnshape, displaySend, supersedePreparation };
}

/** Create a listing reader with its own revision cursor and request fence.
 * Its scheduler is supplied by the application; Onshape mode performs no folder read. */
export function createCadReturnArrivalReader(
  overrides: Partial<CadReturnArrivalReaderIntegration> = {},
  fetcher: typeof fetch = fetch,
) {
  const noop = () => undefined;
  const integration: CadReturnArrivalReaderIntegration = {
    autoIngestSelected: noop, cadFolderConfigured: { current: null }, enterCadWorkspace,
    manualSelectionAt: { current: null }, noteCadActivity: noop,
    projectOpenPending: { current: false }, refreshChannelDriverBases: async () => undefined,
    refusedForeignReturn: { current: false }, returnListRequest: { current: 0 },
    seenReturnRevisions: { current: null }, pendingReturnRequestId: { current: null },
    pendingReturnRequestedAt: { current: null }, pendingReturnWaiter: { current: null },
    setError: noop, setErrorDiagnostics: noop, setStatus: noop, setBundles: noop, setLoading: noop,
    ...overrides,
  };
  return {
    refresh: (options?: RefreshOptions) => refreshCadReturnArrivals(integration, options, fetcher),
    cancel: () => { integration.returnListRequest.current += 1; },
  };
}
