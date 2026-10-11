import { createStore } from 'zustand/vanilla';
import { getFusionCadStatus, jsonRequest, listReturns, requestFusionReturn,
  type CadReturnBundle } from '../../api/cadlink';
import { useCadConnectionStore, type CadConnectionStore, type CadConnectionStatus } from '../../stores/cadConnection';
import { useCadReturnStore } from '../../stores/cadReturn';
import { currentDocumentLoad, isCurrentDocumentLoad, subscribeRevision, useDesignStore } from '../../stores/design';
import { useDocumentStore } from '../../stores/document';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { workspaceNavigation } from '../workspaceNavigation';
import { SupersededError, returnBelongsToAnotherProject } from './arrivals';
import { presentCadRefusal, refusalSentence } from './refusals';

export interface FusionReturnOutcome {
  requestId: string;
  state: string;
  reason: string | null;
  message: string | null;
}
export interface CadReturnWaiterOptions {
  connection?: CadConnectionStore;
  fetcher?: typeof fetch;
  readFusionStatus?: () => Promise<CadConnectionStatus>;
  selectedInstanceId?: () => string | null;
  readOutcome?: (requestId: string) => Promise<FusionReturnOutcome>;
  /** Select and display through the S5-4 arrival actions at cut-over. By
   * default selection keeps compatible settings and enters the CAD workspace. */
  onArrival?: (bundle: CadReturnBundle) => void;
  noteCadActivity?: () => void;
}
export const FUSION_RETURN_TIMEOUT_MS = 60_000;
const POLL_MS = 2_500;

/** Deliberate file-transport command, built beside the coordinator for S5-7.
 * The returns listing is authoritative geometry evidence: only a readable
 * item carrying this request id completes a pull. Adapter acceptance alone
 * never completes it. No operation store or live-session client is required. */
