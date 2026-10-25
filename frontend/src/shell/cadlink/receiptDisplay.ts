import { waitForCadProjectSettings } from '../../stores/cadProjectSettings';
import { CadLinkApiError, listReturns } from '../../api/cadlink';
import { useCadReturnStore } from '../../stores/cadReturn';
import { cadReceiptsStore, type CadReceipt, type CadReceiptsStore, type ReceiptFeedback } from '../../stores/cadReceipts';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { useDocumentStore } from '../../stores/document';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { createCadArrivalActions, SupersededError, returnBelongsToAnotherProject } from './arrivals';
import type { SolvedModelDisplay } from './solvedModel';
import { reportCadAutomationError } from './caughtErrors';

/** The existing artifact display path is supplied by the shell's display owner.
 * This action owns no parametric-document writes and never submits a solve.
 */
export interface ReceiptDisplay extends SolvedModelDisplay {
  enterCadWorkspace(): void;
  isActive?(): boolean;
  refuseAutomaticRestore?(): void;
  manualSelectionAt?(): number | null;
  automaticSelectionAt?(): number;
  prepareSelected?: ReturnType<typeof createCadArrivalActions>['ingestSelected'];
}

/** Receipt-keyed Send display mounted beside the feed.
 * True means displayed or explicitly reported; false keeps a transient failure
 * pending. The receipt store persists this distinction across reloads.
 */
