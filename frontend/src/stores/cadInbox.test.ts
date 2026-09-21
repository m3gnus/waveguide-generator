/**
 * The WG request inbox, as the page hears of it (M1 transfer contract, C4, C7).
 *
 * A refusal with no operation row arrives as a `cadInboxRefusal` message on the
 * jobs channel and lands in the store's bounded list. Every connection also
 * reads the Sends accepted while the page was not listening (E3): since this
 * tab's previous connection, kept across reloads, or a bounded window for a tab
 * that never connected -- WG's start-up pass takes a Send before any page is
 * connected to hear it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobsSocketManager, type CadOperationListener, type JobsWebSocketLike } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import { connectCadOperations, RECOVERY_RETRY_DELAYS_MS, resetCadOperationsStore, useCadOperationsStore } from './cadOperations';

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
    sessionStorage.clear();
    socket = new MockSocket();
    manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    clock = Date.parse('2026-09-21T12:00:00Z');
    finishedReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('pending=false')) {
        finishedReads += 1;
        return json({ operations: [
          sent('send-old', '2026-09-21T11:00:00Z'),
          sent('send-at-startup', '2026-09-21T11:58:00Z'),
          sent('send-missed', '2026-09-21T12:01:00Z'),
        ] });
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

  it('recovers, on the first connection, a Send accepted before the page connected -- within a bounded window', async () => {
    const listener = capture();
    listener.resync();
    await flush();
    expect(finishedReads).toBe(1);
    const operations = useCadOperationsStore.getState().operations;
    // Accepted two minutes ago, by the start-up pass nobody heard: recovered.
    expect(operations['send-at-startup']?.state).toBe('accepted');
    // An hour ago is outside the window: not replayed.
    expect(operations['send-old']).toBeUndefined();
  });

  it('after a reload, looks back to the previous connection of this tab, not a fixed window', async () => {
    sessionStorage.setItem('wg2.cad.sends.since.v1', String(Date.parse('2026-09-21T10:30:00Z')));
    const listener = capture();
    listener.resync();
    await flush();
    expect(useCadOperationsStore.getState().operations['send-old']?.state).toBe('accepted');
    expect(Number(sessionStorage.getItem('wg2.cad.sends.since.v1'))).toBe(clock);
  });

  it('does not move the watermark past a Send when a recovery fails, and says so (review F2)', async () => {
    const listener = capture();
    vi.stubGlobal('fetch', vi.fn(async () => json({ operations: [] })));
    listener.resync();
    await flush();
    // A Send lands at 12:01 while disconnected. Recovery at 12:05 fails.
    clock = Date.parse('2026-09-21T12:05:00Z');
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).includes('pending=false')) throw new TypeError('temporary network failure');
      return json({ operations: [] });
    }));
    listener.resync();
    await flush();
    expect(useCadOperationsStore.getState().recoveryError).toContain('temporary network failure');
    expect(Number(sessionStorage.getItem('wg2.cad.sends.since.v1'))).toBe(Date.parse('2026-09-21T12:00:00Z'));
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => json({
      operations: String(input).includes('pending=false') ? [sent('send-missed', '2026-09-21T12:01:00Z')] : [],
    })));
    clock = Date.parse('2026-09-21T12:10:00Z');
    listener.resync();
    await flush();
    expect(useCadOperationsStore.getState().operations['send-missed']?.state).toBe('accepted');
    expect(useCadOperationsStore.getState().recoveryError).toBeNull();
  });

  it('retries a failed recovery by itself, bounded', async () => {
    vi.useFakeTimers();
    try {
      const listener = capture();
      let attempts = 0;
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
        if (String(input).includes('pending=false')) {
          attempts += 1;
          if (attempts === 1) throw new TypeError('down');
          return json({ operations: [sent('send-missed', '2026-09-21T11:59:00Z')] });
        }
        return json({ operations: [] });
      }));
      listener.resync();
      await vi.advanceTimersByTimeAsync(10);
      expect(attempts).toBe(1);
      await vi.advanceTimersByTimeAsync(RECOVERY_RETRY_DELAYS_MS[0] + 10);
      expect(attempts).toBe(2);
      expect(useCadOperationsStore.getState().operations['send-missed']?.state).toBe('accepted');
      // Succeeded: no further retries.
      await vi.advanceTimersByTimeAsync(10 * 60_000);
      expect(attempts).toBe(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it('lets only the newest of two overlapping recoveries move the watermark', async () => {
    const listener = capture();
    const answers: Array<(value: Response) => void> = [];
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => (
      String(input).includes('pending=false')
        ? new Promise<Response>((resolve) => { answers.push(resolve); })
        : Promise.resolve(json({ operations: [] }))
    )));
    clock = Date.parse('2026-09-21T12:05:00Z');
    listener.resync();
    clock = Date.parse('2026-09-21T12:10:00Z');
    listener.resync();
    await flush();
    // The newer answers first; the older, late one must not move the boundary back.
    answers[1](json({ operations: [] }));
    await flush();
    answers[0](json({ operations: [] }));
    await flush();
    expect(Number(sessionStorage.getItem('wg2.cad.sends.since.v1'))).toBe(Date.parse('2026-09-21T12:10:00Z'));
  });

  it('recovers only what this page awaited and recent Sends, refused ones included (review F1)', async () => {
    const { recoverMissedSnapshots } = await import('./cadOperations');
    useCadOperationsStore.getState().apply({ ...sent('op-mine', '2026-09-21T12:00:00Z', 'processing'), kind: 'prepare_and_solve' });
    const api = (async () => json({ operations: [
      { ...sent('op-mine', '2026-09-21T12:02:00Z', 'accepted'), kind: 'prepare_and_solve', jobId: 'job-1' },
      { ...sent('op-unrelated', '2026-09-21T12:02:00Z', 'accepted'), kind: 'prepare_and_solve', jobId: 'job-2' },
      sent('send-refused', '2026-09-21T12:03:00Z', 'rejected'),
    ] })) as typeof fetch;
    await recoverMissedSnapshots(Date.parse('2026-09-21T12:00:00Z'), api);
    const operations = useCadOperationsStore.getState().operations;
    expect(operations['op-mine']?.state).toBe('accepted');
    expect(operations['op-unrelated']).toBeUndefined();
    expect(operations['send-refused']?.state).toBe('rejected');
  });

  it('a late success of an older recovery does not clear the failure of a newer one', async () => {
    const listener = capture();
    const answers: Array<{ resolve: (value: Response) => void; reject: (reason: unknown) => void }> = [];
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => (
      String(input).includes('pending=false')
        ? new Promise<Response>((resolve, reject) => { answers.push({ resolve, reject }); })
        : Promise.resolve(json({ operations: [] }))
    )));
    listener.resync();
    listener.resync();
    await flush();
    answers[1].reject(new TypeError('newer failed'));
    await flush();
    answers[0].resolve(json({ operations: [] }));
    await flush();
    expect(useCadOperationsStore.getState().recoveryError).toContain('newer failed');
  });

  it('keeps a pushed delivery status for the panel', () => {
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({
      v: 1, kind: 'cadDeliveryStatus',
      status: { consumer: 'running', declined: null, passStartedAt: 'x', lastPassCompletedAt: 'y', passHung: true, recentRefusals: [] },
    });
    expect(useCadOperationsStore.getState().deliveryStatus?.passHung).toBe(true);
  });

  it('recovers a Send accepted while the socket was down, and only a recent one (the positive control)', async () => {
    const listener = capture();
    listener.resync();
    await flush();
    resetCadOperationsStore();
    clock = Date.parse('2026-09-21T12:05:00Z');
    listener.resync();
    await flush();
    expect(finishedReads).toBe(2);
    const operations = useCadOperationsStore.getState().operations;
    expect(operations['send-missed']?.state).toBe('accepted');
    // Since the previous connection (12:00), not the first-connection window.
    expect(operations['send-at-startup']).toBeUndefined();
    expect(operations['send-old']).toBeUndefined();
  });
});
