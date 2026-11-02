import { create } from 'zustand';
import type { CadOperationSummary } from '../api/cadOperations';
import type { WgLinkExportResponse } from '../api/designIo';
import { failureWords } from '../api/httpError';
import { useCadConnectionStore } from '../stores/cadConnection';
import { useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';

/** How long Fusion normally needs; past this the wait is worth explaining. */
export const PICKUP_WAIT_MS = 10_000;
/** How long a finished send stays on screen. A problem stays until dismissed. */
export const SEND_OUTCOME_SHOWN_MS = 20_000;

export interface FusionDeliveryWords { tone: 'status' | 'error'; text: string }

/** Deliveries with nothing left to wait for or do. */
const FINISHED_DELIVERIES: ReadonlySet<unknown> = new Set(['applied', 'cancelled']);

export interface FusionDeliveryFacts {
  operation?: Pick<CadOperationSummary, 'kind' | 'fusionDelivery'> | null;
  /** Until the operation arrives: this send updates a model already in Fusion. */
  update?: boolean;
  fusion?: { processRunning?: boolean; addinDeliveryVersion?: number | null; wgRequestPickup?: number | null } | null;
  /** WG's own remedy for an add-in it knows cannot take the request. */
  waitingRemedy?: string | null;
  waited?: boolean;
}

/** One wording for every Send to CAD surface: what happened to the user's
 * model, and the next step when it needs one (docs/WORDING.md). */
export function fusionDeliveryWords(facts: FusionDeliveryFacts): FusionDeliveryWords {
  const { operation, fusion, waitingRemedy, waited } = facts;
  const update = operation ? operation.kind === 'update_link' : Boolean(facts.update);
  const verb = update ? 'update' : 'open';
  switch (operation?.fusionDelivery ?? 'waiting') {
    case 'claimed': return { tone: 'status', text: `Fusion is ${update ? 'updating' : 'opening'} this model.` };
    case 'applied': return { tone: 'status', text: update ? 'Updated in Fusion.' : 'Opened in Fusion.' };
    // Fusion shows its own reason beside the model; the add-in's text stays there.
    case 'refused': return { tone: 'error', text: `Fusion could not ${verb} this model. Read the reason in Fusion, then send it again from WG.` };
    case 'expired': return { tone: 'error', text: `Fusion did not ${verb} this model in time. Open Fusion, then send it again.` };
    case 'uncertain': return { tone: 'error', text: 'WG cannot tell whether Fusion finished this model. Check it in Fusion before you send it again.' };
    case 'cancelled': return { tone: 'status', text: 'This send was replaced or is no longer needed. Nothing changed in Fusion.' };
    default:
  }
  if (waitingRemedy) return { tone: 'status', text: failureWords(waitingRemedy, 'Restart Fusion to update WGLink, then send the model again.') };
  if (fusion?.processRunning === false) return { tone: 'status', text: 'Fusion is closed. Open Fusion and this model will arrive.' };
  if (!waited) return { tone: 'status', text: `Waiting for Fusion to ${verb} this model.` };
  if (fusion?.wgRequestPickup) return { tone: 'status', text: 'Fusion has not confirmed this model yet. Finish any open command in Fusion.' };
  // Only an add-in that has answered can be judged; silence may be a slow start.
  if (fusion?.processRunning && fusion.addinDeliveryVersion) {
    return { tone: 'status', text: 'WGLink in Fusion cannot take this model. Restart Fusion to update WGLink, then send the model again.' };
  }
  return { tone: 'status', text: 'Fusion has not confirmed this model yet. Check that Fusion has finished starting and WGLink is running.' };
}

export interface FusionSend { requestId: string; update?: boolean; waitingRemedy?: string | null }

export function fusionSendOf(result: WgLinkExportResponse, update = false): FusionSend | null {
  return result.cadHandoffRequestId
    ? { requestId: result.cadHandoffRequestId, update, waitingRemedy: result.cadHandoffWaiting ?? null } : null;
}

/** Follow one send until the caller stops. Reports each change of wording. */
export function watchFusionDelivery(send: FusionSend, report: (words: FusionDeliveryWords, finished: boolean) => void): () => void {
  let waited = false;
  let last: string | null = null;
  // The health store forgets finished operations; the answer they gave stands.
  let operation: CadOperationSummary | undefined;
  const read = () => {
    operation = useCadDeliveryHealthStore.getState().outboundOperations[send.requestId] ?? operation;
    const words = fusionDeliveryWords({ ...send, operation, waited, fusion: useCadConnectionStore.getState().fusionStatus });
    if (words.text === last) return;
    last = words.text;
    report(words, FINISHED_DELIVERIES.has(operation?.fusionDelivery));
  };
  const stops = [useCadDeliveryHealthStore.subscribe(read), useCadConnectionStore.subscribe(read)];
  const timer = setTimeout(() => { waited = true; read(); }, PICKUP_WAIT_MS);
  read();
  return () => { clearTimeout(timer); stops.forEach((stop) => stop()); };
}

/** The first line for every surface, before any answer from Fusion. */
export function sentToCadMessage(result: WgLinkExportResponse): string {
  const send = fusionSendOf(result);
  return send
    ? fusionDeliveryWords(send).text
    : 'The model is ready. In Fusion, choose WGLink → Advanced → Insert.';
}

/** The latest send's words, wherever it was sent from. The floating status
 * line beside the File menu shows them, so they stay on screen with the menus
 * closed and the panel that sent them out of sight. */
export const useCadSendOutcome = create<{ outcome: FusionDeliveryWords | null }>(() => ({ outcome: null }));

let stopFollowing: (() => void) | null = null;
let fading: ReturnType<typeof setTimeout> | null = null;

/** Put a send's words on screen. Finished words leave by themselves. */
export function showCadSendOutcome(outcome: FusionDeliveryWords | null, finished = false): void {
  if (fading !== null) clearTimeout(fading);
  fading = outcome && finished
    ? setTimeout(() => { fading = null; useCadSendOutcome.setState({ outcome: null }); }, SEND_OUTCOME_SHOWN_MS)
    : null;
  useCadSendOutcome.setState({ outcome });
}

/** Show this send's outcome until the next send replaces it. `report` also
 * gets the words, for a caller keeping its own line. */
export function followCadSend(send: FusionSend, report?: (words: FusionDeliveryWords) => void): () => void {
  stopFollowing?.();
  const stop = watchFusionDelivery(send, (words, finished) => {
    report?.(words);
    showCadSendOutcome(words, finished);
  });
  stopFollowing = stop;
  return () => {
    stop();
    if (stopFollowing === stop) stopFollowing = null;
  };
}