export function createReceiptDisplay(display: ReceiptDisplay, store: CadReceiptsStore = cadReceiptsStore,
  fetcher: typeof fetch = fetch): (receipt: CadReceipt) => Promise<boolean> {
  const prepareSelected = display.prepareSelected ?? createCadArrivalActions({
    enterCadWorkspace: display.enterCadWorkspace, showIngestedMesh: (record, name, notice, transport = fetcher, generation = importedMeshStore.captureGeneration()) =>
      display.showIngestedMesh(record, name, notice, transport, generation),
    reportViewportNotice: display.reportViewportNotice,
  }, fetcher).ingestSelected;
  let feedId = store.getState().feedId;
  let newestSequence = store.getState().newestSequence;
  let newestSentAt = store.getState().newestSentAt;
  let newestCursor = 0;
  let newestReceiptId: string | null = null;
  const inFlight = new Map<string, Promise<boolean>>();
  // A newer selection kept the screen; when that selection is this very
  // return (chosen again, or a Fusion solve put it there), nothing was kept
  // from the user, so the line only says it arrived.
  const supersededBySelection = (name: string, bundlePath: string | null | undefined) =>
    bundlePath && useCadReturnStore.getState().selectedBundle?.bundlePath === bundlePath
      ? `Received ${name}.` : `Received ${name}; your newer selection stays on screen.`;
  const report = (receipt: CadReceipt, outcome: ReceiptFeedback['outcome'], message: string) => {
    store.getState().report(receipt.receipt_id, { outcome, message });
  };
  const run = async (receipt: CadReceipt): Promise<boolean> => {
    if (display.isActive?.() === false) return false;
    if (feedId !== store.getState().feedId) {
      feedId = store.getState().feedId;
      newestSequence = store.getState().newestSequence;
      newestSentAt = store.getState().newestSentAt;
      newestCursor = 0;
      newestReceiptId = null;
    }
    const receiptFeed = feedId;
    const name = receipt.document_name ?? 'the model Fusion sent';
    if (receipt.outcome === 'refused') {
      display.refuseAutomaticRestore?.();
      report(receipt, 'refused', `Fusion sent ${name}, but WG could not take it: ${receipt.message ?? receipt.reason ?? 'please send it again from Fusion'}`);
      display.enterCadWorkspace();
      return true;
    }
    const sentAt = Date.parse(receipt.sent_at);
    // Do not even take an ingest/viewport intent from a newer solve. Rejecting
    // only after listing would cancel that solve's in-flight model load.
    if ((display.automaticSelectionAt?.() ?? 0) > sentAt) {
      report(receipt, 'superseded', supersededBySelection(name, receipt.bundle_path));
      return true;
    }
    if ((receipt.send_sequence !== null && receipt.send_sequence < newestSequence)
      || (receipt.send_sequence === null && (sentAt < newestSentAt
        || (sentAt === newestSentAt && receipt.receipt_id !== newestReceiptId)))) {
      report(receipt, 'superseded', `Received ${name}; a newer Send stays selected.`);
      return true;
    }
    newestSequence = Math.max(newestSequence, receipt.send_sequence ?? 0);
    newestSentAt = Math.max(newestSentAt, sentAt);
    newestCursor = receipt.cursor;
    newestReceiptId = receipt.receipt_id;
    if (workspaceModeStore.getSnapshot().importedInput === 'file') {
      report(receipt, 'superseded', `Received ${name}; your mesh file stays selected. Select CAD Link to use a CAD model.`);
      return true;
    }
    const inputRevision = workspaceModeStore.getSnapshot().inputRevision;
    const document = useDocumentStore.getState();
    const choice = useCadReturnStore.getState().captureModelChoice();
    const progress = { phase: 'listing' as 'listing' | 'ingest' | 'display' };
    let generation = useCadReturnStore.getState().beginIngestIntent();
    let baselineViewport = importedMeshStore.beginIntent();
    const current = () => display.isActive?.() !== false && store.getState().feedId === receiptFeed && receipt.cursor === newestCursor
      && (display.automaticSelectionAt?.() ?? 0) <= sentAt
      && workspaceModeStore.getSnapshot().inputRevision === inputRevision
      && useDocumentStore.getState().identity === document.identity
      && useCadReturnStore.getState().isCurrentModelChoice(choice)
      && (progress.phase === 'listing' || (useCadReturnStore.getState().isCurrentIngestIntent(generation)
        && importedMeshStore.isCurrentGeneration(baselineViewport)));
    const superseded = () => {
      if (display.isActive?.() === false) return false;
      if (store.getState().feedId !== receiptFeed) return true;
      report(receipt, 'superseded', supersededBySelection(name, receipt.bundle_path));
      return true;
    };
    let transportFailed = false;
    const transport: typeof fetch = async (...args) => {
      try { return await fetcher(...args); } catch (error) { transportFailed = true; throw error; }
    };
    try {
      // Read the source even if a retained receipt says it was available. The
      // folder may have changed since the feed page was made. Never getIngest
      // or open a retained copy when the source entry has gone.
      const listing = await listReturns(transport);
      if (!current()) return superseded();
      const bundle = listing.items.find((item) => item.bundlePath === receipt.bundle_path);
      if (!bundle || !bundle.readable) {
        display.refuseAutomaticRestore?.();
        const outcome = !listing.cadFolderConfigured ? 'retained_copy' : bundle ? 'unreadable' : 'missing_source';
        report(receipt, outcome, outcome === 'unreadable'
          ? `Received ${name}, but its WGLink folder entry is unreadable: ${bundle?.reason ?? 'could not read the model'}. Send it again from Fusion.`
          : outcome === 'retained_copy'
            ? `WG retained ${name}, but no WGLink folder is selected. Choose the folder and Send it again from Fusion.`
            : `Received ${name}, but it is no longer in the WGLink folder. Send it again from Fusion.`);
        display.enterCadWorkspace();
        return true;
      }
      if (returnBelongsToAnotherProject(bundle, document.identity?.designId)) {
        display.refuseAutomaticRestore?.();
        report(receipt, 'refused', `Received ${name}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`);
        display.enterCadWorkspace();
        return true;
      }
      const pickedAt = display.manualSelectionAt?.();
      if ((pickedAt != null && pickedAt > sentAt) || (display.automaticSelectionAt?.() ?? 0) > sentAt) return superseded();
      // One settings-retention policy, owned by cadReturn (S5-4).
      useCadReturnStore.getState().selectArrivedBundle(bundle, receipt.project_id, transport);
      if (!await waitForCadProjectSettings()) throw new Error('Could not load model settings. Retry the Send.');
      if (!current()) return superseded();
      let changedSinceSend = false;
      try {
        await prepareSelected({
          fetcher: transport, isCurrent: current,
          onStarted: (intent, viewport) => { generation = intent; baselineViewport = viewport; progress.phase = 'ingest'; },
          validate: (record) => {
            if (receipt.manifest_sha256 && record.manifest_sha256 !== receipt.manifest_sha256) {
              changedSinceSend = true;
              throw new Error(`The WGLink folder entry for ${name} changed after this Send. Send it again from Fusion.`);
            }
          },
          onPrepared: () => { progress.phase = 'display'; },
        });
      } catch (error) {
        if (changedSinceSend) {
          report(receipt, 'unreadable', error instanceof Error ? error.message : String(error));
          display.enterCadWorkspace();
          return true;
        }
        throw error;
      }
      if (!current()) return superseded();
      report(receipt, 'accepted', `Received ${name} from Fusion 360.`);
      return true;
    } catch (error) {
      reportCadAutomationError(`receipt-${progress.phase}`, error, { receipt_id: receipt.receipt_id, ingest_id: receipt.ingest_id });
      if (error instanceof SupersededError || !current()) return superseded();
      // The shared preparation owner already opened the one ceiling question.
      if (error instanceof CadLinkApiError && error.code === 'mesh_too_large') return true;
      const classifiedFailure = error instanceof CadLinkApiError && !!error.failure;
      const transient = !classifiedFailure && (transportFailed || progress.phase === 'listing'
        || (error instanceof CadLinkApiError && (error.status === 408 || error.status === 429 || (error.status ?? 0) >= 500)));
      const refusal = progress.phase === 'ingest' && error instanceof CadLinkApiError && !transient;
      report(receipt, refusal ? 'refused' : 'display_failed',
        `WG could not open ${name}: ${error instanceof Error ? error.message : String(error)}. ${transient ? 'CAD Link will retry.' : 'Send it again from Fusion.'}`);
      display.enterCadWorkspace();
      return !transient;
    }
  };
  return (receipt) => {
    if (store.getState().fronted.includes(receipt.receipt_id)
      || (receipt.cursor <= store.getState().cursor && !store.getState().receipts[receipt.receipt_id])) return Promise.resolve(true);
    const actionFeed = store.getState().feedId;
    const key = `${actionFeed ?? 'pending'}:${receipt.receipt_id}`;
    const pending = inFlight.get(key);
    if (pending) return pending;
    const action = run(receipt).then((completed) => {
      if (completed && store.getState().feedId === actionFeed) store.getState().markFronted(receipt.receipt_id, receipt);
      return completed;
    }).finally(() => { inFlight.delete(key); });
    inFlight.set(key, action);
    return action;
  };
}
