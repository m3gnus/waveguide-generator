import { describe, expect, it, vi } from 'vitest';
import { CadLinkApiError } from './cadlink';
import {
  approveCadOperationFindings,
  cancelCadOperation,
  createCadOperation,
  createSetupRevision,
  getCadOperation,
  isPendingCadOperation,
  listCadOperations,
  prepareCadOperation,
  putProjectSetup,
  putSolverSelection,
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
  it('records a setup revision and creates a manual solve operation', async () => {
    const setup = { schema_version: 1 as const, geometry: { drive_channels: [] }, options: { engine: 'auto' } };
    const setupRequest = recorder({ revisionId: 'wgs_1', contentSha256: 'sha256:s', createdAt: 'now' });
    await expect(createSetupRevision(setup, setupRequest.fetcher)).resolves.toMatchObject({ revisionId: 'wgs_1' });
    expect(setupRequest.calls[0]).toMatchObject({ url: '/api/cadlink/setup-revisions', init: { method: 'POST' } });
    expect(JSON.parse(String(setupRequest.calls[0].init?.body))).toEqual({ setup });

    const operationRequest = recorder({ operation: summary({ state: 'received' }) });
    await expect(createCadOperation({ operationId: 'manual-solve:1', ingestId: 'wgi_1' }, operationRequest.fetcher))
      .resolves.toMatchObject({ operationId: 'op-1', state: 'received' });
    expect(operationRequest.calls[0]).toMatchObject({ url: '/api/cadlink/operations', init: { method: 'POST' } });
    expect(JSON.parse(String(operationRequest.calls[0].init?.body))).toEqual({ operationId: 'manual-solve:1', ingestId: 'wgi_1' });
  });
  it('records a project setup for its source inventory', async () => {
    const { calls, fetcher } = recorder({ lineageId: 'wgl_a', inventorySha256: 'sha256:i', revisionId: 'wgs_1' });
    const setup = { schema_version: 1 as const, geometry: { drive_channels: [] }, options: { engine: 'auto' } };
    const inventory = [{ id: 'source-hf', role: 'HF', required: true }];
    const result = await putProjectSetup({ lineageId: 'wgl_a', inventory, setup }, fetcher);
    expect(result.revisionId).toBe('wgs_1');
    expect(calls[0].url).toBe('/api/cadlink/project-setups');
    expect(calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ lineageId: 'wgl_a', inventory, setup });
  });

  it('records the engine selected in WG, and nothing else', async () => {
    const { calls, fetcher } = recorder({ engine: 'bempp' });
    await putSolverSelection('bempp', fetcher);
    expect(calls[0].url).toBe('/api/cadlink/solver-selection');
    expect(calls[0].init?.method).toBe('PUT');
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ engine: 'bempp' });
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

  it('prepares with the automatic setup unless one is named, and approves on one preparation', async () => {
    const { calls, fetcher } = recorder({ operation: summary({ state: 'processing' }) });
    const prepared = await prepareCadOperation('op-1', {}, fetcher);
    expect(prepared.state).toBe('processing');
    await prepareCadOperation('op-1', {
      setupRevisionId: 'wgs_2',
      approvals: { preparationId: 'wgp_1', findingIds: ['finding-a'] },
    }, fetcher);
    expect(calls.map(({ url }) => url)).toEqual([
      '/api/cadlink/operations/op-1/prepare',
      '/api/cadlink/operations/op-1/prepare',
    ]);
    expect(calls[0].init?.method).toBe('POST');
    // No setup revision: the backend resolves the project's own setup.
    expect(JSON.parse(String(calls[0].init?.body))).toEqual({ submit: true });
    expect(JSON.parse(String(calls[1].init?.body))).toEqual({
      setupRevisionId: 'wgs_2',
      submit: true,
      approvals: { preparationId: 'wgp_1', findingIds: ['finding-a'] },
    });
  });

  it('approves findings on a named preparation and cancels an operation', async () => {
    const approvals = recorder({ ...summary(), approvals: ['sha256:r:finding-a'], preparation: null });
    await approveCadOperationFindings('op-1', { preparationId: 'wgp_1', findingIds: ['finding-a'] }, approvals.fetcher);
    expect(approvals.calls[0].url).toBe('/api/cadlink/operations/op-1/approvals');
    expect(JSON.parse(String(approvals.calls[0].init?.body))).toEqual({ preparationId: 'wgp_1', findingIds: ['finding-a'] });

    const cancel = recorder(summary({ state: 'cancelled' }));
    expect((await cancelCadOperation('op-1', cancel.fetcher)).state).toBe('cancelled');
    expect(cancel.calls[0].url).toBe('/api/cadlink/operations/op-1/cancel');
    expect(cancel.calls[0].init?.method).toBe('POST');
  });

  it('raises the server detail with its status', async () => {
    const { fetcher } = recorder({ detail: 'Unknown CAD operation op-9' }, 404);
    const failure = await cancelCadOperation('op-9', fetcher).catch((reason: unknown) => reason);
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
