import { describe, expect, it, vi } from 'vitest';
import type { CadOperationSummary } from '../api/cadOperations';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { cadJobFixture } from '../jobs/cadSolve.fixtures';
import { createCadRecoveryNoticesStore, createCadRequestActions, selectCadRequests, type CadRecoveryNotice } from './cadRecoveryNotices';

const operation = (id: string, manifest = 'elsewhere'): CadOperationSummary => ({
  operationId: id, kind: 'prepare_and_solve', state: 'needs_user_input', reason: 'setup_required',
  stage: null, message: null, jobId: id, attemptGeneration: 0, setupRevisionId: null, preparationId: null,
  snapshot: { manifestSha256: manifest, documentName: id, projectLineageId: `project:${id}` },
  legacy: false, createdAt: '2026-10-02T10:00:00Z', updatedAt: '2026-10-02T10:00:00Z',
});
const notice: CadRecoveryNotice = { id: 'mutation', kind: 'insert_link', document_id: 'fusion:active',
  document_name: 'Document', created_at: '2026-10-01T10:00:00Z', sequence: 1, active_document: false };
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { 'Content-Type': 'application/json' } });
const select = () => selectCadRequests({ jobs: [cadJobFixture(operation('earlier')), cadJobFixture(operation('current', 'screen'))],
  notices: { mutation: notice }, record: { manifest_sha256: 'screen' } as CadReturnIngestRecord });

describe('request selector and named actions with no coordinator', () => {
  it('joins mixed jobs and notices in stable order and keeps only on-screen requests loud', () => {
    const requests = select();
    expect(requests.current.map((item) => item.id)).toEqual(['current']);
    expect(requests.earlier.map((item) => item.id)).toEqual(['mutation', 'earlier']);
    expect(requests.earlier).toHaveLength(2);
    expect(requests.earlier[0].openable).toBe(false);
    expect(requests.earlier[1].openable).toBe(true);
    const active = selectCadRequests({ jobs: [], notices: { mutation: { ...notice, active_document: true } }, record: null });
    expect(active.current.map((item) => item.id)).toEqual(['mutation']);
    expect(active.earlier).toHaveLength(0);
  });
  it('never fills an authoritative empty notice listing from a stale recovery operation', () => {
    const stale = { ...operation('mutation'), kind: 'update_link', state: 'recovery_required' };
    expect(selectCadRequests({ jobs: [], notices: {}, recoveryOperations: { mutation: stale }, record: null }).earlier).toEqual([]);
    expect(selectCadRequests({ jobs: [], recoveryOperations: { mutation: stale }, record: null }).earlier).toHaveLength(1);
  });
  it('keeps operations before jobs and each parent input order for equal createdAt', () => {
    const tied = { ...notice, created_at: operation('job').createdAt!, sequence: 90 };
    const requests = selectCadRequests({ jobs: [cadJobFixture(operation('z-job')), cadJobFixture(operation('a-job'))],
      notices: { z: { ...tied, id: 'z-notice' }, a: { ...tied, id: 'a-notice', sequence: 1 } }, record: null });
    expect(requests.earlier.map((item) => item.id)).toEqual(['z-notice', 'a-notice', 'z-job', 'a-job']);
    const compatibility = selectCadRequests({ jobs: [cadJobFixture(operation('a-job'))],
      recoveryOperations: { recovery: { ...operation('z-recovery'), kind: 'update_link', state: 'recovery_required' } }, record: null });
    expect(compatibility.earlier.map((item) => item.id)).toEqual(['z-recovery', 'a-job']);
  });
  it('selects the latest child once and excludes completed jobs', () => {
    const parent = cadJobFixture(operation('parent'));
    const child = cadJobFixture(operation('child'), { parent_job_id: parent.id });
    const completed = cadJobFixture({ ...operation('completed'), state: 'accepted' });
    expect(selectCadRequests({ jobs: [parent, child, completed], record: null }).earlier.map((item) => item.id)).toEqual(['child']);
  });
  it('Dismiss and Clear all share named requests once even if job binding changes meanwhile', async () => {
    const recovery = createCadRecoveryNoticesStore();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => json({ notices: [notice] }));
    await recovery.getState().load(fetcher);
    vi.stubGlobal('fetch', fetcher);
    const dismissJob = vi.fn(async (_job: import('../api/jobsSocket').JobItem) => undefined);
    const actions = createCadRequestActions({ recovery, dismissJob, openProject: vi.fn() });
    const requests = select().earlier;
    const clear = actions.clearAll(requests);
    const single = actions.dismiss(requests[1]);
    // A fresh binding changes the selector, not the named job already pressed.
    const rebound = selectCadRequests({ jobs: [cadJobFixture(operation('new-job'))], notices: {}, record: null });
    expect(rebound.earlier.map((item) => item.id)).toEqual(['new-job']);
    expect(rebound.current).toEqual([]);
    await Promise.all([clear, single]);
    await actions.clearAll(requests);
    expect(dismissJob).toHaveBeenCalledOnce();
    expect(dismissJob.mock.calls[0][0].id).toBe('earlier');
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(fetcher.mock.calls.at(-1)?.[0]).toBe('/api/cadlink/recovery-notices/mutation/dismiss');
    expect(recovery.getState().notices).toEqual({});
    vi.unstubAllGlobals();
  });
  it('Clear all still dismisses the other named requests when one fails', async () => {
    const dismissJob = vi.fn(async () => { throw new Error('job failed'); });
    const recovery = createCadRecoveryNoticesStore();
    const dismissNotice = vi.spyOn(recovery.getState(), 'dismiss').mockResolvedValue();
    const actions = createCadRequestActions({ recovery, dismissJob, openProject: vi.fn() });
    await expect(actions.clearAll(select().earlier)).rejects.toThrow('job failed');
    expect(dismissNotice).toHaveBeenCalledWith('mutation');
  });
  it('opens only an eligible named project and holds the supplied guarded opener once', async () => {
    let resolve!: () => void;
    const openProject = vi.fn(() => new Promise<void>((yes) => { resolve = yes; }));
    const actions = createCadRequestActions({ openProject });
    const requests = select().earlier;
    await actions.open(requests[0]);
    expect(openProject).not.toHaveBeenCalled();
    const first = actions.open(requests[1]);
    const second = actions.open(requests[1]);
    expect(first).toBe(second);
    await Promise.resolve();
    expect(openProject).toHaveBeenCalledOnce();
    expect(openProject).toHaveBeenCalledWith('project:earlier');
    resolve(); await first;
  });
});
