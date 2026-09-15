import { afterEach, describe, expect, it, vi } from 'vitest';
import type { CadOperationSummary } from '../api/cadOperations';
import { pendingCadOperations, resetCadOperationsStore, useCadOperationsStore } from './cadOperations';

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
});
