import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CadDeliveryStatus, CadOperationSummary } from '../api/cadOperations';
import { jobsSocket, type CadOperationListener } from '../api/jobsSocket';
import { connectCadDeliveryHealth, refreshCadDeliveryHealth, resetCadDeliveryHealthStore, useCadDeliveryHealthStore, WAITING_READ_MS } from './cadDeliveryHealth';

const status = (declined: string | null): CadDeliveryStatus => ({
  consumer: 'running', declined, passStartedAt: null, lastPassCompletedAt: null,
  recentRefusals: [], variable: 'WG2_CAD_DELIVERY',
});
const flush = async () => { await vi.advanceTimersByTimeAsync(0); };

describe('delivery-health reads in flight', () => {
  let listener: CadOperationListener;
  let answers: Array<(response: Response) => void>;
  let disconnect: () => void;

  beforeEach(() => {
    vi.useFakeTimers();
    resetCadDeliveryHealthStore();
    answers = [];
    vi.spyOn(jobsSocket, 'subscribeCadOperations').mockImplementation((next) => {
      listener = next;
      return () => undefined;
    });
    vi.stubGlobal('fetch', vi.fn((input: RequestInfo | URL) => String(input).endsWith('/delivery')
      ? new Promise<Response>((resolve) => { answers.push(resolve); })
      : Promise.resolve(new Response(JSON.stringify({ operations: [] })))));
    disconnect = connectCadDeliveryHealth();
  });
  afterEach(() => {
    disconnect();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.useRealTimers();
    resetCadDeliveryHealthStore();
  });
  const respond = (index: number, value: CadDeliveryStatus) => answers[index](new Response(JSON.stringify(value)));

  it('coalesces requests into one follow-up read when waiting ends during a read', async () => {
    await flush();
    const waiting = { operationId: 'waiting', state: 'received', attemptGeneration: 0, updatedAt: null } as CadOperationSummary;
    listener.operation(waiting);
    await vi.advanceTimersByTimeAsync(3 * WAITING_READ_MS);
    listener.operation({ ...waiting, state: 'processing', attemptGeneration: 1 });
    expect(useCadDeliveryHealthStore.getState().waiting).toBe(false);
    expect(answers).toHaveLength(1);
    respond(0, status('Old waiting snapshot.'));
    await flush();
    expect(answers).toHaveLength(2);
    respond(1, status(null));
    await flush();
    expect(useCadDeliveryHealthStore.getState().status?.declined).toBeNull();
    await vi.advanceTimersByTimeAsync(3 * WAITING_READ_MS);
    expect(answers).toHaveLength(2);
  });

  it('runs the queued read after a failed read and fences a newer push', async () => {
    refreshCadDeliveryHealth();
    refreshCadDeliveryHealth();
    answers[0](new Response('{}', { status: 503 }));
    await flush();
    expect(answers).toHaveLength(2);
    listener.deliveryStatus!(status('New push.'));
    respond(1, status('Obsolete snapshot.'));
    await flush();
    expect(useCadDeliveryHealthStore.getState().status?.declined).toBe('New push.');
    expect(answers).toHaveLength(2);
  });

  it('does not start a queued read or apply a late snapshot after disconnect', async () => {
    refreshCadDeliveryHealth();
    disconnect();
    respond(0, status('Late snapshot.'));
    await flush();
    expect(answers).toHaveLength(1);
    expect(useCadDeliveryHealthStore.getState().status).toBeNull();
  });
});