export function createCadReturnWaiter(options: CadReturnWaiterOptions = {}) {
  const connection = options.connection ?? useCadConnectionStore;
  const fetcher = options.fetcher ?? fetch;
  const store = createStore(() => ({
    pullingFromFusion: false, requestId: null as string | null,
    error: null as string | null, status: null as string | null,
  }));
  const selectedInstance = options.selectedInstanceId
    ?? (() => connection.getState().fusionStatus?.selectedInstanceId ?? null);
  const readOutcome = options.readOutcome ?? ((id: string) => jsonRequest<FusionReturnOutcome>(
    `/api/cadlink/return-requests/${encodeURIComponent(id)}`, undefined, fetcher,
  ));
  let active = true;
  let pending: {
    promise: Promise<CadReturnBundle>;
    requestId: string | null;
    current(): boolean;
    finish(bundle: CadReturnBundle | null, error?: Error): void;
  } | null = null;
  let reading: Promise<void> | null = null;
  let poll: ReturnType<typeof setTimeout> | null = null;
  let deadline: ReturnType<typeof setTimeout> | null = null;
  const clearTimers = () => {
    if (poll !== null) clearTimeout(poll);
    if (deadline !== null) clearTimeout(deadline);
    poll = deadline = null;
  };
  const refresh = (): Promise<void> => {
    const operation = pending;
    if (!operation?.requestId || !operation.current()) return Promise.resolve();
    if (reading) return reading;
    const id = operation.requestId;
    const read = (async () => {
      // An outcome read may fail while geometry succeeds, or vice versa. An
      // absent listing/outcome is not terminal; the independent deadline wins.
      let returned: CadReturnBundle | undefined;
      const listing = listReturns(fetcher).then((page) => {
        if (pending !== operation || !operation.current()) return;
        returned = page.items.find((item) => item.readable && item.requestId === id);
        if (!returned) return;
        if (returnBelongsToAnotherProject(returned, useDocumentStore.getState().identity?.designId)) {
          operation.finish(null, new Error(`Received ${returned.documentName ?? returned.name}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`));
        } else operation.finish(returned);
      }).catch(() => undefined);
      const answer = readOutcome(id).then(async (outcome) => {
        // Give a listing already in hand precedence over adapter diagnostics.
        await Promise.resolve();
        if (pending !== operation || !operation.current() || returned) return;
        if (outcome.requestId === id && ['rejected', 'cancelled', 'failed'].includes(outcome.state)) {
          const presented = presentCadRefusal(outcome.message);
          operation.finish(null, new Error(presented ? refusalSentence(presented)
            : `Fusion ${outcome.state === 'cancelled' ? 'did not carry out' : 'refused'} the request for this model’s geometry, and reported no detail.`));
        }
      }).catch(() => undefined);
      // One stalled endpoint must not hold either settlement or the next poll
      // hostage. Late handlers retain the operation fence and settle only once.
      await Promise.race([listing, answer, operation.promise.then(() => undefined, () => undefined)]);
    })().finally(() => {
      if (reading === read) reading = null;
      if (pending === operation && operation.current()) {
        if (poll !== null) clearTimeout(poll);
        poll = setTimeout(() => { poll = null; void refresh(); }, POLL_MS);
      }
    });
    reading = read;
    return read;
  };
  // Deliberately not async: repeated presses get the identical promise, even
  // before the POST answers with its correlation id.
  const pullFromFusion = (): Promise<CadReturnBundle> => {
    if (pending) return pending.promise;
    if (!active) return Promise.reject(new SupersededError('The geometry return waiter was stopped.'));
    const load = currentDocumentLoad();
    const choice = useCadReturnStore.getState().captureModelChoice();
    const identity = useDocumentStore.getState().identity;
    const instance = selectedInstance();
    let expectedInstance = instance;
    let requesting = false;
    let resolve!: (bundle: CadReturnBundle) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<CadReturnBundle>((yes, no) => { resolve = yes; reject = no; });
    const stops: Array<() => void> = [];
    const operation = {
      promise, requestId: null as string | null,
      current: () => active && pending === operation,
      finish: (returned: CadReturnBundle | null, error?: Error) => {
        if (!operation.current()) return;
        // Clear before selection, callbacks and promise settlement. Reentrant
        // manual picks and duplicate reads cannot settle this operation twice.
        pending = null; reading = null; clearTimers(); stops.forEach((stop) => stop());
        store.setState({ pullingFromFusion: false, requestId: null });
        if (returned) {
          store.setState({ status: `Received ${returned.documentName ?? returned.name} from Fusion 360.` });
          try {
            if (options.onArrival) options.onArrival(returned);
            else {
              const continuity = useCadReturnStore.getState().selectArrivedBundle(returned);
              importedMeshStore.beginIntent();
              workspaceModeStore.setMode('cad'); workspaceNavigation.activate('cadlink');
              store.setState({ status: `Received ${returned.documentName ?? returned.name} from Fusion 360.${
                continuity === 'carried' ? ' Kept your mesh, channel, and solve settings.' : ''}` });
            }
            resolve(returned);
          } catch (reason) {
            const failed = reason instanceof Error ? reason : new Error(String(reason));
            store.setState({ error: failed.message }); reject(failed);
          }
        } else {
          const failed = error ?? new SupersededError('A newer model selection superseded this geometry request.');
          if (failed instanceof SupersededError) store.setState({ status: failed.message });
          else store.setState({ error: failed.message });
          reject(failed);
        }
      },
    };
    pending = operation;
    store.setState({ pullingFromFusion: true, requestId: null, error: null, status: null });
    const changed = () => {
      if (!isCurrentDocumentLoad(load) || !useCadReturnStore.getState().isCurrentModelChoice(choice)
        || identity !== useDocumentStore.getState().identity
        || ((requesting || options.selectedInstanceId) && expectedInstance !== selectedInstance())) {
        operation.finish(null);
      }
    };
    stops.push(useCadReturnStore.subscribe(changed), useDesignStore.subscribe(changed),
      useDocumentStore.subscribe(changed), connection.subscribe(changed), subscribeRevision(changed));
    deadline = setTimeout(() => operation.finish(null, new Error(
      'Fusion did not return the requested model within 60 seconds. Check Fusion for a WGLink message, then retry.',
    )), FUSION_RETURN_TIMEOUT_MS);
    options.noteCadActivity?.();
    void (async () => {
      try {
        const status = await connection.read(options.readFusionStatus ?? (() => getFusionCadStatus(
          useDesignStore.getState().design, identity,
          useCadReturnStore.getState().selectedBundle?.bundlePath ?? null, fetcher, instance,
        )));
        if (!operation.current()) return;
        expectedInstance = selectedInstance(); requesting = true;
        if (!identity?.designId || !status?.documentId || !status.link
          || status.state === 'instance_selection_required' || status.state === 'addin_outdated'
          || !status.running || status.cadFolderConfigured === false
          || status.link.designId !== identity.designId) {
          throw new Error('Fusion changed documents. Refresh CAD Link and try again.');
        }
        const result = await requestFusionReturn({ designId: identity.designId,
          documentId: status.documentId, instanceId: status.link.instanceId,
          expectedReturnStateHash: status.link.documentSignatureHash }, fetcher);
        if (!operation.current()) return;
        operation.requestId = result.requestId;
        store.setState({ requestId: result.requestId,
          status: `Requested current geometry from ${result.documentName}. Waiting for Fusion…` });
        void refresh();
      } catch (reason) {
        operation.finish(null, reason instanceof Error ? reason : new Error(String(reason)));
      }
    })();
    return promise;
  };
  return Object.assign(store, {
    pullFromFusion, refresh,
    awaitedRequestIds: () => pending?.requestId ? [pending.requestId] : [],
    supersede() { pending?.finish(null); },
    dispose() { pending?.finish(null); active = false; clearTimers(); },
  });
}
