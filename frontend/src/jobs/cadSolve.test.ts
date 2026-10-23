import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket } from '../api/jobsSocket';
import { cadJobFixture, publishCadJobs } from './cadSolve.fixtures';
import {
  acknowledgeCadSolve, approveCadJob, cadJobSummary, dismissCadJob,
  latestCadJobs, rememberCadSolve, solveCadAgain,
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

  it('keeps no press of its own: the server owns it, and only completion claims are stored', async () => {
    // The PENDING and LEGACY press caches are gone (S5); a lost response is
    // the server's to answer, by the job already in flight.
    rememberCadSolve('cad-solve:manual-solve:press', 'Speaker', 'ingest');
    expect(Object.keys(sessionStorage)).toEqual(['wg2.cad.solve.claim.v1:cad-solve:manual-solve:press']);
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
    const press = { setup_revision_id: 'displayed', frame_axis: '-y', submit: true };
    await expect(solveCadAgain('parent', press)).rejects.toThrow('response lost after binding');
    expect(await solveCadAgain('parent', press)).toEqual({ job_id: 'bound-child' });
    expect(fetcher.mock.calls[1]).toEqual(fetcher.mock.calls[0]);
  });

  it('sends only solve-again fields and returns the child id', async () => {
    const fetcher = vi.fn(async () => json({ job_id: 'child' }));
    vi.stubGlobal('fetch', fetcher);
    const press = { setup_revision_id: 'displayed', frame_axis: '-y', submit: true };
    expect(await solveCadAgain('parent/id', press)).toEqual({ job_id: 'child' });
    expect(fetcher.mock.calls[0]).toEqual(['/api/jobs/parent%2Fid/solve-again', expect.objectContaining({ body: JSON.stringify({ setup_revision_id: 'displayed', frame_axis: '-y', submit: true }) })]);
  });

  it('records approval of the exact preparation without starting a solve', async () => {
    const fetcher = vi.fn(async (url: RequestInfo | URL) => json(String(url).endsWith('/solve-again') ? { job_id: 'child' } : {}));
    vi.stubGlobal('fetch', fetcher);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    await approveCadJob(job().id, { preparation_id: 'prep', finding_ids: ['finding'] });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/jobs/refused/approvals']);
    const approvals = { preparation_id: 'prep', finding_ids: ['finding'] };
    expect(JSON.parse(String((fetcher.mock.calls[0] as unknown as [string, RequestInit])[1].body))).toEqual(approvals);
  });

  it('does not start a child when exact-preparation approval is refused', async () => {
    const fetcher = vi.fn(async () => json({ detail: 'wrong preparation' }, 422));
    vi.stubGlobal('fetch', fetcher);
    await expect(approveCadJob(job().id, { preparation_id: 'other-prep', finding_ids: ['finding'] })).rejects.toThrow('wrong preparation');
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

  it.each(['preparing', 'queued', 'running'] as const)('never dismisses or stops a %s intent', async (status) => {
    const stopped = vi.spyOn(jobsSocket, 'stopJob').mockResolvedValue();
    const fetcher = vi.fn(async () => json({}));
    vi.stubGlobal('fetch', fetcher);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue();
    await expect(dismissCadJob({ ...job(), status })).rejects.toThrow('Only a refused solve can be dismissed');
    expect(stopped).not.toHaveBeenCalled();
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('fences completion and refusal independently across reload and duplicate events', () => {
    rememberCadSolve(job().client_request_id!, 'Speaker');
    expect(acknowledgeCadSolve(job(), 'completion')?.designName).toBe('Speaker');
    expect(acknowledgeCadSolve({ ...job() }, 'completion')).toBeNull();
    expect(acknowledgeCadSolve(job(), 'refusal')?.designName).toBe('Speaker');
    expect(acknowledgeCadSolve({ ...job() }, 'refusal')).toBeNull();
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

it('does not stop a refused row that became active after the render', async () => {
  const refused = job();
  publishCadJobs([{ ...refused, status: 'running' }]);
  const stopped = vi.spyOn(jobsSocket, 'stopJob').mockResolvedValue();
  const fetcher = vi.fn(); vi.stubGlobal('fetch', fetcher);
  await expect(dismissCadJob(refused)).rejects.toThrow('Only a refused solve can be dismissed');
  expect(stopped).not.toHaveBeenCalled();
  expect(fetcher).not.toHaveBeenCalled();
  vi.restoreAllMocks(); vi.unstubAllGlobals(); publishCadJobs([]);
});
