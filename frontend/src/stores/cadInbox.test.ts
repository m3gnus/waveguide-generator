/**
 * The WG request inbox, as the page hears of it (M1 transfer contract, C4, C7).
 *
 * A refusal with no operation row arrives as a `cadInboxRefusal` message on the
 * jobs channel and lands in the store's bounded list. A reconnect also reads
 * the Sends accepted while the page was away (E3); the first connection does
 * not, so an idle page makes no such read.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobsSocketManager, type CadOperationListener, type JobsWebSocketLike } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import { connectCadOperations, resetCadOperationsStore, useCadOperationsStore } from './cadOperations';

class MockSocket implements JobsWebSocketLike {
  readyState = 1;
  onopen: JobsWebSocketLike['onopen'] = null;
  onmessage: JobsWebSocketLike['onmessage'] = null;
  onerror: JobsWebSocketLike['onerror'] = null;
  onclose: JobsWebSocketLike['onclose'] = null;
  send() {}
  close() { this.readyState = 3; this.onclose?.({}); }
  message(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const flush = async () => { for (let i = 0; i < 6; i += 1) await new Promise((resolve) => setTimeout(resolve, 0)); };

function sent(operationId: string, updatedAt: string, state = 'accepted'): CadOperationSummary {
  return {
    operationId, kind: 'receive_snapshot', state, stage: null, reason: null, message: null, jobId: null,
    attemptGeneration: 1, setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: 'sha256:m', documentName: 'Speaker', bundlePath: 'wgreturn/speaker.wgreturn' },
    legacy: false, createdAt: updatedAt, updatedAt,
  };
}

describe('the WG request inbox on the page', () => {
  let socket: MockSocket;
  let manager: JobsSocketManager;
  let disconnect: () => void;
  let clock: number;
  let finishedReads: number;

  beforeEach(() => {
    resetCadOperationsStore();
    socket = new MockSocket();
    manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    clock = Date.parse('2026-09-21T12:00:00Z');
    finishedReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('pending=false')) {
        finishedReads += 1;
        return json({ operations: [sent('send-old', '2026-09-21T11:00:00Z'), sent('send-missed', '2026-09-21T12:01:00Z')] });
      }
      return json({ operations: [] });
    }));
    disconnect = connectCadOperations(manager, () => clock);
    manager.start();
  });

  /** The listener a connection registers, from a stand-in manager. */
  function capture(): CadOperationListener {
    let held: CadOperationListener | null = null;
    const stand = { subscribeCadOperations: (listener: CadOperationListener) => { held = listener; return () => undefined; } };
    connectCadOperations(stand as unknown as JobsSocketManager, () => clock);
    return held!;
  }

  afterEach(() => {
    disconnect();
    manager.stop();
    vi.unstubAllGlobals();
    resetCadOperationsStore();
  });

  it('keeps a refusal pushed on the jobs channel', () => {
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({
      v: 1, kind: 'cadInboxRefusal',
      refusal: { operationId: 'e5f7', file: 'e5f7.json', reason: 'It does not name its kind.', at: '2026-09-21T12:00:01Z' },
    });
    const state = useCadOperationsStore.getState();
    expect(state.refusals).toEqual([{ operationId: 'e5f7', file: 'e5f7.json', reason: 'It does not name its kind.', at: '2026-09-21T12:00:01Z' }]);
    expect(state.unseenRefusals).toBe(1);
    // The same refusal again (a reconnect's merge) is not news twice.
    useCadOperationsStore.getState().mergeRefusals(state.refusals);
    expect(useCadOperationsStore.getState().unseenRefusals).toBe(1);
  });

  it('ignores a malformed refusal rather than inventing one', () => {
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'cadInboxRefusal', refusal: { file: 3 } });
    expect(useCadOperationsStore.getState().refusals).toEqual([]);
  });

  it('reads nothing finished on the first connection (the zero)', async () => {
    const listener = capture();
    listener.resync();
    await flush();
    expect(finishedReads).toBe(0);
  });

  it('recovers a Send accepted while the socket was down, and only a recent one (the positive control)', async () => {
    const listener = capture();
    listener.resync();
    await flush();
    clock = Date.parse('2026-09-21T12:05:00Z');
    listener.resync();
    await flush();
    expect(finishedReads).toBe(1);
    const operations = useCadOperationsStore.getState().operations;
    expect(operations['send-missed']?.state).toBe('accepted');
    expect(operations['send-old']).toBeUndefined();
  });
});
