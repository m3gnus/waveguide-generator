import { createStore } from 'zustand/vanilla';
import { getFusionCadStatus } from '../../api/cadlink';
import { selectCadWorkspace } from '../../api/cadWorkspace';
import { sendDesignToCad } from '../../api/designIo';
import { keptContentKeyOf, rememberSentCopy } from '../../design/replacementCheck';
import { useCadConnectionStore, type CadConnectionStore, type CadConnectionStatus } from '../../stores/cadConnection';
import { useCadReturnStore } from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, recordCommittedAthPolars, useDesignStore } from '../../stores/design';
import { useDocumentStore } from '../../stores/document';
import { documentSettingsSignature } from '../../stores/designWire';
import { fusionWorkflowView } from '../cadWorkflowView';
import { designNameSlug } from '../../stores/designName';
import { polarConfigFromUi, useSolveOptionsStore } from '../../stores/solveOptions';
import { runGuardedFusionSend } from './send';

export class OutboundSupersededError extends Error {}
/** UI callers return quietly from their catch when true. */
export function isOutboundSupersededError(error: unknown): error is OutboundSupersededError {
  return error instanceof OutboundSupersededError;
}
export interface CadOutboundOptions {
  connection?: CadConnectionStore;
  fetcher?: typeof fetch;
  readFusionStatus?: () => Promise<CadConnectionStatus>;
  selectedInstanceId?: () => string | null;
  refresh?: () => Promise<void>;
  noteCadActivity?: () => void;
}

/** Supplies the guarded command to the menu,
 * parameter panel, CAD Link panel and conflict dialog. Historical run exports
 * carry their own immutable design and do not adopt the editable identity. */
