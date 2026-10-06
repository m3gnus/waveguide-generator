import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket } from '../api/jobsSocket';
import { cadJobFixture, publishCadJobs } from './cadSolve.fixtures';
import {
  acknowledgeCadSolve, approveCadJob, beginCadSolve, cadJobSummary, dismissCadJob,
  latestCadJobs, pendingCadSolve, recoverLegacyCadSolves, releaseCadSolve,
  rememberCadSolve, retainCadSolvePress, solveCadAgain, submitCadSolve,
} from './cadSolve';

const job = () => cadJobFixture({
  operationId: 'manual-solve:press', kind: 'prepare_and_solve', state: 'needs_user_input',
  stage: 'ready', reason: 'findings_need_review', message: 'Review this preparation.', jobId: 'refused',
  preparationId: 'prep', setupRevisionId: 'setup', attemptGeneration: 1,
  snapshot: { manifestSha256: 'snapshot', documentName: 'Speaker' },
  legacy: false, createdAt: '2026-10-01', updatedAt: '2026-10-02',
});
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

describe('CAD job commands and window identities', () => {
  beforeEach(() => { sessionStorage.clear(); publishCadJobs([]); });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); publishCadJobs([]); });

  it('posts the CAD intent and replays its held id and immutable press after a lost response', async () => {
    const held = beginCadSolve('ingest', 'Speaker', 'Speaker1');
    held.press = { client_request_id: held.requestId, ingest_id: 'ingest', setup_revision_id: 'setup', frame_axis: '+x' };
    retainCadSolvePress('ingest', held);
    const fetcher = vi.fn().mockRejectedValueOnce(new TypeError('network lost')).mockResolvedValue(json({ job_id: 'same-job' }));
    vi.stubGlobal('fetch', fetcher);
    await expect(submitCadSolve(held.press)).rejects.toThrow('network lost');
    const reloaded = pendingCadSolve('ingest')!;
    expect(beginCadSolve('ingest', 'changed', 'changed')).toEqual(reloaded);
    expect(await submitCadSolve(reloaded.press!)).toEqual({ job_id: 'same-job' });
    expect(fetcher.mock.calls[1]).toEqual(fetcher.mock.calls[0]);
    expect(fetcher.mock.calls[0][0]).toBe('/api/jobs/cad-solve');
    releaseCadSolve('ingest', reloaded, 'same-job');
    expect(beginCadSolve('ingest', 'Speaker', 'Speaker2').requestId).not.toBe(held.requestId);
  });

  it('recovers a bound child after losing the first solve-again response', async () => {
    // The route answers an identical press with the child it made, even once
    // that child is bound (server/tests/test_job_cad_lane.py covers the route).
    let loseResponse = true;
    const fetcher = vi.fn(async () => {
      if (loseResponse) {
        loseResponse = false;
        throw new TypeError('response lost after binding');
      }
      return json({ job_id: 'bound-child' });
    });
    vi.stubGlobal('fetch', fetcher);
    const press = { client_request_id: 'held', ingest_id: 'replay-ingest', setup_revision_id: 'displayed', frame_axis: '-y', submit: true };
    const held = beginCadSolve('replay-ingest', 'Speaker', 'Speaker1');
    held.jobId = 'parent';
    held.press = { ...press, client_request_id: held.requestId };
    retainCadSolvePress('replay-ingest', held);
    await expect(solveCadAgain('parent', held.press!)).rejects.toThrow('response lost after binding');
    const reloaded = pendingCadSolve('replay-ingest')!;
    expect(reloaded.press).toEqual(held.press);
    expect(await solveCadAgain(reloaded.jobId!, reloaded.press!)).toEqual({ job_id: 'bound-child' });
    expect(fetcher.mock.calls[1]).toEqual(fetcher.mock.calls[0]);
    releaseCadSolve('replay-ingest', reloaded, 'bound-child');
    expect(pendingCadSolve('replay-ingest')).toBeNull();
  });

  it('sends only solve-again fields and returns the child id', async () => {
    const fetcher = vi.fn(async () => json({ job_id: 'child' }));
    vi.stubGlobal('fetch', fetcher);
    const press = { client_request_id: 'unused', ingest_id: 'unused', label: 'unused', setup_revision_id: 'displayed', frame_axis: '-y', submit: true };
    expect(await solveCadAgain('parent/id', press)).toEqual({ job_id: 'child' });
    expect(fetcher.mock.calls[0]).toEqual(['/api/jobs/parent%2Fid/solve-again', expect.objectContaining({ body: JSON.stringify({ setup_revision_id: 'displayed', frame_axis: '-y', submit: true }) })]);
  });

  it('approves the exact preparation then continues with those approvals, without operation calls', async () => {
    const fetcher = vi.fn(async (url: RequestInfo | URL) => json(String(url).endsWith('/solve-again') ? { job_id: 'child' } : {}));
    vi.stubGlobal('fetch', fetcher);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    await approveCadJob(job(), 'prep', ['finding']);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/jobs/refused/approvals', '/api/jobs/refused/solve-again']);
    const approvals = { preparation_id: 'prep', finding_ids: ['finding'] };
    expect(JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual(approvals);
    expect(JSON.parse(String((fetcher.mock.calls[1] as unknown as [string, RequestInit])[1].body))).toEqual({ approvals });
  });

  it('does not start a child when exact-preparation approval is refused', async () => {
    const fetcher = vi.fn(async () => json({ detail: 'wrong preparation' }, 422));
    vi.stubGlobal('fetch', fetcher);
    await expect(approveCadJob(job(), 'other-prep', ['finding'])).rejects.toThrow('wrong preparation');
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('dismisses a refused chain through one backend call so reconnect cannot re-front an ancestor', async () => {
    const parent = { ...job(), id: 'parent' };
    const child = { ...job(), parent_job_id: 'parent' };
    publishCadJobs([parent, child]);
    const fetcher = vi.fn(async () => json({ deleted: true, job_id: 'refused' }));
    vi.stubGlobal('fetch', fetcher);
    const deleted = vi.spyOn(jobsSocket, 'deleteJob').mockResolvedValue();
    const refreshed = vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    await dismissCadJob(child);
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/jobs/refused/dismiss');
    expect(init.method).toBe('POST');
    expect(deleted).not.toHaveBeenCalled();
    expect(refreshed).toHaveBeenCalled();
  });

  it('stops an active intent instead of dismissing it', async () => {
    const stopped = vi.spyOn(jobsSocket, 'stopJob').mockResolvedValue();
    const fetcher = vi.fn(async () => json({}));
    vi.stubGlobal('fetch', fetcher);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    await dismissCadJob({ ...job(), status: 'preparing' });
    expect(stopped).toHaveBeenCalledWith('refused');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('fences completion and refusal independently across reload and duplicate events', () => {
    rememberCadSolve(job().client_request_id!, 'Speaker');
    expect(acknowledgeCadSolve(job(), 'completion')?.designName).toBe('Speaker');
    expect(acknowledgeCadSolve({ ...job() }, 'completion')).toBeNull();
    expect(acknowledgeCadSolve(job(), 'refusal')?.designName).toBe('Speaker');
    expect(acknowledgeCadSolve({ ...job() }, 'refusal')).toBeNull();
  });

  it('recovers old identities only once and preserves their completion fence', async () => {
    const fetcher = vi.fn(async () => json({ ...cadJobSummary(job()), approvals: [], preparation: null }));
    vi.stubGlobal('fetch', fetcher);
    sessionStorage.setItem('wg2.cad.manual-solve.v1:ingest', JSON.stringify({ operationId: 'manual-solve:press', completionAcknowledged: true, designName: 'Speaker' }));
    await recoverLegacyCadSolves([job()]);
    expect(sessionStorage.getItem('wg2.cad.manual-solve.v1:ingest')).toBeNull();
    expect(pendingCadSolve('ingest')?.recoveredJobId).toBe('refused');
    expect(acknowledgeCadSolve(job(), 'completion')).toBeNull();
    await recoverLegacyCadSolves([job()]);
    expect(acknowledgeCadSolve(job(), 'completion')).toBeNull();
  });

  it('shows only the latest leaf when a chain has more than one continuation', () => {
    const parent = { ...job(), id: 'parent', created_at: '2026-10-01' };
    const old = { ...job(), id: 'old-child', parent_job_id: 'parent', created_at: '2026-10-02' };
    const latest = { ...job(), id: 'new-child', parent_job_id: 'parent', created_at: '2026-10-03' };
    expect(latestCadJobs([old, latest, parent]).map((item) => item.id)).toEqual(['new-child']);
  });

  it('selects the latest child even when a parent was updated later, and reads defaults and automatic axis from jobs', () => {
    const parent = { ...job(), id: 'parent', created_at: '2026-10-03' };
    const child = { ...job(), id: 'child', parent_job_id: 'parent', created_at: '2026-10-02' };
    expect(latestCadJobs([parent, child]).map((item) => item.id)).toEqual(['child']);
    child.cad_state = { ...child.cad_state!, setup_defaults: true, frame_axis_automatic: '+x' };
    expect(cadJobSummary(child)).toMatchObject({ setupDefaults: true, frameAxisAutomatic: '+x', snapshot: { manifestSha256: 'snapshot' } });
  });
});
