import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { JobsSocketManager, type JobItem, type JobsWebSocketLike } from '../api/jobsSocket';
import { compareSelection, provisionalResults } from '../api/results';

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
  raw(data: string) { this.onmessage?.({ data }); }
}

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    id: 'job-1', run_number: 1, parent_job_id: null,
    status: 'queued', progress: 0, stage: 'queued', stage_message: 'Queued',
    created_at: '2026-08-03T10:00:00Z', queued_at: '2026-08-03T10:00:00Z', started_at: null, completed_at: null,
    config_summary: { formula_type: 'OSSE', engine: 'dryrun' }, solve_options: {} as JobItem['solve_options'], has_results: false, has_mesh_artifact: false,
    label: null, error_message: null, cancellation_requested: false, mesh_stats: null, script_snapshot: null,
    design_revision: 0, polar_grid: {}, rating: null, exported_files: [], auto_export_completed_at: null,
    auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

async function flush(): Promise<void> { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); }

describe('jobs websocket state machine', () => {
  beforeEach(() => { vi.useFakeTimers(); compareSelection.clear(); provisionalResults.clear(); });
  afterEach(() => { provisionalResults.clear(); vi.useRealTimers(); });

  it('routes live frequency deltas outside the jobs list and recovers a revision gap', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      expect(String(input)).toBe('/api/partial-results/job-1');
      return json({ revision: 3, result: { frequencies: [200, 400, 800] } });
    });
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job({ status: 'running' })] });
    let jobNotifications = 0;
    manager.subscribe(() => { jobNotifications += 1; });

    socket.message({ v: 1, kind: 'partialResult', epoch: 4, jobId: 'job-1', revision: 1, result: { frequencies: [200] } });
    expect(provisionalResults.get('job-1')).toMatchObject({ revision: 1, result: { frequencies: [200] } });
    expect(jobNotifications).toBe(0);
    expect(manager.getSnapshot().cursor).toBe(10);

    socket.message({ v: 1, kind: 'partialResult', epoch: 4, jobId: 'job-1', revision: 3, result: { frequencies: [800] } });
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    // Response.json() settles after a Node-version-dependent number of
    // microtasks, so poll for the recovered snapshot instead of counting them.
    await vi.waitFor(() => expect(provisionalResults.get('job-1')).toMatchObject({ revision: 3, result: { frequencies: [200, 400, 800] } }));
    expect(jobNotifications).toBe(0);
    manager.stop();
  });

  it('accepts snapshot then contiguous events and tracks live job fields', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async () => json({ items: [job()] }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job()] });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'started', payload: { started_at: '2026-08-03T10:00:01Z' } });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 12, jobId: 'job-1', type: 'stage', payload: { stage: 'solve', message: 'Solving' } });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 13, jobId: 'job-1', type: 'progress', payload: { progress: .42 } });
    expect(manager.getSnapshot().cursor).toBe(13);
    expect(manager.getSnapshot().jobs[0].progress).toBe(.42);
    await flush();
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ status: 'running', stage: 'solve', stage_message: 'Solving' });
    expect(fetcher).not.toHaveBeenCalled();
    manager.stop();
  });

  it('uses the faithful retry endpoint and refreshes the run list', async () => {
    const fetcher = vi.fn(async (input: RequestInfo | URL) =>
      String(input).includes('/retry')
        ? json({ id: 'child' })
        : json({ items: [job({ id: 'child', parent_job_id: 'job-1' })], total: 1 }),
    );
    const manager = new JobsSocketManager(() => new MockSocket(), fetcher, 'ws://test/ws/jobs');

    await manager.retryJob('job-1');

    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/jobs/job-1/retry', { method: 'POST' });
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/jobs?limit=200&offset=0');
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ id: 'child', parent_job_id: 'job-1' });
  });

  it('does not apply messages from an unsupported protocol version', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 2, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connecting', error: 'Unsupported jobs protocol message' });

    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job()] });
    socket.message({ v: 2, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'progress', payload: { progress: .9 } });
    expect(manager.getSnapshot().jobs[0].progress).toBe(0);
    expect(manager.getSnapshot().cursor).toBe(10);
    manager.stop();
  });

  it('validates hello fields before changing connection state and accepts a valid recovery', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();

    socket.raw('{not-json');
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connecting', epoch: null, error: 'Malformed jobs message' });
    socket.message(null);
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connecting', epoch: null, error: 'Malformed jobs message' });
    socket.message({ v: 1, kind: 'hello', epoch: '4', heartbeatSec: 15 });
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connecting', epoch: null, error: 'Invalid jobs hello message' });
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: '15' });
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connecting', epoch: null, error: 'Invalid jobs hello message' });

    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    expect(manager.getSnapshot()).toMatchObject({ connection: 'connected', epoch: 4, error: null });
    manager.stop();
  });

  it('rejects malformed snapshot arrays and rows without poisoning a later valid snapshot', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });

    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 1, jobs: {} });
    expect(manager.getSnapshot()).toMatchObject({ cursor: null, jobs: [], error: 'Invalid jobs snapshot message' });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: '1', jobs: [job()] });
    expect(manager.getSnapshot().cursor).toBeNull();
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 1, jobs: [job({ run_number: 0 })] });
    expect(manager.getSnapshot().jobs).toEqual([]);
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 1, jobs: [{ ...job(), log_tail: 'not-an-array' }] });
    expect(manager.getSnapshot().jobs).toEqual([]);
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 1, jobs: [job(), job()] });
    expect(manager.getSnapshot().jobs).toEqual([]);

    const prototypeRow = JSON.parse(JSON.stringify(job())) as Record<string, unknown>;
    Object.defineProperty(prototypeRow, '__proto__', { enumerable: true, value: { polluted: true } });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 1, jobs: [prototypeRow] });
    expect(manager.getSnapshot().jobs).toEqual([]);
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();

    socket.message({
      v: 1,
      kind: 'snapshot',
      epoch: 4,
      cursor: 2,
      jobs: [{ ...job(), future_server_field: { safe: true } }],
    });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 2, error: null });
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ id: 'job-1', future_server_field: { safe: true } });
    manager.stop();
  });

  it('retains a safe saved CAD setup and rejects a non-imported setup shape', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    const cadSetup = {
      type: 'imported' as const,
      ingest_id: 'wgi_saved',
      drive_channels: [{
        id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' as const,
        driver: { sd_cm2: 82, bl_t_m: 11.4, re_ohm: 5.8 },
      }],
      combine: null,
      mesh: { rigid_size_mm: 8, transition_mm: 16, source_size_mm: { 'source-hf': 2.5 } },
    };

    socket.message({
      v: 1, kind: 'snapshot', epoch: 4, cursor: 1,
      jobs: [job({ cad_setup: cadSetup })],
    });
    expect(manager.getSnapshot().jobs[0].cad_setup).toEqual(cadSetup);

    socket.message({
      v: 1, kind: 'snapshot', epoch: 4, cursor: 2,
      jobs: [job({ cad_setup: { type: 'parametric' } as never })],
    });
    expect(manager.getSnapshot()).toMatchObject({
      cursor: 1,
      error: 'Invalid jobs snapshot message',
    });
    manager.stop();
  });

  it('rejects malformed event cursors before applying a valid recovery event', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job({ status: 'running' })] });

    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: '11', jobId: 'job-1', type: 'progress', payload: { progress: .4 } });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: -1, jobId: 'job-1', type: 'progress', payload: { progress: .4 } });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: Number.MAX_SAFE_INTEGER + 1, jobId: 'job-1', type: 'progress', payload: { progress: .4 } });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 10, error: 'Invalid jobs event message' });
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ progress: 0, log_tail: [] });

    socket.message({
      v: 1,
      kind: 'event',
      epoch: 4,
      cursor: 11,
      jobId: 'job-1',
      type: 'progress',
      payload: { progress: .4, future_payload_field: ['ignored'] },
      future_envelope_field: { ignored: true },
    });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 11, error: null });
    expect(manager.getSnapshot().jobs[0].progress).toBe(.4);
    manager.stop();
  });

  it('resyncs malformed event payloads and then accepts valid events', async () => {
    const socket = new MockSocket();
    const authoritative = job({ status: 'running', progress: .25 });
    const fetcher = vi.fn(async () => json({ items: [authoritative], total: 1 }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job({ status: 'running' })] });

    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'progress', payload: [] });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 11, error: 'Invalid jobs event message; resyncing' });
    await vi.waitFor(() => expect(manager.getSnapshot()).toMatchObject({ cursor: 11, error: null }));
    expect(manager.getSnapshot().jobs[0].progress).toBe(.25);

    // These stale malformed replays are rejected without touching the row.
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'progress', payload: { progress: 'fast' } });
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'log', payload: { lines: ['ok', 42] } });
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ progress: .25, log_tail: [] });

    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 12, jobId: 'job-1', type: 'progress', payload: { progress: .5 } });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 12, error: null });
    expect(manager.getSnapshot().jobs[0].progress).toBe(.5);
    manager.stop();
  });

  it('whitelists metadata changes and ignores identity, lifecycle, and prototype-like keys', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async () => json({ items: [job({ label: 'kept', rating: 5 })], total: 1 }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job()] });
    const changed = JSON.parse(`{
      "label":"kept",
      "rating":5,
      "id":"attacker",
      "run_number":999,
      "status":"complete",
      "progress":1,
      "created_at":"2099-01-01T00:00:00Z",
      "log_tail":["forged"],
      "__proto__":{"polluted":true},
      "prototype":{"polluted":true},
      "constructor":{"prototype":{"polluted":true}}
    }`) as Record<string, unknown>;

    socket.message({
      v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'metadata', payload: { changed },
    });

    expect(manager.getSnapshot().jobs[0]).toMatchObject({
      id: 'job-1', run_number: 1, status: 'queued', progress: 0,
      created_at: '2026-08-03T10:00:00Z', log_tail: [], label: 'kept', rating: 5,
    });
    expect(({} as { polluted?: boolean }).polluted).toBeUndefined();

    socket.message({
      v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'job-1', type: 'metadata', payload: { changed: { label: [] } },
    });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 3, error: 'Invalid jobs event message; resyncing' });
    expect(manager.getSnapshot().jobs[0].label).toBe('kept');
    await vi.waitFor(() => expect(manager.getSnapshot()).toMatchObject({ cursor: 3, error: null }));
    manager.stop();
  });

  it('resyncs through HTTP for an unknown future event type', async () => {
    const socket = new MockSocket();
    const authoritative = job({ status: 'complete', progress: 1, has_results: true });
    const fetcher = vi.fn(async () => json({ items: [authoritative], total: 1 }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job()] });

    socket.message({
      v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'archived', payload: { future: ['shape'] },
    });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 2 });
    expect(manager.getSnapshot().error).toContain('Unsupported jobs event type');
    await vi.waitFor(() => expect(manager.getSnapshot()).toMatchObject({
      cursor: 2, error: null, jobs: [expect.objectContaining({ status: 'complete', progress: 1 })],
    }));
    expect(fetcher).toHaveBeenCalledWith('/api/jobs?limit=200&offset=0');
    manager.stop();
  });

  it('rejects malformed partial results before accepting a valid replacement', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job({ status: 'running' })] });

    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 0, result: { frequencies: [200] } });
    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 1, snapshot: 'yes', result: { frequencies: [200] } });
    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 1, result: { frequencies: '200' } });
    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 1, result: { frequencies: [200], channels: [] } });
    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 1, result: { frequencies: [200], spl_on_axis: [] } });
    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 1, result: { frequencies: [200], directivity: { horizontal: 'wide' } } });
    const prototypeResult = JSON.parse('{"frequencies":[200],"__proto__":{"polluted":true}}');
    socket.message({ v: 1, kind: 'partialResult', epoch: 1, jobId: 'job-1', revision: 1, result: prototypeResult });
    expect(provisionalResults.get('job-1')).toBeUndefined();
    expect(manager.getSnapshot()).toMatchObject({ cursor: 1, error: 'Invalid jobs partialResult message' });

    socket.message({
      v: 1,
      kind: 'partialResult',
      epoch: 1,
      jobId: 'job-1',
      revision: 1,
      result: {
        frequencies: [200],
        spl_on_axis: { frequencies: [200], spl: [90], phase_degrees: [0] },
        directivity: { horizontal: [[[0, [1, 0]]]], vertical: [[[0, 0]]] },
        di: { frequencies: [200], di: { horizontal: [6], vertical: [5] } },
        channels: { combined: { frequencies: [200], impedance: { frequencies: [200], real: [1], imaginary: [0] } } },
        channel_order: ['combined'],
      },
    });
    expect(provisionalResults.get('job-1')).toMatchObject({
      revision: 1,
      result: { frequencies: [200], channel_order: ['combined'] },
    });
    expect(manager.getSnapshot()).toMatchObject({ cursor: 1, error: null });
    manager.stop();
  });

  it('does not notify subscribers when an event changes nothing', () => {
    // Four components subscribe to this store, and one of them rebuilds every
    // open ECharts option. A repeated progress value used to re-render all of
    // them, several times a second, for the whole duration of a solve.
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job({ status: 'running', progress: .5 })] });

    let notifications = 0;
    manager.subscribe(() => { notifications += 1; });
    const before = manager.getSnapshot();

    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'job-1', type: 'progress', payload: { progress: .5 } });
    expect(notifications).toBe(0);
    // The cursor still advances -- it just does not wake anybody on its own.
    expect(manager.getSnapshot().cursor).toBe(11);
    expect(manager.getSnapshot().jobs[0]).toBe(before.jobs[0]);

    // A real change still propagates, with fresh identities.
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 12, jobId: 'job-1', type: 'progress', payload: { progress: .75 } });
    expect(notifications).toBe(1);
    expect(manager.getSnapshot().jobs[0].progress).toBe(.75);
    expect(manager.getSnapshot().jobs[0]).not.toBe(before.jobs[0]);
    manager.stop();
  });

  it('keeps list order stable across patches, since created_at cannot change', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({
      v: 1, kind: 'snapshot', epoch: 4, cursor: 10,
      jobs: [
        job({ id: 'newer', created_at: '2026-08-03T12:00:00Z' }),
        job({ id: 'older', created_at: '2026-08-03T10:00:00Z' }),
      ],
    });
    expect(manager.getSnapshot().jobs.map((item) => item.id)).toEqual(['newer', 'older']);
    socket.message({ v: 1, kind: 'event', epoch: 4, cursor: 11, jobId: 'older', type: 'progress', payload: { progress: .9 } });
    expect(manager.getSnapshot().jobs.map((item) => item.id)).toEqual(['newer', 'older']);
    manager.stop();
  });

  it('splits multiline log chunks into bounded individual tail lines', () => {
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, vi.fn(), 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 4, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 4, cursor: 10, jobs: [job({ log_tail: ['existing'] })] });
    const lines = Array.from({ length: 32 }, (_, index) => `line-${index + 1}`);
    socket.message({
      v: 1,
      kind: 'event',
      epoch: 4,
      cursor: 11,
      jobId: 'job-1',
      type: 'log',
      // New servers preserve every logical line in `lines`; `chunk` remains a
      // single-line compatibility field for older clients.
      payload: { chunk: lines.at(-1), lines },
    });

    expect(manager.getSnapshot().jobs[0].log_tail).toHaveLength(30);
    expect(manager.getSnapshot().jobs[0].log_tail[0]).toBe('line-3');
    expect(manager.getSnapshot().jobs[0].log_tail.at(-1)).toBe('line-32');
    socket.message({
      v: 1,
      kind: 'event',
      epoch: 4,
      cursor: 12,
      jobId: 'job-1',
      type: 'log',
      payload: { chunk: 'legacy-a\r\nlegacy-b' },
    });
    expect(manager.getSnapshot().jobs[0].log_tail.slice(-2)).toEqual(['legacy-a', 'legacy-b']);
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

  it('recovers a cursor gap by requesting replay without relying on an unsolicited snapshot', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async () => json(job({ status: 'running' })));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 9, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 9, cursor: 3, jobs: [job()] });
    compareSelection.setPrimary('job-1');
    compareSelection.toggleOverlay('orphan');
    socket.message({ v: 1, kind: 'event', epoch: 9, cursor: 8, jobId: 'job-1', type: 'completed', payload: {} });
    expect(socket.sent.map((message) => JSON.parse(message))).toContainEqual({ v: 1, kind: 'resume', epoch: 9, cursor: 3 });
    expect(fetcher).not.toHaveBeenCalledWith('/api/jobs?limit=200&offset=0');
    expect(manager.getSnapshot()).toMatchObject({ cursor: 3 });
    expect(manager.getSnapshot().error).toContain('gap');
    for (let cursor = 4; cursor <= 8; cursor += 1) {
      socket.message({ v: 1, kind: 'event', epoch: 9, cursor, jobId: 'job-1', type: 'progress', payload: { progress: cursor / 10 } });
    }
    expect(manager.getSnapshot()).toMatchObject({ cursor: 8, error: null });
    expect(manager.getSnapshot().jobs[0].progress).toBe(.8);
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'job-1', overlays: [] });
    manager.stop();
  });

  it('paginates a REST refresh instead of dropping jobs beyond the first 200', async () => {
    const firstPage = Array.from({ length: 200 }, (_unused, index) => job({
      id: `job-${index}`,
      created_at: `2026-08-03T10:${String(Math.floor(index / 60)).padStart(2, '0')}:${String(index % 60).padStart(2, '0')}Z`,
    }));
    const lastPage = [job({ id: 'job-200', created_at: '2026-08-03T14:00:00Z' })];
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('offset=0')
      ? json({ items: firstPage, total: 201, limit: 200, offset: 0 })
      : json({ items: lastPage, total: 201, limit: 200, offset: 200 }));
    const manager = new JobsSocketManager(() => new MockSocket(), fetcher, 'ws://test/ws/jobs');

    await manager.refresh();

    expect(fetcher.mock.calls.map(([input]) => String(input))).toEqual([
      '/api/jobs?limit=200&offset=0',
      '/api/jobs?limit=200&offset=200',
    ]);
    expect(manager.getSnapshot().jobs).toHaveLength(201);
    expect(manager.getSnapshot().jobs.some(({ id }) => id === 'job-200')).toBe(true);
  });

  it('uses descending permanent run number when creation timestamps tie', async () => {
    const sameTime = '2026-08-03T10:00:00Z';
    const fetcher = vi.fn(async () => json({
      items: [
        job({ id: 'older', run_number: 1, created_at: sameTime }),
        job({ id: 'newer', run_number: 2, created_at: sameTime }),
      ],
      total: 2,
    }));
    const manager = new JobsSocketManager(() => new MockSocket(), fetcher, 'ws://test/ws/jobs');

    await manager.refresh();

    expect(manager.getSnapshot().jobs.map(({ id }) => id)).toEqual(['newer', 'older']);
  });

  it('merges concurrent WS mutations per job without letting stale REST resurrect deletions', async () => {
    let resolveList!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveList = resolve; }));
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({
      v: 1, kind: 'snapshot', epoch: 1, cursor: 1,
      jobs: [job({ id: 'live' }), job({ id: 'pruned' }), job({ id: 'untouched' })],
    });

    const refresh = manager.refresh();
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'live', type: 'progress', payload: { progress: .8 } });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'pruned', type: 'deleted', payload: { reason: 'retention' } });
    resolveList(json({
      items: [
        job({ id: 'live', progress: .1 }),
        job({ id: 'pruned' }),
        job({ id: 'untouched', status: 'complete', progress: 1, has_results: true }),
      ],
      total: 3,
    }));
    await refresh;

    expect(manager.getSnapshot().jobs.find(({ id }) => id === 'live')?.progress).toBe(.8);
    expect(manager.getSnapshot().jobs.some(({ id }) => id === 'pruned')).toBe(false);
    expect(manager.getSnapshot().jobs.find(({ id }) => id === 'untouched')).toMatchObject({ status: 'complete', progress: 1 });
    manager.stop();
  });

  it('preserves a row skipped when a concurrent deletion shifts offset pages', async () => {
    const socket = new MockSocket();
    const initial = Array.from({ length: 201 }, (_unused, index) => job({
      id: `job-${index}`,
      created_at: new Date(Date.UTC(2026, 7, 3, 10, 0, 201 - index)).toISOString(),
    }));
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('offset=0')) {
        return json({ items: initial.slice(0, 200), total: 201, limit: 200, offset: 0 });
      }
      // Deleting the first row moves job-200 from offset 200 to offset 199, so
      // a live database legitimately returns an empty second page.
      socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-0', type: 'deleted', payload: { reason: 'retention' } });
      return json({ items: [], total: 200, limit: 200, offset: 200 });
    });
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: initial });

    await manager.refresh();

    expect(manager.getSnapshot().jobs).toHaveLength(200);
    expect(manager.getSnapshot().jobs.some(({ id }) => id === 'job-0')).toBe(false);
    expect(manager.getSnapshot().jobs.some(({ id }) => id === 'job-200')).toBe(true);
    manager.stop();
  });

  it('preserves local deletes, clear-failed removals, and optimistic ratings across a stale refresh', async () => {
    let resolveList!: (response: Response) => void;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.startsWith('/api/jobs?')) return new Promise<Response>((resolve) => { resolveList = resolve; });
      if (path === '/api/jobs/clear-failed') return json({ deleted_ids: ['clear-me'] });
      return json({ status: 'ok' });
    });
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    const initial = [job({ id: 'delete-me' }), job({ id: 'clear-me', status: 'error' }), job({ id: 'rated', rating: 2 })];
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: initial });

    const refresh = manager.refresh();
    await manager.deleteJob('delete-me');
    await manager.clearFailed();
    await manager.patchRating('rated', 5);
    resolveList(json({ items: initial, total: initial.length }));
    await refresh;

    expect(manager.getSnapshot().jobs.map(({ id }) => id)).toEqual(['rated']);
    expect(manager.getSnapshot().jobs[0].rating).toBe(5);
    manager.stop();
  });

  it('preserves a status refresh that completes after the full refresh begins', async () => {
    let resolveStatus!: (response: Response) => void;
    let resolveList!: (response: Response) => void;
    const fetcher = vi.fn((input: RequestInfo | URL) => {
      const path = String(input);
      return new Promise<Response>((resolve) => {
        if (path.startsWith('/api/status/')) resolveStatus = resolve;
        else resolveList = resolve;
      });
    });
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job()] });
    // queued starts refreshJob but does not itself change the visible row.
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'queued', payload: {} });
    const refresh = manager.refresh();
    resolveStatus(json(job({ status: 'running', progress: .4 })));
    await flush();
    resolveList(json({ items: [job()], total: 1 }));
    await refresh;

    expect(manager.getSnapshot().jobs[0]).toMatchObject({ status: 'running', progress: .4 });
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
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'queued', payload: {} });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'job-1', type: 'completed', payload: {} });
    expect(fetcher).toHaveBeenCalledTimes(1);
    resolveFirst(json(job({ status: 'running', progress: .2 })));
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(2);
    resolveSecond(json(job({ status: 'complete', progress: 1, has_results: true })));
    await flush();
    expect(manager.getSnapshot().jobs[0].status).toBe('complete');

    let resolveDeleted!: (response: Response) => void;
    fetcher.mockReturnValueOnce(new Promise<Response>((resolve) => { resolveDeleted = resolve; }));
    compareSelection.setPrimary('job-1');
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 4, jobId: 'job-1', type: 'failed', payload: { message: 'late failure' } });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 5, jobId: 'job-1', type: 'deleted', payload: {} });
    resolveDeleted(json(job({ status: 'running' })));
    await flush();
    expect(manager.getSnapshot().jobs).toEqual([]);
    expect(compareSelection.getSnapshot().primary).toBeNull();
    manager.stop();
  });

  it('re-fetches a completed job when metadata invalidates its in-flight status response', async () => {
    let resolveFirst!: (response: Response) => void;
    let resolveSecond!: (response: Response) => void;
    const fetcher = vi.fn()
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveFirst = resolve; }))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { resolveSecond = resolve; }));
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job({ status: 'running' })] });

    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'completed', payload: {} });
    socket.message({
      v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'job-1', type: 'metadata',
      payload: { changed: { has_mesh_artifact: false, mesh_discarded_at: '2026-08-03T10:01:01Z' } },
    });
    resolveFirst(json(job({
      status: 'complete', progress: 1, has_results: true,
      completed_at: null, mesh_stats: null, has_mesh_artifact: true,
    })));
    await flush();

    expect(fetcher).toHaveBeenCalledTimes(2);
    expect(manager.getSnapshot().jobs[0].completed_at).toBeNull();

    resolveSecond(json(job({
      status: 'complete', progress: 1, has_results: true,
      completed_at: '2026-08-03T10:01:00Z', mesh_stats: { cells: 42 },
      has_mesh_artifact: false, mesh_discarded_at: '2026-08-03T10:01:01Z',
    })));
    await flush();
    await flush();

    expect(manager.getSnapshot().jobs[0]).toMatchObject({
      status: 'complete',
      completed_at: '2026-08-03T10:01:00Z',
      mesh_stats: { cells: 42 },
      has_mesh_artifact: false,
    });
    manager.stop();
  });

  it('caps follow-up status fetches during a continuous mutation stream', async () => {
    const socket = new MockSocket();
    let cursor = 2;
    const fetcher = vi.fn(async () => {
      cursor += 1;
      socket.message({
        v: 1, kind: 'event', epoch: 1, cursor, jobId: 'job-1', type: 'metadata',
        payload: { changed: { label: `mutation-${cursor}` } },
      });
      return json(job({ status: 'complete', progress: 1, completed_at: null }));
    });
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job({ status: 'running' })] });

    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'completed', payload: {} });
    await flush();
    await flush();

    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ status: 'complete', completed_at: null, label: 'mutation-5' });
    manager.stop();
  });

  it('preserves newer live fields when a queued-job status refresh returns late', async () => {
    let resolveStatus!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>((resolve) => { resolveStatus = resolve; }));
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job()] });

    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'queued', payload: {} });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'job-1', type: 'started', payload: { started_at: '2026-08-03T10:00:01Z' } });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 4, jobId: 'job-1', type: 'stage', payload: { stage: 'solve', message: 'Solving' } });
    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 5, jobId: 'job-1', type: 'progress', payload: { progress: .42 } });
    expect(manager.getSnapshot().jobs[0]).toMatchObject({ status: 'running', progress: .42, stage: 'solve' });

    resolveStatus(json(job()));
    await flush();

    expect(manager.getSnapshot().jobs[0]).toMatchObject({
      status: 'running',
      progress: .42,
      stage: 'solve',
      stage_message: 'Solving',
      started_at: '2026-08-03T10:00:01Z',
    });
    manager.stop();
  });

  it('preserves an optimistic rating and its metadata event across a late status response', async () => {
    let resolveStatus!: (response: Response) => void;
    const fetcher = vi.fn((input: RequestInfo | URL) => String(input).startsWith('/api/status/')
      ? new Promise<Response>((resolve) => { resolveStatus = resolve; })
      : Promise.resolve(json({ status: 'ok' })));
    const socket = new MockSocket();
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job()] });

    socket.message({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: 'job-1', type: 'completed', payload: {} });
    await manager.patchRating('job-1', 4);
    socket.message({
      v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: 'job-1', type: 'metadata', payload: { changed: { rating: 4 } },
    });
    expect(manager.getSnapshot().jobs[0].rating).toBe(4);

    resolveStatus(json(job({ status: 'complete', progress: 1, has_results: true, rating: null })));
    await flush();

    expect(manager.getSnapshot().jobs[0].rating).toBe(4);
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

describe('individual job deletion', () => {
  it('deletes one terminal job and prunes it from result selection', async () => {
    const socket = new MockSocket();
    const fetcher = vi.fn(async () => json({ deleted: true, job_id: 'job-1' }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    manager.start();
    socket.message({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
    socket.message({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [job({ status: 'complete', has_results: true })] });
    compareSelection.setPrimary('job-1');
    await manager.deleteJob('job-1');
    expect(fetcher).toHaveBeenCalledWith('/api/jobs/job-1', { method: 'DELETE' });
    expect(manager.getSnapshot().jobs).toEqual([]);
    expect(compareSelection.getSnapshot().primary).toBeNull();
    manager.stop();
  });
});
