import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobsSocketManager, type JobItem, type JobsWebSocketLike } from '../api/jobsSocket';
import { compareSelection } from '../api/results';

class MockSocket implements JobsWebSocketLike {
  readyState = 1;
  onopen: JobsWebSocketLike['onopen'] = null;
  onmessage: JobsWebSocketLike['onmessage'] = null;
  onerror: JobsWebSocketLike['onerror'] = null;
  onclose: JobsWebSocketLike['onclose'] = null;
  sent: string[] = [];
  send(data: string) { this.sent.push(data); }
  close() { this.readyState = 3; this.onclose?.({}); }
  message(value: unknown) { this.onmessage?.({ data: JSON.stringify(value) }); }
}

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    id: 'job-1', status: 'queued', progress: 0, stage: 'queued', stage_message: 'Queued',
    created_at: '2026-08-03T10:00:00Z', queued_at: '2026-08-03T10:00:00Z', started_at: null, completed_at: null,
    config_summary: { formula_type: 'OSSE', engine: 'dryrun' }, has_results: false, has_mesh_artifact: false,
    label: null, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
    rating: null, exported_files: [], auto_export_completed_at: null, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

async function flush(): Promise<void> { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }

describe('jobs websocket state machine', () => {
  beforeEach(() => { vi.useFakeTimers(); compareSelection.clear(); });
  afterEach(() => vi.useRealTimers());

  it('accepts snapshot then contiguous events and tracks live job fields', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).includes('/api/status/')
      ? json(job({ status: 'running', progress: .42, stage: 'solve', stage_message: 'Solving' }))
      : json({ items: [job()] }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job()] });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'progress', payload: { progress: .42 } });
    expect(manager.getSnapshot().cursor).toBe(11);
    expect(manager.getSnapshot().jobs[0].progress).toBe(.42);
    await flush();
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ status: 'running', stage: 'solve', stage_message: 'Solving' });
    manager.stop();
  });

  it('sends resume with its last cursor after reconnect hello', () => {
    const sockets: MockSocket[] = [];
    const manager = new JobsSocketManager(() => { const socket = new MockSocket(); sockets.push(socket); return socket; }, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    sockets[0].message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    sockets[0].message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 27, jobs: [] });
    sockets[0].close();
    vi.advanceTimersByTime(250);
    sockets[1].message({ v: 1, kind: 'hello', epoch: 5, heartbeatSec: 15 });
    expect(JSON.parse(sockets[1].sent[0])).toEqual({ v: 1, kind: 'resume', epoch: 5, cursor: 27 });
    manager.stop();
  });

  it('detects a cursor gap and refetches the authoritative job list', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async () => json({ items: [job({ label: 'resynced' })] }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 9, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 9, cursor: 3, jobs: [job()] });
    compareSelection.setPrimary('job-1');
    compareSelection.toggleOverlay('orphan');
    socket.message({ v: 1, kind: 'event', epoch: 9, cursor: 8, jobId: 'job-1', type: 'completed', payload: {} });
    await flush();
    expect(fetcher).toHaveBeenCalledWith('/api/jobs?limit=200&offset=0');
    expect(manager.getSnapshot()).toMatchObject({ cursor: 3 });
    expect(manager.getSnapshot().error).toContain('gap');
    expect(manager.getSnapshot().jobs[0].label).toBe('resynced');
    expect(compareSelection.getSnapshot()).toEqual({ primary: 'job-1', overlays: [] });
    socket.message({ v: 1, kind: 'event', epoch: 9, cursor: 9, jobId: 'job-1', type: 'completed', payload: {} });
    expect(manager.getSnapshot().cursor).toBe(3);
    socket.message({ v: 1, kind: 'snapshot', epoch: 9, cursor: 9, jobs: [job({ label: 'snapshot' })] });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 9, error: null });
    manager.stop();
  });

  it('ignores stale status responses and invalidates an in-flight refresh on deletion', async () => {
    vi.useRealTimers();
    let resolveFirst!: (response: Response) => void;
    let resolveSecond!: (response: Response) => void;
    const first = new Promise<Response>((resolve) => { resolveFirst = resolve; });
    const second = new Promise<Response>((resolve) => { resolveSecond = resolve; });
    const fetcher = vi.fn()
      .mockReturnValueOnce(first)
      .mockReturnValueOnce(second);
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job()] });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'progress', payload: { progress: .2 } });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'job-1', type: 'completed', payload: {} });
    resolveSecond(json(job({ status: 'complete', progress: 1, has_results: true })));
    await flush();
    resolveFirst(json(job({ status: 'running', progress: .2 })));
    await flush();
    expect(manager.getSnapshot().jobs[0].status).toBe('complete');

    let resolveDeleted!: (response: Response) => void;
    fetcher.mockReturnValueOnce(new Promise<Response>((resolve) => { resolveDeleted = resolve; }));
    compareSelection.setPrimary('job-1');
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 4, jobId: 'job-1', type: 'progress', payload: { progress: .9 } });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 5, jobId: 'job-1', type: 'deleted', payload: {} });
    resolveDeleted(json(job({ status: 'running' })));
    await flush();
    expect(manager.getSnapshot().jobs).toEqual([]);
    expect(compareSelection.getSnapshot().primary).toBeNull();
    manager.stop();
  });
});

describe('rating metadata', () => {
  it('updates optimistically and rolls back a failed PATCH', async () => {
    const socket = new MockSocket();
    let reject!: (reason: Error) => void;
    const pending = new Promise<Response>((_resolve, fail) => { reject = fail; });
    const fetcher = vi.fn(() => pending);
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job({ rating: 2 })] });
    const update = manager.patchRating('job-1', 5);
    expect(manager.getSnapshot().jobs[0].rating).toBe(5);
    reject(new Error('offline'));
    await expect(update).rejects.toThrow('offline');
    expect(manager.getSnapshot().jobs[0].rating).toBe(2);
    expect(fetcher).toHaveBeenCalledWith('/api/jobs/job-1/metadata', expect.objectContaining({ method: 'PATCH', body: JSON.stringify({ rating: 5 }) }));
    manager.stop();
  });

  it('does not let an older failed mutation roll back a newer rating', async () => {
    const socket = new MockSocket();
    let rejectFirst!: (reason: Error) => void;
    let resolveSecond!: (response: Response) => void;
    const first = new Promise<Response>((_resolve, reject) => { rejectFirst = reject; });
    const second = new Promise<Response>((resolve) => { resolveSecond = resolve; });
    const fetcher = vi.fn().mockReturnValueOnce(first).mockReturnValueOnce(second);
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job({ rating: 2 })] });
    const older = manager.patchRating('job-1', 5);
    const newer = manager.patchRating('job-1', 4);
    expect(manager.getSnapshot().jobs[0].rating).toBe(4);
    rejectFirst(new Error('older failed'));
    await expect(older).rejects.toThrow('older failed');
    await flush();
    expect(manager.getSnapshot().jobs[0].rating).toBe(4);
    resolveSecond(json({}));
    await newer;
    expect(manager.getSnapshot().jobs[0].rating).toBe(4);
    manager.stop();
  });
});
