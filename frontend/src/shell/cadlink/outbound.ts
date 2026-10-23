import { createStore } from 'zustand/vanilla';
import { getFusionCadStatus } from '../../api/cadlink';
import { getCadOperation, type CadOperationSummary } from '../../api/cadOperations';
import { selectCadWorkspace } from '../../api/cadWorkspace';
import { sendDesignToCad } from '../../api/designIo';
import { currentModel, type CurrentModel } from '../../model/currentModel';
import { assertCurrentModelExport } from '../../results/exportContext';
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
    requests: {} as Record<string, string>,
    sendingToFusion: false, pendingFusionConflict: false,
    error: null as string | null, status: null as string | null,
  }));
  let generation = 0;
  let active = true;
  let folderPick: ReturnType<typeof selectCadWorkspace> | null = null;
  // Confirmation authorizes only the exact target and WG revision displayed.
  let conflict: { evidence: string; model: CurrentModel } | null = null;
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
    const picking = selectCadWorkspace(undefined, fetcher);
    folderPick = picking;
    void picking.catch(() => undefined).finally(() => { if (folderPick === picking) folderPick = null; });
    return picking;
  };
  const sendWgToFusion = async (command?: { confirmed?: boolean }) => {
    if (!active) throw new OutboundSupersededError('The Fusion command controller was stopped.');
    const request = ++generation;
    const load = currentDocumentLoad();
    // A conflict confirmation continues the original model's command.
    const model = command?.confirmed && conflict ? conflict.model : currentModel();
    const current = () => active && request === generation;
    const assertCurrent = () => {
      if (!current() || !isCurrentDocumentLoad(load)) {
        throw new OutboundSupersededError('Another design or command superseded this Fusion send.');
      }
      assertCurrentModelExport(model, 'step');
    };
    store.setState({ sendingToFusion: true, error: null, status: null });
    options.noteCadActivity?.();
    let evidence: CadConnectionStatus | null = null;
    let exportedIdentity: ReturnType<typeof useDocumentStore.getState>['identity'] | undefined;
    try {
      assertCurrent();
      return await runGuardedFusionSend({
        current: null, readStatusFirst: true,
        readStatus: async () => { evidence = await readStatus(); assertCurrent(); return evidence; },
        chooseCadFolder: async () => {
          const choice = await chooseFolder();
          assertCurrent();
          if (!choice.selected) throw new Error('Check the WGLink folder in Settings → CAD Link, then send again.');
          evidence = await readStatus();
          assertCurrent();
          return evidence;
        },
        // A confirmation is rechecked after the status read, in setConflict/send.
        confirmed: command?.confirmed,
        refuse: (message) => new Error(message),
        setConflict: (pending) => {
          assertCurrent();
          if (pending) conflict = { evidence: evidenceKey(evidence), model };
          store.setState({ pendingFusionConflict: pending });
        },
        sendToFusion: async (target) => {
          assertCurrent();
          if (fusionWorkflowView(evidence).action === 'update' && (!target?.documentId || !target.instanceId)) {
            throw new Error('Fusion changed documents. Refresh CAD Link and try again.');
          }
          if (target && evidence?.fusionChangesAvailable
            && (!command?.confirmed || conflict?.evidence !== evidenceKey(evidence))) {
            conflict = { evidence: evidenceKey(evidence), model };
            store.setState({ pendingFusionConflict: true });
            // Return null through the outer command without reaching transport.
            throw new ConflictChangedError();
          }
          conflict = null;
          const { design, designRevision } = useDesignStore.getState();
          const { identity, designName, designUid } = useDocumentStore.getState();
          const polar = polarConfigFromUi(useSolveOptionsStore.getState().polar);
          const sentKey = keptContentKeyOf(design);
          exportedIdentity = identity;
          const result = await sendDesignToCad(design, designRevision, designNameSlug(designName),
            identity, fetcher, undefined, target ?? null, polar, undefined, designUid, assertCurrent);
          if (active && result.cadHandoffRequestId && result.cadHandoff !== 'failed') {
            const id = result.cadHandoffRequestId;
            // A terminal push may have beaten the export response.
            if (!store.getState().requests[id]) store.setState({ requests: { ...store.getState().requests, [id]: 'received' } });
          }
          let sameModel = true;
          try { assertCurrentModelExport(model, 'step'); } catch { sameModel = false; }
          const adopt = sameModel && current() && isCurrentDocumentLoad(load)
            && useDocumentStore.getState().identity === identity
            && useDocumentStore.getState().designUid === designUid;
          rememberSentCopy(sentKey, result.identity?.designId, adopt);
          if (!current()) return result;
          if (!adopt) {
            store.setState({ status: `Sent to Fusion 360 · sequence ${result.sequence}. ${sameModel ? 'The Fusion link' : 'The model'} changed while it was sending, so the returned CAD link was not adopted.` });
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
      if (current()) { conflict = null; store.setState({ pendingFusionConflict: false }); }
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
    observeOperation(operation: CadOperationSummary) {
      if (active && (operation.kind === 'insert_link' || operation.kind === 'update_link')) {
        store.setState({ requests: { ...store.getState().requests, [operation.operationId]: operation.state } });
      }
    },
    async reconcile(ids: readonly string[]): Promise<ReadonlySet<string>> {
      const settled = new Set<string>();
      await Promise.all(ids.map(async (id) => {
        try {
          const operation = await getCadOperation(id, fetcher);
          if (operation.operationId !== id || !active) return;
          store.setState({ requests: { ...store.getState().requests, [id]: operation.state } });
          settled.add(id);
        } catch { /* The exact outcome remains retryable. */ }
      }));
      return settled;
    },
    confirmFusionConflict: () => sendWgToFusion({ confirmed: true }),
    cancelFusionConflict() { conflict = null; store.setState({ pendingFusionConflict: false }); },
    dispose() { active = false; generation += 1; conflict = null;
      store.setState({ sendingToFusion: false, pendingFusionConflict: false }); },
  });
}
class ConflictChangedError extends Error {}
