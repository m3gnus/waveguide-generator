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
  // No `design`/`designRevision`: the send reads both from the store at the
  // moment it builds the bundle, because a render-time capture goes stale
  // across the folder picker. Passing them in would offer a second, older
  // source of the same fact.
  designName: string;
  identity: ReturnType<typeof useDocumentStore.getState>['identity'];
  setCadLink: ReturnType<typeof useDocumentStore.getState>['setCadLink'];
  fusionStatus: FusionCadStatus | null;
  mounted: MutableRefObject<boolean>;
  noteCadActivity(): void;
  /** Read Fusion's status now and publish it, answering `null` when it cannot
   * be read. Held in a ref because the coordinator defines it below this hook,
   * the same way the returns refresh is shared. */
  readFusionStatus: MutableRefObject<() => Promise<FusionCadStatus | null>>;
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
  designName,
  identity,
  setCadLink,
  fusionStatus,
  mounted,
  noteCadActivity,
  readFusionStatus,
  refresh,
  setError,
  setStatus,
}: UseCadSendOptions) {
  const [sendingToFusion, setSendingToFusion] = useState(false);
  const [pendingFusionConflict, setPendingFusionConflict] = useState(false);
  const fusionSendRequest = useRef(0);
  /** The folder pick currently in flight, so a second send joins that dialog
   * rather than opening another one on top of it. Scoped to the pick, not to
   * the whole send: overlapping sends are a supported case, fenced by
   * `fusionSendRequest` so the newest one's identity and feedback win. */
  const folderPickInFlight = useRef<Promise<FusionCadStatus | null> | null>(null);

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
      // Read here, not from the render this callback was built in. A send can
      // sit in the native folder picker for as long as the user takes, and WG
      // stays live behind it: the status the guards re-ran against was read
      // after the picker closed, from the design as it is now. Exporting the
      // design as it was when Send was pressed would clear a revision the
      // bundle does not contain. `isCurrentDocumentLoad` fences document
      // loads, which is a different thing from a parameter edit.
      const { design, designRevision } = useDesignStore.getState();
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
  }, [designName, identity, mounted, noteCadActivity, refresh, setCadLink,
    setError, setStatus]);

  /** End the send with a sentence, rather than with an answer.
   *
   * Thrown rather than returned: a `null` answer already means "parked on the
   * two-way conflict dialog", and a caller that reported one of these as that
   * would be telling the user something that did not happen.
   */
  const refuse = useCallback((message: string): Error => {
    setError(message);
    return new Error(message);
  }, [setError]);

  /** Ask for the shared exchange folder, and answer with the status read after.
   *
   * WG refuses to guess this folder, deliberately: the add-in's
   * `workspace_root` says an implicit application-data fallback would hide the
   * exchange and make first-time setup impossible to understand. That refusal
   * stays. What changes is that it stops being the end of the operation --
   * pressing Send with no folder chosen used to reach the export guard and go
   * no further, leaving a first-time user with a sentence pointing at a dialog
   * they had to find. Now the dialog is opened here and the send continues
   * into it. The folder is still the user's choice; nothing is defaulted.
   *
   * Choosing the folder changes the world the send is about, so this answers
   * with a freshly read status rather than with nothing. `cadFolderConfigured`
   * is read from the filesystem and the link is not (`server/workspace/api.py`
   * reports no folder for a path that is not a directory at this moment, while
   * the link and the heartbeat come from the data directory), so the status
   * before the picker can say "no folder" about a document that is linked and
   * has Fusion-side changes. Deciding from it would run the guards below
   * against a world that no longer exists.
   */
  const chooseCadFolder = useCallback((): Promise<FusionCadStatus | null> => {
    // One dialog at a time. `sendingToFusion` is only set inside the export,
    // which is past this point, so it disables no control while the picker is
    // open: two quick presses of Send used to reach `selectCadWorkspace` twice
    // and stack two native folder dialogs. A second send joins this one and
    // continues from the folder the user chooses in it.
    if (folderPickInFlight.current) return folderPickInFlight.current;
    const picking = (async (): Promise<FusionCadStatus | null> => {
      let selected: boolean;
      try {
        // No body: the server opens its own native folder picker. A dismissed
        // picker answers with the selection as it was, which is still none.
        selected = (await selectCadWorkspace()).selected;
      } catch (reason) {
        const failed = reason instanceof Error ? reason : new Error(String(reason));
        setError(failed.message);
        throw failed;
      }
      if (!selected) {
        throw refuse(
          'WG and Fusion share a folder, and none is chosen yet, so there is nowhere to send '
          + 'this design. Choose the folder when WG asks, or set it in Settings → CAD Link, '
          + 'and send again.',
        );
      }
      return readFusionStatus.current();
    })();
    folderPickInFlight.current = picking;
    void picking.catch(() => undefined).finally(() => {
      if (folderPickInFlight.current === picking) folderPickInFlight.current = null;
    });
    return picking;
  }, [readFusionStatus, refuse, setError]);

  const sendWgToFusion = useCallback((options?: { confirmed?: boolean }): Promise<WgLinkExportResponse | null> => {
    /** The whole outbound guard chain, against one status.
     *
     * The status is a parameter rather than a closure read so that choosing a
     * folder can re-enter here with what the pick revealed, and every guard --
     * not just the one somebody remembered to recompute -- sees it. The
     * conflict guard is the one that hurts most when it does not (its `action`
     * comes from `fusionWorkflowView`, whose `not-configured` reading has a
     * null `action`, which disarms it and turns an update into an unbound
     * create send over a live link) but it is not special, and singling it out
     * would leave the next guard added here with the same defect.
     */
    const attempt = async (
      current: FusionCadStatus | null,
      mayChooseFolder: boolean,
    ): Promise<WgLinkExportResponse | null> => {
      // First, because no status is not a status to decide from, and every way
      // in reaches this line. `fusionWorkflowView(null)` is "Checking Fusion
      // 360…" with `action: 'open'` -- a create send carrying no expected
      // document, instance or return-state hash, over whatever link the
      // document really has -- and the Send control is live while it shows,
      // both on the first heartbeat and in the window a parameter edit opens
      // by blanking the status. Guarding one caller would leave the others.
      if (current === null) {
        throw refuse(
          'WG could not check the Fusion link, so it sent nothing rather than risk '
          + 'replacing that link with a new document. Try Send again in a moment.',
        );
      }
      if (current.state === 'instance_selection_required') {
        throw refuse('Choose which linked Fusion instance to update.');
      }
      // Only on an explicit negative. `cadFolderConfigured` is polled with the
      // rest of the Fusion status, and an unknown answer is not a missing
      // folder: the export guard in `sendDesignToCad` is still the check that
      // decides, and this is only what turns its refusal into a way forward.
      if (current.cadFolderConfigured === false) {
        if (!mayChooseFolder) {
          // Asked once and answered; the folder the user chose is still not
          // one WG can use. Sending anyway is the create-over-a-link defect.
          throw refuse(
            'WG still has no shared folder for this design after that choice, so there is '
            + 'nowhere to send it. Check the folder in Settings → CAD Link, and send again.',
          );
        }
        // A pick that could not be re-read answers `null`, which the guard at
        // the top of this function refuses. It is not repeated here: one
        // statement of the rule is what keeps every entry covered by it.
        return attempt(await chooseCadFolder(), false);
      }
      const action = fusionWorkflowView(current).action;
      if (action === 'update' && current.fusionChangesAvailable && !options?.confirmed) {
        setPendingFusionConflict(true);
        return null;
      }
      setPendingFusionConflict(false);
      return sendToFusion(action === 'update' && current.documentId && current.link
        ? { documentId: current.documentId, instanceId: current.link.instanceId, returnStateHash: current.link.documentSignatureHash }
        : undefined);
    };
    return attempt(fusionStatus, true);
  }, [chooseCadFolder, fusionStatus, refuse, sendToFusion]);

  const cancelFusionConflict = useCallback(() => setPendingFusionConflict(false), []);

  return {
    sendingToFusion,
    pendingFusionConflict,
    sendWgToFusion,
    cancelFusionConflict,
  };
}
