import { useCallback, useEffect, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import { cadCoordinationStore } from '../../api/cadCoordination';
import {
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

  const refresh = useCallback(async (options: RefreshOptions = {}) => {
    const background = options.background === true;
    const request = ++returnListRequest.current;
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
      if (request !== returnListRequest.current) return;
      setBundles(response.items);
      const previous = seenReturnRevisions.current;
      const next = new Map(response.items.map((item) => [item.bundlePath, item.modifiedAt]));
      const wasConfigured = cadFolderConfigured.current;
      cadFolderConfigured.current = response.cadFolderConfigured;
      cadCoordinationStore.set(response.coordination === 'off' ? 'off' : 'on');
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
        if (!response.cadFolderConfigured) return;
        const current = response.items.find((bundle) => bundle.bundlePath === currentSelected.bundlePath);
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
      if (request === returnListRequest.current) setLoading(false);
    }
  }, [autoIngestSelected, cadFolderConfigured, enterCadWorkspace, manualSelectionAt, noteCadActivity,
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
