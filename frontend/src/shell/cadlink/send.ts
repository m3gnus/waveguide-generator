import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { FusionCadStatus } from '../../api/cadlink';
import { sendDesignToCad, type WgLinkExportResponse } from '../../api/designIo';
import {
  currentDocumentLoad,
  isCurrentDocumentLoad,
  recordCommittedAthPolars,
  useDesignStore,
} from '../../stores/design';
import { useDocumentStore } from '../../stores/document';
import { polarConfigFromUi, useSolveOptionsStore } from '../../stores/solveOptions';
import { designNameSlug } from '../../stores/designName';
import { keptContentKeyOf, rememberSentCopy } from '../../design/replacementCheck';
import { fusionWorkflowView } from '../cadWorkflowView';

interface UseCadSendOptions {
  design: ReturnType<typeof useDesignStore.getState>['design'];
  designRevision: number;
  designName: string;
  identity: ReturnType<typeof useDocumentStore.getState>['identity'];
  setCadLink: ReturnType<typeof useDocumentStore.getState>['setCadLink'];
  fusionStatus: FusionCadStatus | null;
  mounted: MutableRefObject<boolean>;
  noteCadActivity(): void;
  refresh(): Promise<void>;
  setError: Dispatch<SetStateAction<string | null>>;
  setStatus: Dispatch<SetStateAction<string | null>>;
}

/** Whether directivity settings are still the ones an outbound send committed. */
function polarConfigStillCommitted(committed: unknown): boolean {
  try {
    return JSON.stringify(polarConfigFromUi(useSolveOptionsStore.getState().polar))
      === JSON.stringify(committed);
  } catch {
    return false;
  }
}

/** Own the one outbound Fusion path and its confirmation/fencing state. */
export function useCadSend({
  design,
  designRevision,
  designName,
  identity,
  setCadLink,
  fusionStatus,
  mounted,
  noteCadActivity,
  refresh,
  setError,
  setStatus,
}: UseCadSendOptions) {
  const [sendingToFusion, setSendingToFusion] = useState(false);
  const [pendingFusionConflict, setPendingFusionConflict] = useState(false);
  const fusionSendRequest = useRef(0);

  useEffect(() => () => { fusionSendRequest.current += 1; }, []);

  const sendToFusion = useCallback(async (target?: {
    documentId: string;
    instanceId: string;
    returnStateHash: string | null;
  }) => {
    const request = ++fusionSendRequest.current;
    const documentLoad = currentDocumentLoad();
    setSendingToFusion(true); setError(null); setStatus(null);
    noteCadActivity();
    try {
      const polarConfig = polarConfigFromUi(useSolveOptionsStore.getState().polar);
      const sentKey = keptContentKeyOf(design);
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
      rememberSentCopy(
        sentKey,
        result.identity?.designId,
        request === fusionSendRequest.current && mounted.current && isCurrentDocumentLoad(documentLoad),
      );
      if (request === fusionSendRequest.current && mounted.current) {
        if (!isCurrentDocumentLoad(documentLoad)) {
          setStatus(`Sent to Fusion 360 · sequence ${result.sequence}. Another design was opened while it was sending, so its CAD link stayed with the design that was exported.`);
          return result;
        }
        if (polarConfigStillCommitted(polarConfig)) recordCommittedAthPolars(polarConfig);
        if (result.identity) setCadLink(result.identity, 'current');
        const superseded = result.cadHandoffSuperseded?.length
          ? ' It replaced an earlier update Fusion had not started yet.'
          : '';
        const waiting = result.cadHandoffWaiting ? ` ${result.cadHandoffWaiting}` : '';
        setStatus(target
          ? `Update sent to Fusion 360 · sequence ${result.sequence}.${superseded}${waiting}`
          : `Opening in Fusion 360 · sequence ${result.sequence}.${waiting}`);
        await refresh();
      }
      return result;
    } catch (reason) {
      rememberSentCopy(null, identity?.designId, mounted.current && isCurrentDocumentLoad(documentLoad));
      if (request === fusionSendRequest.current && mounted.current) {
        setError(reason instanceof Error ? reason.message : String(reason));
      }
      throw reason;
    } finally {
      if (request === fusionSendRequest.current && mounted.current) setSendingToFusion(false);
    }
  }, [design, designRevision, designName, identity, mounted, noteCadActivity, refresh, setCadLink,
    setError, setStatus]);

  const sendWgToFusion = useCallback(async (options?: { confirmed?: boolean }): Promise<WgLinkExportResponse | null> => {
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
  }, [fusionStatus, sendToFusion, setError]);

  const cancelFusionConflict = useCallback(() => setPendingFusionConflict(false), []);

  return {
    sendingToFusion,
    pendingFusionConflict,
    sendWgToFusion,
    cancelFusionConflict,
  };
}
