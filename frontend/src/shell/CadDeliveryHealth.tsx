import { useEffect, useRef } from 'react';
import type { CadDeliveryStatus } from '../api/cadOperations';
import { connectCadDeliveryHealth, refreshCadDeliveryHealth, useCadDeliveryHealthStore } from '../stores/cadDeliveryHealth';
import { usePanelVisible } from './panelVisibility';

export { WAITING_READ_MS } from '../stores/cadDeliveryHealth';

/** A pass that started this long ago and has not finished is reported as stuck. */
export const HUNG_PASS_MS = 15_000;

export interface DeliveryHealthLine {
  tone: 'blocked' | 'waiting';
  text: string;
}

function clock(iso: string): string {
  const at = new Date(iso);
  return Number.isNaN(at.getTime()) ? iso : at.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

/**
 * Why a request from Fusion is not moving, in words (M1 transfer contract, C4).
 *
 * A consumer that is off, one that declines (no WGLink folder, an approved
 * update restart) and one stuck inside a pass all look idle from outside. The
 * server records which (server/cadlink/joblane/delivery_status.py); this says it.
 * An idle, running consumer says nothing.
 */
export function deliveryHealthLines(status: CadDeliveryStatus | null, now: number): DeliveryHealthLine[] {
  if (!status) return [];
  if (status.consumer === 'disabled') {
    return [{
      tone: 'blocked',
      text: `WG is not collecting CAD Link requests (${status.variable ?? 'WG2_CAD_DELIVERY'}=0). A Send or Solve from Fusion is refused there, and nothing here runs one.`,
    }];
  }
  if (status.consumer !== 'running') {
    return [{ tone: 'blocked', text: 'WG’s CAD Link request consumer is not running, so nothing sent from Fusion is taken.' }];
  }
  const lines: DeliveryHealthLine[] = [];
  if (status.declined) lines.push({ tone: 'waiting', text: `Requests from Fusion are waiting: ${status.declined}` });
  const started = status.passStartedAt ? Date.parse(status.passStartedAt) : Number.NaN;
  const finished = status.lastPassCompletedAt ? Date.parse(status.lastPassCompletedAt) : Number.NaN;
  const unfinished = Number.isFinite(started) && (!Number.isFinite(finished) || started > finished);
  if (status.passHung || (unfinished && now - started > HUNG_PASS_MS)) {
    lines.push({
      tone: 'blocked',
      text: `WG’s request consumer has not finished a pass since ${clock(status.passStartedAt!)}; requests sent since then have not been taken.`,
    });
  }
  return lines;
}

/** Mount once beside the other shell services, independently of the panel or coordinator. */
export function CadDeliveryHealthDataSource() {
  useEffect(() => connectCadDeliveryHealth(), []);
  return null;
}

/** The consumer's state and recent refusals, in the CAD Link panel. */
export function CadDeliveryHealth({ now = Date.now }: { now?: () => number }) {
  const status = useCadDeliveryHealthStore((state) => state.status);
  const refusals = useCadDeliveryHealthStore((state) => state.refusals);
  const unseen = useCadDeliveryHealthStore((state) => state.unseenRefusals);
  const visible = usePanelVisible();
  const wasVisible = useRef(false);
  useEffect(() => {
    if (visible && !wasVisible.current) refreshCadDeliveryHealth();
    wasVisible.current = visible;
  }, [visible]);

  // Seeing the list is what acknowledges it.
  useEffect(() => {
    if (visible && unseen) useCadDeliveryHealthStore.getState().acknowledgeRefusals();
  }, [unseen, visible]);

  const recoveryError = useCadDeliveryHealthStore((state) => state.recoveryError);
  const lines = [
    ...deliveryHealthLines(status, now()),
    ...(recoveryError ? [{ tone: 'waiting' as const, text: recoveryError }] : []),
  ];
  if (!lines.length && !refusals.length) return null;
  return <div className="cad-delivery-health" role="status">
    {lines.map((line) => <p key={line.text} className={`cad-delivery-${line.tone}`}>{line.text}</p>)}
    {refusals.length > 0 && <details className="cad-delivery-refusals" open={unseen > 0}>
      <summary>{refusals.length === 1 ? 'A request from Fusion was refused' : `${refusals.length} requests from Fusion were refused`}</summary>
      <ul>
        {refusals.slice(0, 5).map((refusal) => <li key={`${refusal.at}|${refusal.file}`}>
          <b>{clock(refusal.at)}</b> {refusal.reason}
          {refusal.operationId && <> <code>{refusal.operationId}</code></>}
        </li>)}
      </ul>
    </details>}
  </div>;
}
