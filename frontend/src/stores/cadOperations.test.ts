import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationListener } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import { connectCadOperations, pendingCadOperations, RECOVERY_RETRY_DELAYS_MS, resetCadOperationsStore, useCadOperationsStore } from './cadOperations';

function summary(overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId: 'op-1',
    kind: 'prepare_and_solve',
    state: 'received',
    stage: 'received',
    reason: null,
    message: null,
    jobId: null,
    attemptGeneration: 0,
    setupRevisionId: null,
    preparationId: null,
    snapshot: { manifestSha256: `sha256:${'a'.repeat(64)}` },
    legacy: false,
    createdAt: '2026-09-14T10:00:00Z',
    updatedAt: '2026-09-14T10:00:00Z',
    ...overrides,
  };
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

describe('CAD operations store', () => {
  afterEach(() => resetCadOperationsStore());

  it('keeps the newer of two updates to one operation, by attempt and then by time', () => {
    const { apply } = useCadOperationsStore.getState();
    expect(apply(summary({ state: 'processing', stage: 'preparing-mesh', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:02Z' }))).toBe(true);
    // A late message from the same attempt, written earlier, changes nothing.
    expect(apply(summary({ state: 'processing', stage: 'validating', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:01Z' }))).toBe(false);
    // An obsolete attempt never overwrites a newer one, whatever its clock says.
    expect(apply(summary({ state: 'needs_user_input', reason: 'interrupted', attemptGeneration: 0, updatedAt: '2026-09-14T10:00:09Z' }))).toBe(false);
    expect(useCadOperationsStore.getState().operations['op-1'].stage).toBe('preparing-mesh');
    expect(apply(summary({ state: 'accepted', stage: 'submitted', jobId: 'job-1', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:03Z' }))).toBe(true);
    expect(useCadOperationsStore.getState().operations['op-1']).toMatchObject({ state: 'accepted', jobId: 'job-1' });
    expect(pendingCadOperations(useCadOperationsStore.getState().operations)).toEqual([]);
  });

  it('loads the unfinished operations and forgets pending ones the server no longer lists', async () => {
    const { apply, load } = useCadOperationsStore.getState();
    apply(summary({ operationId: 'op-gone', state: 'needs_user_input' }));
    const fetcher = vi.fn(async () => json({ operations: [summary({ operationId: 'op-2', state: 'needs_user_input', reason: 'setup_required' })] }));
    await load(fetcher as unknown as typeof fetch);
    expect(fetcher).toHaveBeenCalledWith('/api/cadlink/operations', undefined);
    const operations = useCadOperationsStore.getState().operations;
    expect(Object.keys(operations)).toEqual(['op-2']);
    expect(pendingCadOperations(operations).map((operation) => operation.operationId)).toEqual(['op-2']);
  });

  it('restores a recovery-required Fusion update from the durable listing after reload', async () => {
    const recovery = summary({ operationId: 'update-7', kind: 'update_link', state: 'recovery_required' });
    await useCadOperationsStore.getState().load(
      vi.fn(async () => json({ operations: [recovery] })) as unknown as typeof fetch,
    );
    expect(pendingCadOperations(useCadOperationsStore.getState().operations)).toEqual([recovery]);
  });

  it('keeps an update that arrives while a load is in flight', async () => {
    const { apply, load } = useCadOperationsStore.getState();
    let answer!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    const loading = load(fetcher as unknown as typeof fetch);
    // Accepted after the listing was taken: absent from it, and still real.
    apply(summary({ operationId: 'op-new' }));
    // Newer than the copy the listing is about to hand back.
    apply(summary({ operationId: 'op-2', state: 'processing', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:05Z' }));
    answer(json({ operations: [summary({ operationId: 'op-2', state: 'received', attemptGeneration: 0 })] }));
    await loading;
    const operations = useCadOperationsStore.getState().operations;
    expect(Object.keys(operations).sort()).toEqual(['op-2', 'op-new']);
    expect(operations['op-2'].state).toBe('processing');
  });

  it('keeps an update that finished an operation over a listing taken before it, and the held copy on a tie', async () => {
    const { apply, load } = useCadOperationsStore.getState();
    let answer!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { answer = resolve; }));
    const loading = load(fetcher as unknown as typeof fetch);
    // Both reported on the jobs channel while the listing was in flight.
    apply(summary({ operationId: 'op-1', state: 'accepted', stage: 'submitted', jobId: 'job-1', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:05Z' }));
    apply(summary({ operationId: 'op-2', state: 'processing', stage: 'validating', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:05Z' }));
    answer(json({ operations: [
      // Taken before the job existed; a clock a little ahead changes nothing.
      summary({ operationId: 'op-1', state: 'processing', stage: 'preparing-mesh', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:07Z' }),
      summary({ operationId: 'op-2', state: 'processing', stage: 'received', attemptGeneration: 1, updatedAt: '2026-09-14T10:00:05Z' }),
    ] }));
    await loading;
    const operations = useCadOperationsStore.getState().operations;
    expect(operations['op-1']).toMatchObject({ state: 'accepted', stage: 'submitted', jobId: 'job-1' });
    expect(operations['op-2'].stage).toBe('validating');
  });

  it('preserves an exact awaited solve across a failed recovery and pending-list removal', async () => {
    vi.useFakeTimers();
    let listener: CadOperationListener | null = null;
    const manager = {
      subscribeCadOperations: (next: CadOperationListener) => { listener = next; return () => undefined; },
    };
    const solve = summary({ operationId: 'awaited-solve', state: 'processing', attemptGeneration: 1 });
    useCadOperationsStore.getState().apply(solve);
    useCadOperationsStore.getState().apply(summary({ operationId: 'other-solve', state: 'processing' }));
    let exactReads = 0;
    let otherReads = 0;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/operations/awaited-solve')) {
        exactReads += 1;
        if (exactReads === 1) throw new TypeError('temporary failure');
        return json({ ...solve, state: 'accepted', stage: 'submitted', jobId: 'new-job' });
      }
      if (path.endsWith('/operations/other-solve')) {
        otherReads += 1;
        return json(summary({ operationId: 'other-solve', state: 'accepted', stage: 'submitted', jobId: 'other-job' }));
      }
      // The bounded history page is deliberately empty: the awaited row may
      // be older than its newest 50 operations. The pending list also omits it.
      return json({ operations: [] });
    }));
    const disconnect = connectCadOperations(manager as never, () => Date.parse('2026-09-21T12:00:00Z'));
    try {
      listener!.resync();
      await vi.advanceTimersByTimeAsync(10);
      expect(useCadOperationsStore.getState().operations['awaited-solve']).toBeUndefined();
      expect(useCadOperationsStore.getState().operations['other-solve']).toMatchObject({
        state: 'accepted', jobId: 'other-job',
      });
      await vi.advanceTimersByTimeAsync(RECOVERY_RETRY_DELAYS_MS[0] + 10);
      expect(exactReads).toBe(2);
      expect(otherReads).toBe(1);
      expect(useCadOperationsStore.getState().operations['awaited-solve']).toMatchObject({
        state: 'accepted', jobId: 'new-job',
      });
      expect(useCadOperationsStore.getState().recoveryError).toBeNull();
    } finally {
      disconnect();
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('recovers each awaited operation independently and resolves a missing request once', async () => {
    vi.useFakeTimers();
    let listener: CadOperationListener | null = null;
    const manager = {
      subscribeCadOperations: (next: CadOperationListener) => { listener = next; return () => undefined; },
    };
    useCadOperationsStore.getState().apply(summary({ operationId: 'missing', state: 'processing' }));
    useCadOperationsStore.getState().apply(summary({ operationId: 'good', state: 'processing' }));
    const exactReads: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.endsWith('/operations/missing')) {
        exactReads.push('missing');
        return new Response(JSON.stringify({ detail: 'Unknown CAD operation' }), {
          status: 404, headers: { 'Content-Type': 'application/json' },
        });
      }
      if (path.endsWith('/operations/good')) {
        exactReads.push('good');
        return json(summary({ operationId: 'good', state: 'accepted', stage: 'submitted', jobId: 'new-job' }));
      }
      if (path.includes('pending=false')) {
        return json({ operations: [summary({
          operationId: 'recent-send', kind: 'receive_snapshot', state: 'accepted',
          updatedAt: '2026-09-21T11:59:59Z',
        })] });
      }
      return json({ operations: [] });
    }));
    const disconnect = connectCadOperations(manager as never, () => Date.parse('2026-09-21T12:00:00Z'));
    try {
      listener!.resync();
      await vi.advanceTimersByTimeAsync(10);
      expect(useCadOperationsStore.getState().operations.good).toMatchObject({ state: 'accepted', jobId: 'new-job' });
      expect(useCadOperationsStore.getState().operations.missing).toMatchObject({
        state: 'rejected', reason: 'request_unknown',
      });
      expect(useCadOperationsStore.getState().operations.missing.message).toContain('no longer known to WG');
      expect(useCadOperationsStore.getState().operations['recent-send']).toMatchObject({
        kind: 'receive_snapshot', state: 'accepted',
      });
      expect(exactReads).toEqual(expect.arrayContaining(['missing', 'good']));

      listener!.resync();
      await vi.advanceTimersByTimeAsync(10);
      expect(exactReads.filter((id) => id === 'missing')).toHaveLength(1);
      expect(exactReads.filter((id) => id === 'good')).toHaveLength(1);
    } finally {
      disconnect();
      vi.unstubAllGlobals();
      vi.useRealTimers();
    }
  });

  it('caps awaited recovery reads at eight concurrent requests', async () => {
    let listener: CadOperationListener | null = null;
    const manager = {
      subscribeCadOperations: (next: CadOperationListener) => { listener = next; return () => undefined; },
    };
    for (let index = 0; index < 18; index += 1) {
      useCadOperationsStore.getState().apply(summary({ operationId: `awaited-${index}`, state: 'processing' }));
    }
    let active = 0;
    let maximum = 0;
    const releases: Array<() => void> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (!path.includes('/operations/awaited-')) return json({ operations: [] });
      active += 1;
      maximum = Math.max(maximum, active);
      await new Promise<void>((resolve) => { releases.push(resolve); });
      active -= 1;
      const operationId = path.slice(path.lastIndexOf('/') + 1);
      return json(summary({ operationId, state: 'accepted', stage: 'submitted', jobId: `job-${operationId}` }));
    }));
    const disconnect = connectCadOperations(manager as never);
    try {
      listener!.resync();
      await vi.waitFor(() => expect(releases).toHaveLength(8));
      releases.splice(0).forEach((release) => release());
      await vi.waitFor(() => expect(releases).toHaveLength(8));
      releases.splice(0).forEach((release) => release());
      await vi.waitFor(() => expect(releases).toHaveLength(2));
      releases.splice(0).forEach((release) => release());
      await vi.waitFor(() => expect(useCadOperationsStore.getState().operations['awaited-17'].state).toBe('accepted'));
      expect(maximum).toBe(8);
    } finally {
      disconnect();
      vi.unstubAllGlobals();
    }
  });
});