export function createCadOutboundController(options: CadOutboundOptions = {}) {
  const connection = options.connection ?? useCadConnectionStore;
  const fetcher = options.fetcher ?? fetch;
  const instance = options.selectedInstanceId ?? (() => connection.getState().fusionStatus?.selectedInstanceId ?? null);
  const store = createStore(() => ({
    sendingToFusion: false, pendingFusionConflict: false,
    error: null as string | null, status: null as string | null,
  }));
  let generation = 0;
  let active = true;
  let folderPick: Promise<CadConnectionStatus | null> | null = null;
  // Confirmation authorizes only the exact target and WG revision displayed.
  let conflict: string | null = null;
  const evidenceKey = (status: CadConnectionStatus | null) => JSON.stringify([
    currentDocumentLoad(), useDesignStore.getState().designRevision, documentSettingsSignature(),
    useDocumentStore.getState().identity,
    status?.documentId, status?.link?.instanceId, status?.link?.documentSignatureHash,
  ]);
  const readStatus = async () => {
    const load = currentDocumentLoad();
    const revision = useDesignStore.getState().designRevision;
    const settings = documentSettingsSignature();
    const identity = useDocumentStore.getState().identity;
    const selected = instance();
    const result = await connection.readCommand(options.readFusionStatus ?? ((signal) => getFusionCadStatus(
      useDesignStore.getState().design, useDocumentStore.getState().identity,
      useCadReturnStore.getState().selectedBundle?.bundlePath ?? null, (input, init) => fetcher(input, { ...init, signal }), selected,
    )));
    // An answer measured for another editor or instance cannot drive a send.
    return isCurrentDocumentLoad(load) && revision === useDesignStore.getState().designRevision
      && settings === documentSettingsSignature() && identity === useDocumentStore.getState().identity
      && (selected === instance() || (!options.selectedInstanceId && selected === null)) ? result : null;
  };
  const chooseFolder = () => {
    if (folderPick) return folderPick;
    const picking = (async () => {
      if (!(await selectCadWorkspace(undefined, fetcher)).selected) {
        throw new Error('WG and Fusion share a folder, and none is chosen yet, so there is nowhere to send '
          + 'this design. Choose the folder when WG asks, or set it in Settings → CAD Link, and send again.');
      }
      return readStatus();
    })();
    folderPick = picking;
    void picking.catch(() => undefined).finally(() => { if (folderPick === picking) folderPick = null; });
    return picking;
  };
  const sendWgToFusion = async (command?: { confirmed?: boolean }) => {
    if (!active) throw new OutboundSupersededError('The Fusion command controller was stopped.');
    const request = ++generation;
    const load = currentDocumentLoad();
    const current = () => active && request === generation;
    const assertCurrent = () => {
      if (!current() || !isCurrentDocumentLoad(load)) {
        throw new OutboundSupersededError('Another design or command superseded this Fusion send.');
      }
    };
    store.setState({ sendingToFusion: true, error: null, status: null });
    options.noteCadActivity?.();
    let evidence: CadConnectionStatus | null = null;
    let exportedIdentity: ReturnType<typeof useDocumentStore.getState>['identity'] | undefined;
    try {
      return await runGuardedFusionSend({
        current: null, readStatusFirst: true,
        readStatus: async () => { evidence = await readStatus(); assertCurrent(); return evidence; },
        chooseCadFolder: async () => { evidence = await chooseFolder(); assertCurrent(); return evidence; },
        // A confirmation is rechecked after the status read, in setConflict/send.
        confirmed: command?.confirmed,
        refuse: (message) => new Error(message),
        setConflict: (pending) => {
          assertCurrent();
          if (pending) conflict = evidenceKey(evidence);
          store.setState({ pendingFusionConflict: pending });
        },
        sendToFusion: async (target) => {
          assertCurrent();
          if (fusionWorkflowView(evidence).action === 'update' && (!target?.documentId || !target.instanceId)) {
            throw new Error('Fusion changed documents. Refresh CAD Link and try again.');
          }
          if (target && evidence?.fusionChangesAvailable
            && (!command?.confirmed || conflict !== evidenceKey(evidence))) {
            conflict = evidenceKey(evidence);
            store.setState({ pendingFusionConflict: true });
            // Return null through the outer command without reaching transport.
            throw new ConflictChangedError();
          }
          conflict = null;
          const { design, designRevision } = useDesignStore.getState();
          const { identity, designName } = useDocumentStore.getState();
          const polar = polarConfigFromUi(useSolveOptionsStore.getState().polar);
          const sentKey = keptContentKeyOf(design);
          exportedIdentity = identity;
          const result = await sendDesignToCad(design, designRevision, designNameSlug(designName),
            identity, fetcher, undefined, target ?? null, polar);
          const adopt = current() && isCurrentDocumentLoad(load)
            && useDocumentStore.getState().identity === identity;
          rememberSentCopy(sentKey, result.identity?.designId, adopt);
          if (!current()) return result;
          if (!adopt) {
            store.setState({ status: `Sent to Fusion 360 · sequence ${result.sequence}. The Fusion link changed while it was sending, so the returned CAD link was not adopted.` });
            return result;
          }
          try {
            if (JSON.stringify(polarConfigFromUi(useSolveOptionsStore.getState().polar)) === JSON.stringify(polar)) {
              recordCommittedAthPolars(polar);
            }
          } catch { /* A half-typed setting is not the one committed. */ }
          if (result.identity) useDocumentStore.getState().setCadLink(result.identity, 'current');
          const superseded = result.cadHandoffSuperseded?.length
            ? ' It replaced an earlier update Fusion had not started yet.' : '';
          const waiting = result.cadHandoffWaiting ? ` ${result.cadHandoffWaiting}` : '';
          store.setState({ status: target
            ? `Update sent to Fusion 360 · sequence ${result.sequence}.${superseded}${waiting}`
            : `Opening in Fusion 360 · sequence ${result.sequence}.${waiting}` });
          await options.refresh?.();
          return result;
        },
      });
    } catch (reason) {
      if (reason instanceof ConflictChangedError) return null;
      if (exportedIdentity !== undefined) {
        rememberSentCopy(null, exportedIdentity?.designId, current() && isCurrentDocumentLoad(load));
      }
      if (current() && !(reason instanceof OutboundSupersededError)) {
        store.setState({ error: reason instanceof Error ? reason.message : String(reason) });
      }
      throw reason;
    } finally {
      if (current()) store.setState({ sendingToFusion: false });
    }
  };
  return Object.assign(store, {
    sendWgToFusion,
    confirmFusionConflict: () => sendWgToFusion({ confirmed: true }),
    cancelFusionConflict() { conflict = null; store.setState({ pendingFusionConflict: false }); },
    dispose() { active = false; generation += 1; conflict = null;
      store.setState({ sendingToFusion: false, pendingFusionConflict: false }); },
  });
}
class ConflictChangedError extends Error {}
