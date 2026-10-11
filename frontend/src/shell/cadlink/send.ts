import type { FusionCadStatus } from '../../api/cadlink';
import type { WgLinkExportResponse } from '../../api/designIo';
import { fusionWorkflowView } from '../cadWorkflowView';

export interface GuardedFusionSendOptions {
  current: FusionCadStatus | null;
  readStatus(): Promise<FusionCadStatus | null>;
  chooseCadFolder(): Promise<FusionCadStatus | null>;
  refuse(message: string): Error;
  setConflict(pending: boolean): void;
  sendToFusion(target?: { documentId: string; instanceId: string; returnStateHash: string | null }): Promise<WgLinkExportResponse>;
  confirmed?: boolean;
  readStatusFirst?: boolean;
}

/** Shared command boundary for editable WG designs. No caller can choose an
 * unguarded Insert/Update; target evidence is selected inside this boundary. */
export async function runGuardedFusionSend({
  current, readStatus, chooseCadFolder, refuse, setConflict, sendToFusion,
  confirmed = false, readStatusFirst = false,
}: GuardedFusionSendOptions): Promise<WgLinkExportResponse | null> {
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
    // Each allowance is spent once, so the chain is bounded: at most one
    // status read and one folder dialog per press, in either order.
    may: { readStatus: boolean; chooseFolder: boolean },
  ): Promise<WgLinkExportResponse | null> => {
    // First, because no status is not a status to decide from, and every way
    // in reaches this line. `fusionWorkflowView(null)` is "Checking Fusion
    // 360…" with `action: 'open'` -- a create send carrying no expected
    // document, instance or return-state hash, over whatever link the
    // document really has -- and the Send control is live while it shows,
    // both on the first heartbeat and in the window a parameter edit opens
    // by blanking the status. Guarding one caller would leave the others.
    if (current === null) {
      if (!may.readStatus) {
        throw refuse(
          'WG could not check the Fusion link, so it sent nothing rather than risk '
          + 'replacing that link with a new document. Try Send again in a moment.',
        );
      }
      // Press Send, and Send is what re-reads. The heartbeat poll is
      // suspended while no exchange folder is configured -- the add-in
      // writes the heartbeat into that folder, so there is nothing there to
      // read -- which makes the read at mount the only one there will be. If
      // that one failed, waiting recovers nothing and the folder dialog is
      // never reached, so refusing here with "try again in a moment" would
      // promise a recovery no amount of waiting delivers. The folder
      // allowance is carried through: a status that now says there is no
      // folder still has to be able to ask for one.
      return attempt(await readStatus(), { ...may, readStatus: false });
    }
    if (current.state === 'instance_selection_required') {
      throw refuse('Choose which linked Fusion instance to update.');
    }
    // Only on an explicit negative. `cadFolderConfigured` is polled with the
    // rest of the Fusion status, and an unknown answer is not a missing
    // folder: the export guard in `sendDesignToCad` is still the check that
    // decides, and this is only what turns its refusal into a way forward.
    if (current.cadFolderConfigured === false) {
      if (!may.chooseFolder) {
        // Asked once and answered; the folder the user chose is still not
        // one WG can use. Sending anyway is the create-over-a-link defect.
        throw refuse(
          'WG still has no shared folder for this design after that choice, so there is '
          + 'nowhere to send it. Check the folder in Settings → CAD Link, and send again.',
        );
      }
      // A pick that could not be re-read answers `null`, which the guard at
      // the top of this function refuses -- and does not re-read, because the
      // pick has just done that. One statement of the rule, every entry
      // covered by it.
      return attempt(await chooseCadFolder(), { readStatus: false, chooseFolder: false });
    }
    const view = fusionWorkflowView(current);
    const action = view.action;
    // No action means WG cannot say what a send would do to the document:
    // the add-in is offline or outdated, an update is being recovered, or
    // Fusion already holds this design. Sending anyway would be a create
    // with no expected document -- a second Fusion document over a linked
    // one, the create-over-a-link defect -- so it is refused with the reason.
    if (action === null) {
      throw refuse(view.state === 'current'
        ? 'Fusion already holds this design as it is in WG, so there is nothing to send.'
        : `WG did not send this design: ${view.headline}. ${view.detail}`);
    }
    if (action === 'update' && current.fusionChangesAvailable && !confirmed) {
      setConflict(true);
      return null;
    }
    setConflict(false);
    return sendToFusion(action === 'update' && current.documentId && current.link
      ? { documentId: current.documentId, instanceId: current.link.instanceId, returnStateHash: current.link.documentSignatureHash }
      : undefined);
  };
  return readStatusFirst
    ? attempt(await readStatus(), { readStatus: false, chooseFolder: true })
    : attempt(current, { readStatus: true, chooseFolder: true });
}
