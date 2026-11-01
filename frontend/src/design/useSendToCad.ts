import { useEffect, useState } from 'react';
import type { CadOperationSummary } from '../api/cadOperations';
import type { WgLinkExportResponse } from '../api/designIo';
import { failureWords } from '../api/httpError';
import { useCadConnectionStore } from '../stores/cadConnection';
import { useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';

/** How long Fusion normally needs; past this the wait is worth explaining. */
export const PICKUP_WAIT_MS = 10_000;

export interface FusionDeliveryWords { tone: 'status' | 'error'; text: string }

export interface FusionDeliveryFacts {
  operation?: Pick<CadOperationSummary, 'kind' | 'fusionDelivery' | 'message'> | null;
  /** Until the operation arrives: this send updates a model already in Fusion. */
  update?: boolean;
  fusion?: { processRunning?: boolean; wgRequestPickup?: number | null } | null;
  /** WG's own remedy for an add-in it knows cannot take the request. */
  waitingRemedy?: string | null;
  waited?: boolean;
}

/** One wording for every Send to CAD surface: what happened to the user's
 * model, and the next step when it needs one (docs/WORDING.md). */
export function fusionDeliveryWords(facts: FusionDeliveryFacts): FusionDeliveryWords | null {
  const { operation, fusion, waitingRemedy, waited } = facts;
  const update = operation ? operation.kind === 'update_link' : Boolean(facts.update);
  const verb = update ? 'update' : 'open';
  switch (operation?.fusionDelivery ?? 'waiting') {
    case 'claimed': return { tone: 'status', text: `Fusion is ${update ? 'updating' : 'opening'} this model.` };
    case 'applied': return { tone: 'status', text: update ? 'Updated in Fusion.' : 'Opened in Fusion.' };
    case 'refused': return { tone: 'error', text: failureWords(operation?.message ?? null,
      `Fusion could not ${verb} this model. Send it again from WG.`) };
    case 'expired': return { tone: 'error', text: `Fusion did not ${verb} this model in time. Open Fusion, then send it again.` };
    case 'uncertain': return { tone: 'error', text: 'WG cannot tell whether Fusion finished this model. Check it in Fusion before you send it again.' };
    case 'cancelled': return null;
    default:
  }
  if (waitingRemedy) return { tone: 'status', text: failureWords(waitingRemedy, 'Restart Fusion to update WGLink, then send the model again.') };
  if (fusion?.processRunning === false) return { tone: 'status', text: 'Fusion is closed. Open Fusion and this model will arrive.' };
  if (!waited) return { tone: 'status', text: `Waiting for Fusion to ${verb} this model.` };
  if (fusion?.processRunning && !fusion.wgRequestPickup) {
    return { tone: 'status', text: 'WGLink in Fusion cannot take this model. Restart Fusion to update WGLink, then send the model again.' };
  }
  return { tone: 'status', text: `Fusion has not started on this model. Finish any open command in Fusion and it will ${verb}.` };
}

export interface FusionSend { requestId: string; update?: boolean; waitingRemedy?: string | null }

export function fusionSendOf(result: WgLinkExportResponse, update = false): FusionSend | null {
  return result.cadHandoffRequestId
    ? { requestId: result.cadHandoffRequestId, update, waitingRemedy: result.cadHandoffWaiting ?? null } : null;
}

/** Follow one send until the caller stops. Reports each change of wording. */
export function watchFusionDelivery(send: FusionSend, report: (words: FusionDeliveryWords) => void): () => void {
  let waited = false;
  let last: string | null = null;
  const read = () => {
    const operation = useCadDeliveryHealthStore.getState().outboundOperations[send.requestId];
    const words = fusionDeliveryWords({ ...send, operation, waited, fusion: useCadConnectionStore.getState().fusionStatus });
    if (!words || words.text === last) return;
    last = words.text;
    report(words);
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
    ? fusionDeliveryWords(send)?.text ?? ''
    : 'The model is ready. In Fusion, choose WGLink → Advanced → Insert.';
}

/** A surface's live line for its latest send; null before one. */
export function useFusionDelivery(send: FusionSend | null): FusionDeliveryWords | null {
  const [words, setWords] = useState<FusionDeliveryWords | null>(null);
  useEffect(() => {
    setWords(null);
    return send ? watchFusionDelivery(send, setWords) : undefined;
  }, [send]);
  return words;
}
