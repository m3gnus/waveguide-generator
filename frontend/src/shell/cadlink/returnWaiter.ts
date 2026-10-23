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
  readOutcome?: (requestId: string, signal: AbortSignal) => Promise<FusionReturnOutcome>;
  /** Select and display through the shared arrival actions. By
   * default selection keeps compatible settings and enters the CAD workspace. */
  onArrival?: (bundle: CadReturnBundle) => void;
  noteCadActivity?: () => void;
}
export const FUSION_RETURN_TIMEOUT_MS = 60_000;
const POLL_MS = 2_500;

/** Deliberate file-transport command.
 * The returns listing is authoritative geometry evidence: only a readable
 * item carrying this request id completes a pull. Adapter acceptance alone
 * never completes it. No operation store or live-session client is required. */
export function createCadReturnWaiter(options: CadReturnWaiterOptions = {}) {
  const connection = options.connection ?? useCadConnectionStore;
  const fetcher = options.fetcher ?? fetch;
  const store = createStore(() => ({
    pullingFromFusion: false, requestId: null as string | null,
    error: null as string | null, status: null as string | null,
    errorDiagnostics: null as { message: string; detail: string } | null,
  }));
  const selectedInstance = options.selectedInstanceId
    ?? (() => connection.getState().fusionStatus?.selectedInstanceId ?? null);
  const readOutcome = options.readOutcome ?? ((id: string, signal: AbortSignal) => jsonRequest<FusionReturnOutcome>(
    `/api/cadlink/return-requests/${encodeURIComponent(id)}`, { signal }, fetcher,
  ));
  let active = true;
  const reconciled = new Set<string>();
  let pending: {
    promise: Promise<CadReturnBundle>;
    requestId: string | null;
    current(): boolean;
    finish(bundle: CadReturnBundle | null, error?: Error): void;
  } | null = null;
  let reading: Promise<void> | null = null;
  let readingAbort: AbortController | null = null;
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
    const started = Date.now();
    const controller = new AbortController();
    readingAbort = controller;
    const aborted = new Promise<void>((resolve) => {
      controller.signal.addEventListener('abort', () => resolve(), { once: true });
    });
    // Bound the whole batch, including response body reads. One pair of
    // independent endpoints is active at a time; a stalled peer is cancelled
    // before another poll can start.
    const readDeadline = setTimeout(() => controller.abort(), POLL_MS);
    const pollingFetch: typeof fetch = (input, init) => fetcher(input, { ...init, signal: controller.signal });
    const read = (async () => {
      // An outcome read may fail while geometry succeeds, or vice versa. An
      // absent listing/outcome is not terminal; the independent deadline wins.
      let returned: CadReturnBundle | undefined;
      const listing = listReturns(pollingFetch).then((page) => {
        if (controller.signal.aborted || pending !== operation || !operation.current()) return;
        returned = page.items.find((item) => item.readable && item.requestId === id);
        if (!returned) return;
        reconciled.add(id);
        if (returnBelongsToAnotherProject(returned, useDocumentStore.getState().identity?.designId)) {
          operation.finish(null, new Error(`Received ${returned.documentName ?? returned.name}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`));
        } else operation.finish(returned);
      }).catch(() => undefined);
      const answer = readOutcome(id, controller.signal).then(async (outcome) => {
        if (outcome.requestId === id && !controller.signal.aborted) reconciled.add(id);
        // Give a listing already in hand precedence over adapter diagnostics.
        await Promise.resolve();
        if (controller.signal.aborted || pending !== operation || !operation.current() || returned) return;
        if (outcome.requestId === id && ['rejected', 'cancelled', 'failed'].includes(outcome.state)) {
          const presented = presentCadRefusal(outcome.message);
          const sentence = presented ? refusalSentence(presented)
            : `Fusion ${outcome.state === 'cancelled' ? 'did not carry out' : 'refused'} the request for this model’s geometry, and reported no detail.`;
          if (presented) store.setState({ errorDiagnostics: { message: sentence, detail: presented.diagnostics } });
          operation.finish(null, new Error(sentence));
        }
      }).catch(() => undefined);
      await Promise.race([Promise.all([listing, answer]), aborted,
        operation.promise.then(() => undefined, () => undefined)]);
    })().finally(() => {
      clearTimeout(readDeadline);
      controller.abort();
      if (readingAbort === controller) readingAbort = null;
      if (reading === read) reading = null;
      if (pending === operation && operation.current()) {
        if (poll !== null) clearTimeout(poll);
        const remaining = POLL_MS - (Date.now() - started);
        if (remaining <= 0) void refresh();
        else poll = setTimeout(() => { poll = null; void refresh(); }, remaining);
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
    const contextCurrent = () => isCurrentDocumentLoad(load)
      && useCadReturnStore.getState().isCurrentModelChoice(choice)
      && identity === useDocumentStore.getState().identity
      && (!(requesting || options.selectedInstanceId) || expectedInstance === selectedInstance());
    let resolve!: (bundle: CadReturnBundle) => void;
    let reject!: (error: Error) => void;
    const promise = new Promise<CadReturnBundle>((yes, no) => { resolve = yes; reject = no; });
    const stops: Array<() => void> = [];
    const commandAbort = new AbortController();
    const operation = {
      promise, requestId: null as string | null,
      current: (): boolean => {
        if (!active || pending !== operation) return false;
        // Starting an archive restore advances intent without a store update.
        // Subscriptions are only an early hint; every async boundary checks it.
        if (!contextCurrent()) { operation.finish(null); return false; }
        return true;
      },
      finish: (returned: CadReturnBundle | null, error?: Error) => {
        if (!active || pending !== operation) return;
        if (!contextCurrent()) { returned = null; error = undefined; }
        // Clear before selection, callbacks and promise settlement. Reentrant
        // manual picks and duplicate reads cannot settle this operation twice.
        if (operation.requestId) reconciled.add(operation.requestId);
        pending = null; commandAbort.abort(); reading = null; readingAbort?.abort(); readingAbort = null;
        clearTimers(); stops.forEach((stop) => stop());
        store.setState({ pullingFromFusion: false, requestId: null });
        if (returned) {
          store.setState({ status: `Received ${returned.documentName ?? returned.name} from Fusion 360.` });
          try {
            if (options.onArrival) options.onArrival(returned);
            else {
              const continuity = useCadReturnStore.getState().selectArrivedBundle(returned);
              importedMeshStore.beginIntent();
              workspaceModeStore.setMode('cad'); workspaceNavigation.activate('geometry');
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
    store.setState({ pullingFromFusion: true, requestId: null, error: null, status: null, errorDiagnostics: null });
    const changed = () => { operation.current(); };
    stops.push(useCadReturnStore.subscribe(changed), useDesignStore.subscribe(changed),
      useDocumentStore.subscribe(changed), connection.subscribe(changed), subscribeRevision(changed));
    deadline = setTimeout(() => operation.finish(null, new Error(
      'Fusion did not return the requested model within 60 seconds. Check Fusion for a message from the Waveguide Generator add-in in Fusion, then retry.',
    )), FUSION_RETURN_TIMEOUT_MS);
    options.noteCadActivity?.();
    void (async () => {
      try {
        const status = await connection.readCommand(options.readFusionStatus ?? ((signal) => getFusionCadStatus(
          useDesignStore.getState().design, identity,
          useCadReturnStore.getState().selectedBundle?.bundlePath ?? null, (input, init) => fetcher(input, { ...init, signal }), instance,
        )), commandAbort.signal);
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
    async reconcile(ids: readonly string[]): Promise<ReadonlySet<string>> {
      // Pending work still owes fresh evidence; a settled local waiter has no
      // reader left to recreate its outcome after the reconnect feed finishes.
      ids.forEach((id) => { if (pending?.requestId === id) reconciled.delete(id); });
      await refresh();
      return new Set(ids.filter((id) => reconciled.has(id)));
    },
    awaitedRequestIds: () => pending?.requestId ? [pending.requestId] : [],
    supersede() { pending?.finish(null); },
    dispose() { pending?.finish(null); active = false; clearTimers(); },
  });
}
