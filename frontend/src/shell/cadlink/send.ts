import { useCallback, useEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react';
import type { FusionCadStatus } from '../../api/cadlink';
import { selectCadWorkspace } from '../../api/cadWorkspace';
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

  /** Make sure the shared exchange folder exists before the send needs it.
   *
   * WG refuses to guess this folder, deliberately: the add-in's
   * `workspace_root` says an implicit application-data fallback would hide the
   * exchange and make first-time setup impossible to understand. That refusal
   * stays. What changes is that it stops being the end of the operation --
   * pressing Send with no folder chosen used to reach the export guard and go
   * no further, leaving a first-time user with a sentence pointing at a dialog
   * they had to find. Now the dialog is opened here and the send continues
   * into it. The folder is still the user's choice; nothing is defaulted.
   */
  const ensureCadFolder = useCallback(async (known: FusionCadStatus | null): Promise<void> => {
    // Only on an explicit negative. `cadFolderConfigured` is polled with the
    // rest of the Fusion status, and an unknown answer is not a missing
    // folder: the export guard in `sendDesignToCad` is still the check that
    // decides, and this is only what turns its refusal into a way forward.
    if (known?.cadFolderConfigured !== false) return;
    try {
      // No body: the server opens its own native folder picker. A dismissed
      // picker answers with the selection as it was, which is still none.
      if ((await selectCadWorkspace()).selected) return;
    } catch (reason) {
      const failed = reason instanceof Error ? reason : new Error(String(reason));
      setError(failed.message);
      throw failed;
    }
    // Thrown rather than returned: a `null` answer already means "parked on
    // the two-way conflict dialog", and a caller that reported this as that
    // would be telling the user something that did not happen.
    const refusal = new Error(
      'WG and Fusion share a folder, and none is chosen yet, so there is nowhere to send '
      + 'this design. Choose the folder when WG asks, or set it in Settings → CAD Link, '
      + 'and send again.',
    );
    setError(refusal.message);
    throw refusal;
  }, [setError]);

  const sendWgToFusion = useCallback(async (options?: { confirmed?: boolean }): Promise<WgLinkExportResponse | null> => {
    const current = fusionStatus;
    if (current?.state === 'instance_selection_required') {
      const reason = new Error('Choose which linked Fusion instance to update.');
      setError(reason.message);
      throw reason;
    }
    await ensureCadFolder(current);
    const action = fusionWorkflowView(current).action;
    if (action === 'update' && current?.fusionChangesAvailable && !options?.confirmed) {
      setPendingFusionConflict(true);
      return null;
    }
    setPendingFusionConflict(false);
    return sendToFusion(action === 'update' && current?.documentId && current.link
      ? { documentId: current.documentId, instanceId: current.link.instanceId, returnStateHash: current.link.documentSignatureHash }
      : undefined);
  }, [ensureCadFolder, fusionStatus, sendToFusion, setError]);

  const cancelFusionConflict = useCallback(() => setPendingFusionConflict(false), []);

  return {
    sendingToFusion,
    pendingFusionConflict,
    sendWgToFusion,
    cancelFusionConflict,
  };
}
