import { describe, expect, it, vi } from 'vitest';
import { CadLinkApiError } from './cadlink';
import {
  createSetupRevision,
  getCadOperation,
  getSetupRevision,
  isPendingCadOperation,
  listCadOperations,
  putProjectSetup,
  putSolverSelection,
  reconcileCadOperation,
  type CadOperationSummary,
} from './cadOperations';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

function summary(overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId: 'op-1',
    kind: 'prepare_and_solve',
    state: 'needs_user_input',
    stage: 'ready',
    reason: 'findings_need_review',
    message: 'Review the blocking findings.',
    jobId: null,
    attemptGeneration: 2,
    setupRevisionId: 'wgs_1',
    preparationId: 'wgp_1',
    snapshot: { manifestSha256: `sha256:${'a'.repeat(64)}` },
    legacy: false,
    createdAt: '2026-09-14T10:00:00Z',
    updatedAt: '2026-09-14T10:00:05Z',
    ...overrides,
  };
}

/** A fetch that records every call and answers with `body`. */
function recorder(body: unknown, status = 200) {
  const calls: Array<{ url: string; init?: RequestInit }> = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init });
    return json(body, status);
  }) as unknown as typeof fetch;
  return { calls, fetcher };
}

describe('CAD operations client', () => {
  it('records a setup revision', async () => {
    const setup = { schema_version: 1 as const, geometry: { drive_channels: [] }, options: { engine: 'auto' } };
    const setupRequest = recorder({ revisionId: 'wgs_1', contentSha256: 'sha256:s', createdAt: 'now' });
    await expect(createSetupRevision(setup, setupRequest.fetcher)).resolves.toMatchObject({ revisionId: 'wgs_1' });
    expect(setupRequest.calls[0]).toMatchObject({ url: '/api/cadlink/setup-revisions', init: { method: 'POST' } });
    expect(JSON.parse(String(setupRequest.calls[0].init?.body))).toEqual({ setup });
  });

  it('reads the immutable content of a setup revision', async () => {
    const setup = { schema_version: 1 as const, geometry: { drive_channels: [] }, options: { engine: 'beat-cpu' } };
    const request = recorder({ revisionId: 'wgs/1', contentSha256: 'sha256:s', createdAt: 'now', setup });

    await expect(getSetupRevision('wgs/1', request.fetcher)).resolves.toMatchObject({
      revisionId: 'wgs/1', setup: { options: { engine: 'beat-cpu' } },
    });
    expect(request.calls[0].url).toBe('/api/cadlink/setup-revisions/wgs%2F1');
  });
  it('records a project setup for its source inventory', async () => {
    const { calls, fetcher } = recorder({ revision_id: 'wgs_1', content_sha256: 'sha256:c', inventory_sha256: 'sha256:i' });
    const setup = { schema_version: 1 as const, geometry: { drive_channels: [] }, options: { engine: 'auto' } };
    const inventory = [{ id: 'source-hf', role: 'HF', required: true }];
    const result = await putProjectSetup({ lineageId: 'wgl_a', inventory, setup }, fetcher);
    expect(result).toEqual({ lineageId: 'wgl_a', inventorySha256: 'sha256:i', revisionId: 'wgs_1' });
    // The project is an imported model; its sources key the setup.
    expect(calls[0].url).toBe('/api/models/wgl_a/setup');
    expect(calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ inventory, setup });
  });

  it('records the shared solve selection', async () => {
    const { calls, fetcher } = recorder({ engine: 'bempp' });
    await putSolverSelection({ engine: 'bempp', accuracy: 'accurate' }, fetcher);
    expect(calls[0].url).toBe('/api/cadlink/solver-selection');
    expect(calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ engine: 'bempp', accuracy: 'accurate' });
  });

  it('lists the unfinished operations by default, and every operation on request', async () => {
    const { calls, fetcher } = recorder({ operations: [summary()] });
    expect(await listCadOperations({}, fetcher)).toEqual([summary()]);
    await listCadOperations({ pending: false }, fetcher);
    expect(calls.map(({ url }) => url)).toEqual([
      '/api/cadlink/operations',
      '/api/cadlink/operations?pending=false',
    ]);
  });

  it('reads one operation with its preparation', async () => {
    const detail = {
      ...summary(),
      approvals: [],
      preparation: {
        preparationId: 'wgp_1', ingestId: 'wgi_1', snapshotSha256: 'sha256:s', setupRevisionId: 'wgs_1',
        reportSha256: 'sha256:r', blockingFindingIds: ['finding-a'], attemptGeneration: 2,
      },
    };
    const { calls, fetcher } = recorder(detail);
    const result = await getCadOperation('op/1', fetcher);
    expect(calls[0].url).toBe('/api/cadlink/operations/op%2F1');
    expect(result.preparation?.blockingFindingIds).toEqual(['finding-a']);
  });

  it('asks the backend to reconcile a Fusion operation from current evidence', async () => {
    const request = recorder({ operation: summary({ kind: 'update_link', state: 'accepted' }) });
    await expect(reconcileCadOperation('op/1', request.fetcher)).resolves.toMatchObject({
      operationId: 'op-1', state: 'accepted',
    });
    expect(request.calls[0]).toMatchObject({
      url: '/api/cadlink/operations/op%2F1/reconcile', init: { method: 'POST' },
    });
  });

  it('raises the server detail with its status', async () => {
    const { fetcher } = recorder({ detail: 'Unknown CAD operation op-9' }, 404);
    const failure = await getCadOperation('op-9', fetcher).catch((reason: unknown) => reason);
    expect(failure).toBeInstanceOf(CadLinkApiError);
    expect((failure as CadLinkApiError).message).toBe('Unknown CAD operation op-9');
    expect((failure as CadLinkApiError).status).toBe(404);
  });

  it('calls every state that is not terminal pending', () => {
    for (const state of ['received', 'processing', 'needs_user_input', 'recovery_required', 'cancel_requested']) {
      expect(isPendingCadOperation(summary({ state }))).toBe(true);
    }
    for (const state of ['accepted', 'rejected', 'cancelled']) {
      expect(isPendingCadOperation(summary({ state }))).toBe(false);
    }
  });
});
