import { ingestReturn, listReturns } from '../../api/cadlink';
import { useCadReturnStore } from '../../stores/cadReturn';
import { useCadPreparationStore } from '../../stores/cadPreparation';
import { cadReceiptsStore, type CadReceipt, type CadReceiptsStore, type ReceiptFeedback } from '../../stores/cadReceipts';
import { useDocumentStore } from '../../stores/document';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { returnBelongsToAnotherProject } from './arrivals';
import type { SolvedModelDisplay } from './solvedModel';

/** The existing artifact display path is supplied by the shell's display owner.
 * This action owns no parametric-document writes and never submits a solve.
 */
export interface ReceiptDisplay extends SolvedModelDisplay {
  enterCadWorkspace(): void;
  manualSelectionAt?(): number | null;
}

/** Receipt-keyed Send display, ready for S5-7 to mount beside the feed.
 * True means displayed or explicitly reported; false keeps a transient failure
 * pending. The receipt store persists this distinction across reloads.
 */
export function createReceiptDisplay(display: ReceiptDisplay, store: CadReceiptsStore = cadReceiptsStore,
  fetcher: typeof fetch = fetch): (receipt: CadReceipt) => Promise<boolean> {
  const alreadyFronted = Object.values(store.getState().receipts)
    .filter((item) => store.getState().fronted.includes(item.receipt_id) && item.outcome !== 'refused');
  let newestSequence = Math.max(0, ...alreadyFronted.map((item) => item.send_sequence ?? 0));
  let newestSentAt = Math.max(0, ...alreadyFronted.map((item) => Date.parse(item.sent_at)));
  let newestCursor = 0;
  const inFlight = new Map<string, Promise<boolean>>();
  const report = (receipt: CadReceipt, outcome: ReceiptFeedback['outcome'], message: string) => {
    store.getState().report(receipt.receipt_id, { outcome, message });
  };
  const run = async (receipt: CadReceipt): Promise<boolean> => {
    const name = receipt.document_name ?? 'the model Fusion sent';
    if (receipt.outcome === 'refused') {
      report(receipt, 'refused', `Fusion sent ${name}, but WG could not take it: ${receipt.message ?? receipt.reason ?? 'refused'}`);
      display.enterCadWorkspace();
      return true;
    }
    const sentAt = Date.parse(receipt.sent_at);
    if ((receipt.send_sequence !== null && receipt.send_sequence < newestSequence)
      || (receipt.send_sequence === null && sentAt < newestSentAt)) {
      report(receipt, 'superseded', `Received ${name}; a newer Send stays selected.`);
      return true;
    }
    newestSequence = Math.max(newestSequence, receipt.send_sequence ?? 0);
    newestSentAt = Math.max(newestSentAt, sentAt);
    newestCursor = receipt.cursor;
    const document = useDocumentStore.getState();
    let generation = useCadReturnStore.getState().beginIngestIntent();
    const baselineViewport = importedMeshStore.beginIntent();
    const current = () => receipt.cursor === newestCursor
      && useDocumentStore.getState().identity === document.identity
      && useCadReturnStore.getState().isCurrentIngestIntent(generation)
      && importedMeshStore.isCurrentGeneration(baselineViewport);
    const superseded = () => {
      report(receipt, 'superseded', `Received ${name}; your newer selection stays on screen.`);
      return true;
    };
    try {
      // Read the source even if a retained receipt says it was available. The
      // folder may have changed since the feed page was made. Never getIngest
      // or open a retained copy when the source entry has gone.
      const listing = await listReturns(fetcher);
      if (!current()) return superseded();
      const bundle = listing.items.find((item) => item.bundlePath === receipt.bundle_path);
      if (!bundle || !bundle.readable) {
        const outcome = !listing.cadFolderConfigured ? 'retained_copy' : bundle ? 'unreadable' : 'missing_source';
        report(receipt, outcome, outcome === 'unreadable'
          ? `Received ${name}, but its WGLink folder entry is unreadable: ${bundle?.reason ?? 'could not read the model'}. Send it again from Fusion.`
          : outcome === 'retained_copy'
            ? `WG retained ${name}, but no WGLink folder is selected. Choose the folder and Send it again from Fusion.`
            : `Received ${name}, but it is no longer in the WGLink folder. Send it again from Fusion.`);
        display.enterCadWorkspace();
        return true;
      }
      if (returnBelongsToAnotherProject(bundle, document.identity?.designId)
        || (receipt.project_id && document.identity?.lineageId
          && receipt.project_id !== document.identity.lineageId)) {
        report(receipt, 'refused', `Received ${name}, but it belongs to another CAD-linked project. Open that project from File → CAD-linked designs.`);
        display.enterCadWorkspace();
        return true;
      }
      const pickedAt = display.manualSelectionAt?.();
      if (pickedAt != null && pickedAt > Date.parse(receipt.sent_at)) return superseded();
      // One settings-retention policy, owned by cadReturn (S5-4).
      useCadReturnStore.getState().selectArrivedBundle(bundle, receipt.project_id);
      const selected = useCadReturnStore.getState();
      generation = selected.beginIngestIntent();
      const skipped = new Set(selected.skippedSourceIds);
      const record = await ingestReturn({
        bundlePath: bundle.bundlePath, bundleOrigin: bundle.bundleOrigin ?? 'wglink',
        mesh: { rigidSizeMm: selected.rigidSizeMm, transitionMm: selected.transitionMm,
          sourceSizeMm: Object.fromEntries(Object.entries(selected.sourceSizesMm).filter(([id]) => !skipped.has(id))) },
        skippedSourceIds: selected.skippedSourceIds, areaDriftOverrides: selected.areaDriftOverrides,
        expectedDesignId: document.identity?.designId ?? null,
        expectedInstanceId: bundle.solverAnchorInstanceId ?? null,
        symmetryMode: useCadPreparationStore.getState().symmetryMode,
      }, fetcher);
      if (!current()) return superseded();
      // A mutable folder entry must not substitute a different snapshot for
      // the one this receipt accepted. The ingest record is the verified hash.
      if (receipt.manifest_sha256 && record.manifest_sha256 !== receipt.manifest_sha256) {
        report(receipt, 'unreadable', `The WGLink entry for ${name} changed after this Send. Send it again from Fusion.`);
        display.enterCadWorkspace();
        return true;
      }
      if (!useCadReturnStore.getState().applyIngest(record, generation)) return superseded();
      display.enterCadWorkspace();
      await display.showIngestedMesh(record, name, display.reportViewportNotice, fetcher, baselineViewport);
      if (!current()) return superseded();
      report(receipt, 'accepted', `Received ${name} from Fusion 360.`);
      return true;
    } catch (error) {
      if (!current()) return superseded();
      report(receipt, 'display_failed', `WG could not open ${name}: ${error instanceof Error ? error.message : String(error)}. Refresh CAD Link to try again.`);
      display.enterCadWorkspace();
      return false;
    }
  };
  return (receipt) => {
    if (store.getState().fronted.includes(receipt.receipt_id)) return Promise.resolve(true);
    const pending = inFlight.get(receipt.receipt_id);
    if (pending) return pending;
    const action = run(receipt).then((completed) => {
      if (completed) store.getState().markFronted(receipt.receipt_id);
      return completed;
    }).finally(() => { inFlight.delete(receipt.receipt_id); });
    inFlight.set(receipt.receipt_id, action);
    return action;
  };
}
