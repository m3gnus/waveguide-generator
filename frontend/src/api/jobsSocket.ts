import { compareSelection, provisionalResults, type JobResults } from './results';

/**
 * Reference-compares own properties. Nested values are compared by identity,
 * which is deliberately conservative: a freshly parsed payload always looks
 * changed, so this can only ever suppress a notification that carried nothing.
 */
function shallowEqual(a: object, b: object): boolean {
  if (a === b) return true;
  const keys = Object.keys(a) as (keyof typeof a)[];
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((key) => Object.is(a[key], (b as typeof a)[key]));
}

export type JobStatus = 'queued' | 'running' | 'complete' | 'error' | 'cancelled';
export type JobsConnection = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected';
export interface AutoExportFormatStatus {
  status: 'complete' | 'failed';
  attempted_at: string;
  reason?: string;
}

export interface JobItem {
  id: string;
  run_number: number;
  parent_job_id: string | null;
  status: JobStatus;
  progress: number;
  stage: string | null;
  stage_message: string | null;
  created_at: string;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  config_summary: Record<string, unknown>;
  solve_options: {
    engine: string;
    symmetry: string;
    frequency_range: number[] | null;
    num_frequencies: number | null;
    frequency_spacing: 'log' | 'linear';
    frequencies_hz: number[] | null;
    verbose: boolean;
    mesh_validation_mode: 'warn' | 'strict' | 'off';
    polar_config: Record<string, unknown>;
    stage_delay_ms: number;
  };
  has_results: boolean;
  has_mesh_artifact: boolean;
  label: string | null;
  error_message: string | null;
  cancellation_requested: boolean;
  mesh_stats: Record<string, unknown> | null;
  script_snapshot: Record<string, unknown> | null;
  design_revision: number;
  polar_grid: Record<string, unknown>;
  rating: number | null;
  exported_files: string[];
  auto_export_completed_at: string | null;
  auto_export_formats: Record<string, AutoExportFormatStatus>;
  raw_results_file: string | null;
  mesh_artifact_file: string | null;
  results_discarded_at?: string | null;
  mesh_discarded_at?: string | null;
  log_tail: string[];
  design_availability?: {
    reopenable: boolean;
    source: 'v2-snapshot' | 'v1-design-state' | 'v1-mesher-payload' | 'cad-import' | 'none';
    reason_code: 'ok' | 'recovered' | 'imported_geometry' | 'freeform_legacy_design' | 'no_stored_design' | 'unreadable_design';
    reason: string | null;
    note: string | null;
  } | null;
  symmetry?: Record<string, unknown>;
  solve_path?: 'full-3d' | 'axisymmetric-meridian' | null;
  axisymmetric_eligibility_reasons?: string[];
  solve_wall_time_seconds?: number | null;
}

export interface JobsSnapshot {
  connection: JobsConnection;
  epoch: number | null;
  cursor: number | null;
  jobs: JobItem[];
  error: string | null;
}

interface SocketEvent { data?: unknown }

export interface JobsWebSocketLike {
  readyState: number;
  onopen: ((event: SocketEvent) => void) | null;
  onmessage: ((event: SocketEvent) => void) | null;
  onerror: ((event: SocketEvent) => void) | null;
  onclose: ((event: SocketEvent) => void) | null;
  send(data: string): void;
  close(): void;
}

export type JobsWebSocketFactory = (url: string) => JobsWebSocketLike;
export type JobsFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

interface HelloMessage {
  v: 1;
  kind: 'hello';
  epoch: number;
  heartbeatSec: number;
}

interface SnapshotMessage {
  v: 1;
  kind: 'snapshot';
  epoch?: number;
  cursor: number;
  jobs: JobItem[];
}

interface EventMessage {
  v: 1;
  kind: 'event';
  epoch?: number;
  cursor: number;
  jobId: string;
  type: 'queued' | 'started' | 'progress' | 'stage' | 'log' | 'completed' | 'failed' | 'cancelled' | 'deleted' | 'metadata';
  payload?: Record<string, unknown>;
}

interface PartialResultMessage {
  v: 1;
  kind: 'partialResult';
  epoch?: number;
  jobId: string;
  revision: number;
  snapshot?: boolean;
  result: JobResults;
}

const OPEN = 1;
const MAX_JOB_REFRESH_ATTEMPTS = 3;
const defaultFactory: JobsWebSocketFactory = (url) => new WebSocket(url) as unknown as JobsWebSocketLike;
const defaultFetch: JobsFetch = (input, init) => fetch(input, init);

function jobsUrl(): string {
  if (typeof window === 'undefined') return 'ws://localhost/ws/jobs';
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${protocol}//${window.location.host}/ws/jobs`;
}

async function responseError(response: Response): Promise<Error> {
  let detail = `${response.status} ${response.statusText}`.trim();
  try {
    const body = await response.json() as { detail?: string };
    if (body.detail) detail = body.detail;
  } catch {
    // A non-JSON response still has a useful status.
  }
  return new Error(detail);
}

export class JobsSocketManager {
  private socket: JobsWebSocketLike | null = null;
  private stopped = true;
  private helloSeen = false;
  private reconnectAttempt = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatTimer: ReturnType<typeof setTimeout> | null = null;
  private heartbeatMs = 30_000;
  private refetchGeneration = 0;
  private jobMutationCounter = 0;
  private readonly jobMutationVersions = new Map<string, number>();
  private gapTargetCursor: number | null = null;
  private readonly jobGenerations = new Map<string, number>();
  private readonly jobRefreshes = new Map<string, Promise<void>>();
  private readonly partialRefreshes = new Map<string, Promise<void>>();
  private readonly ratingMutations = new Map<string, {
    tail: Promise<void>;
    token: number;
    confirmed: number | null;
  }>();
  private readonly listeners = new Set<() => void>();
  private snapshot: JobsSnapshot = {
    connection: 'idle', epoch: null, cursor: null, jobs: [], error: null,
  };

  constructor(
    private readonly factory: JobsWebSocketFactory = defaultFactory,
    private readonly fetcher: JobsFetch = defaultFetch,
    private readonly url: string = jobsUrl(),
  ) {}

  getSnapshot = (): JobsSnapshot => this.snapshot;

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  start(): void {
    if (!this.stopped) return;
    this.stopped = false;
    this.connect(false);
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    const socket = this.socket;
    this.socket = null;
    socket?.close();
    provisionalResults.clear();
    this.update({ connection: 'disconnected', epoch: null });
  }

  async refresh(): Promise<void> {
    await this.refetchJobs();
  }

  async stopJob(jobId: string): Promise<void> {
    const response = await this.fetcher(`/api/stop/${encodeURIComponent(jobId)}`, { method: 'POST' });
    if (!response.ok) throw await responseError(response);
    await this.refreshJob(jobId);
  }

  async retryJob(jobId: string): Promise<void> {
    const response = await this.fetcher(
      `/api/jobs/${encodeURIComponent(jobId)}/retry`,
      { method: 'POST' },
    );
    if (!response.ok) throw await responseError(response);
    await this.refetchJobs();
  }

  async clearFailed(): Promise<void> {
    const response = await this.fetcher('/api/jobs/clear-failed', { method: 'DELETE' });
    if (!response.ok) throw await responseError(response);
    const body = await response.json() as { deleted_ids?: string[] };
    const deleted = new Set(body.deleted_ids ?? []);
    deleted.forEach((jobId) => {
      this.invalidateJob(jobId);
      provisionalResults.remove(jobId);
      this.markJobMutation(jobId);
    });
    this.update({ jobs: this.snapshot.jobs.filter((job) => !deleted.has(job.id)) });
  }

  async deleteJob(jobId: string): Promise<void> {
    const response = await this.fetcher(`/api/jobs/${encodeURIComponent(jobId)}`, { method: 'DELETE' });
    if (!response.ok) throw await responseError(response);
    this.invalidateJob(jobId);
    provisionalResults.remove(jobId);
    this.markJobMutation(jobId);
    this.update({ jobs: this.snapshot.jobs.filter((job) => job.id !== jobId) });
  }

  async patchMetadata(jobId: string, fields: Record<string, unknown>): Promise<void> {
    const response = await this.fetcher(`/api/jobs/${encodeURIComponent(jobId)}/metadata`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(fields),
    });
    if (!response.ok) throw await responseError(response);
  }

  async patchRating(jobId: string, rating: number | null): Promise<void> {
    const previous = this.snapshot.jobs.find((job) => job.id === jobId)?.rating ?? null;
    const state = this.ratingMutations.get(jobId) ?? {
      tail: Promise.resolve(), token: 0, confirmed: previous,
    };
    const token = state.token + 1;
    state.token = token;
    this.patchJob(jobId, { rating });
    const mutation = state.tail.catch(() => undefined).then(async () => {
      try {
        await this.patchMetadata(jobId, { rating });
        state.confirmed = rating;
      } catch (error) {
        const current = this.snapshot.jobs.find((job) => job.id === jobId)?.rating ?? null;
        if (state.token === token && current === rating) this.patchJob(jobId, { rating: state.confirmed });
        throw error;
      }
    });
    state.tail = mutation;
    this.ratingMutations.set(jobId, state);
    try {
      await mutation;
    } finally {
      if (this.ratingMutations.get(jobId)?.tail === mutation) this.ratingMutations.delete(jobId);
    }
  }

  private connect(reconnecting: boolean): void {
    if (this.stopped) return;
    this.update({ connection: reconnecting ? 'reconnecting' : 'connecting', epoch: null, error: null });
    const socket = this.factory(this.url);
    this.socket = socket;
    this.helloSeen = false;
    socket.onopen = () => undefined;
    socket.onmessage = (event) => this.onMessage(socket, event.data);
    socket.onerror = () => this.update({ error: 'Jobs connection error' });
    socket.onclose = () => {
      if (this.socket !== socket) return;
      this.socket = null;
      if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
      this.heartbeatTimer = null;
      if (!this.stopped) this.scheduleReconnect();
    };
  }

  private onMessage(socket: JobsWebSocketLike, raw: unknown): void {
    if (socket !== this.socket || typeof raw !== 'string') return;
    let message: HelloMessage | SnapshotMessage | EventMessage | PartialResultMessage | { kind?: string; epoch?: number };
    try {
      message = JSON.parse(raw) as typeof message;
    } catch {
      this.update({ error: 'Malformed jobs message' });
      return;
    }
    if ((message as { v?: unknown }).v !== 1) {
      this.update({ error: 'Unsupported jobs protocol message' });
      return;
    }
    if (message.kind === 'hello') {
      const hello = message as HelloMessage;
      if (this.helloSeen) return;
      this.helloSeen = true;
      this.reconnectAttempt = 0;
      this.heartbeatMs = Math.max(250, hello.heartbeatSec * 2_000);
      this.update({ connection: 'connected', epoch: hello.epoch, error: null });
      this.armHeartbeat();
      if (this.snapshot.cursor !== null && socket.readyState === OPEN) {
        socket.send(JSON.stringify({ v: 1, kind: 'resume', epoch: hello.epoch, cursor: this.snapshot.cursor }));
      }
      return;
    }
    if (!this.helloSeen || ('epoch' in message && message.epoch !== undefined && message.epoch !== this.snapshot.epoch)) return;
    this.armHeartbeat();
    if (message.kind === 'partialResult') {
      const partial = message as PartialResultMessage;
      if (
        typeof partial.jobId === 'string'
        && Number.isInteger(partial.revision)
        && partial.result
        && typeof partial.result === 'object'
      ) {
        const applied = provisionalResults.apply(
          partial.jobId,
          partial.revision,
          partial.result,
          partial.snapshot === true,
        );
        if (!applied) void this.refreshPartialResults(partial.jobId);
      }
      return;
    }
    if (message.kind === 'snapshot') {
      const incoming = message as SnapshotMessage;
      this.gapTargetCursor = null;
      new Set([...this.snapshot.jobs.map((job) => job.id), ...incoming.jobs.map((job) => job.id)])
        .forEach((jobId) => this.markJobMutation(jobId));
      provisionalResults.prune(new Set(incoming.jobs
        .filter((job) => job.status === 'running' || job.status === 'queued')
        .map((job) => job.id)));
      this.update({ cursor: incoming.cursor, jobs: this.sortJobs(incoming.jobs), error: null });
      return;
    }
    if (message.kind === 'event') this.onEvent(message as EventMessage);
  }

  private onEvent(message: EventMessage): void {
    const cursor = this.snapshot.cursor;
    if (cursor !== null && message.cursor <= cursor) return;
    if (this.gapTargetCursor !== null) {
      if (cursor === null || message.cursor !== cursor + 1) return;
      this.markJobMutation(message.jobId);
      if (message.type === 'deleted') {
        this.invalidateJob(message.jobId);
        provisionalResults.remove(message.jobId);
        this.update({
          cursor: message.cursor,
          jobs: this.snapshot.jobs.filter((job) => job.id !== message.jobId),
        });
      } else {
        this.commitEvent(message);
        if (this.eventNeedsRefresh(message)) void this.refreshJob(message.jobId);
      }
      if (message.cursor >= this.gapTargetCursor) {
        this.gapTargetCursor = null;
        this.update({ error: null });
      }
      return;
    }
    if (cursor !== null && message.cursor !== cursor + 1) {
      this.gapTargetCursor = message.cursor;
      this.update({ error: `Jobs event gap (${cursor} → ${message.cursor}); resyncing` });
      if (this.socket?.readyState === OPEN && this.snapshot.epoch !== null) {
        this.socket.send(JSON.stringify({
          v: 1,
          kind: 'resume',
          epoch: this.snapshot.epoch,
          cursor,
        }));
      }
      return;
    }
    this.markJobMutation(message.jobId);
    if (message.type === 'deleted') {
      this.invalidateJob(message.jobId);
      provisionalResults.remove(message.jobId);
      this.update({
        cursor: message.cursor,
        jobs: this.snapshot.jobs.filter((job) => job.id !== message.jobId),
      });
      return;
    }
    this.commitEvent(message);
    if (this.eventNeedsRefresh(message)) void this.refreshJob(message.jobId);
  }

  /**
   * Advance the cursor and apply the event's effect in one update.
   *
   * Two updates would mean two notifications, and the cursor always moves --
   * so subscribers were woken for every event whether or not anything they
   * render had changed. The cursor is internal resume bookkeeping; nothing in
   * the interface displays it.
   */
  private commitEvent(message: EventMessage): void {
    const jobs = this.applyDelta(message);
    this.update(jobs === null ? { cursor: message.cursor } : { cursor: message.cursor, jobs });
  }

  private eventNeedsRefresh(message: EventMessage): boolean {
    if (!this.snapshot.jobs.some((job) => job.id === message.jobId)) return true;
    return message.type === 'queued'
      || message.type === 'completed'
      || message.type === 'failed'
      || message.type === 'cancelled';
  }

  /** The job list this event implies, or null when it changes nothing. */
  private applyDelta(message: EventMessage): JobItem[] | null {
    const payload = message.payload ?? {};
    const patch: Partial<JobItem> = {};
    if (message.type === 'queued') Object.assign(patch, { status: 'queued', progress: 0 });
    if (message.type === 'started') Object.assign(patch, { status: 'running', started_at: String(payload.started_at ?? new Date().toISOString()) });
    if (message.type === 'progress') patch.progress = Number(payload.progress ?? 0);
    if (message.type === 'stage') Object.assign(patch, {
      stage: typeof payload.stage === 'string' ? payload.stage : null,
      stage_message: typeof payload.message === 'string' ? payload.message : null,
      ...(typeof payload.progress === 'number' ? { progress: payload.progress } : {}),
    });
    if (message.type === 'log') {
      const chunk = typeof payload.chunk === 'string' ? payload.chunk : '';
      const current = this.snapshot.jobs.find((job) => job.id === message.jobId)?.log_tail ?? [];
      const lines = Array.isArray(payload.lines)
        ? payload.lines.filter((line): line is string => typeof line === 'string' && Boolean(line))
        : chunk.split(/\r\n|\r|\n/).filter(Boolean);
      patch.log_tail = [...current, ...lines].slice(-30);
    }
    if (message.type === 'completed') Object.assign(patch, { status: 'complete', progress: 1, has_results: true });
    if (message.type === 'failed') Object.assign(patch, { status: 'error', error_message: String(payload.message ?? 'Simulation failed') });
    if (message.type === 'cancelled') Object.assign(patch, { status: 'cancelled', error_message: String(payload.message ?? 'Simulation cancelled') });
    if (message.type === 'failed' || message.type === 'cancelled') provisionalResults.remove(message.jobId);
    if (message.type === 'metadata' && payload.changed && typeof payload.changed === 'object') {
      Object.assign(patch, payload.changed as Partial<JobItem>);
    }
    return this.patchedJobs(message.jobId, patch);
  }

  private async refetchJobs(): Promise<void> {
    const generation = ++this.refetchGeneration;
    const mutationBaseline = this.jobMutationCounter;
    try {
      const pageSize = 200;
      const fetched = new Map<string, JobItem>();
      let offset = 0;
      let total = Infinity;
      while (offset < total) {
        const response = await this.fetcher(`/api/jobs?limit=${pageSize}&offset=${offset}`);
        if (!response.ok) throw await responseError(response);
        const body = await response.json() as { items: JobItem[]; total?: number };
        if (generation !== this.refetchGeneration) return;
        body.items.forEach((job) => fetched.set(job.id, job));
        offset += body.items.length;
        total = Number.isInteger(body.total) && Number(body.total) >= 0 ? Number(body.total) : offset;
        // A changing database can legitimately make the final page shorter
        // than the count observed on an earlier page. Never spin on an empty
        // page while trying to reach a now-stale total.
        if (body.items.length === 0) break;
      }
      // REST pages are not one atomic server snapshot. Preserve current
      // membership when a mutation could have shifted an offset boundary, then
      // overlay each job changed through WS/status/local traffic. A changed id
      // absent from the current list represents a concurrent deletion and must
      // not be resurrected by an older page.
      const current = new Map(this.snapshot.jobs.map((job) => [job.id, job]));
      if (this.jobMutationCounter !== mutationBaseline) {
        // Insertions/deletions shift offset pagination. If anything changed
        // during the walk, a still-live row missing from every fetched page may
        // simply have crossed a page boundary. Preserve those current rows;
        // per-id tombstones below still remove jobs actually deleted.
        current.forEach((job, jobId) => {
          if (!fetched.has(jobId)) fetched.set(jobId, job);
        });
      }
      this.jobMutationVersions.forEach((version, jobId) => {
        if (version <= mutationBaseline) return;
        const live = current.get(jobId);
        if (live) fetched.set(jobId, live);
        else fetched.delete(jobId);
      });
      const items = [...fetched.values()];
      new Set([...this.snapshot.jobs.map((job) => job.id), ...items.map((job) => job.id)])
        .forEach((jobId) => this.invalidateJob(jobId));
      this.update({ jobs: this.sortJobs(items), error: this.gapTargetCursor === null ? null : this.snapshot.error });
    } catch (error) {
      if (generation !== this.refetchGeneration) return;
      this.update({ error: error instanceof Error ? error.message : String(error) });
    }
  }

  private refreshJob(jobId: string): Promise<void> {
    const existing = this.jobRefreshes.get(jobId);
    if (existing) return existing;

    const generation = this.nextJobGeneration(jobId);
    const refresh = this.refreshJobUntilStable(jobId, generation);
    this.jobRefreshes.set(jobId, refresh);
    const clearRefresh = () => {
      if (this.jobRefreshes.get(jobId) === refresh) this.jobRefreshes.delete(jobId);
    };
    void refresh.then(clearRefresh, clearRefresh);
    return refresh;
  }

  private refreshPartialResults(jobId: string): Promise<void> {
    const existing = this.partialRefreshes.get(jobId);
    if (existing) return existing;
    const refresh = (async () => {
      try {
        const response = await this.fetcher(`/api/partial-results/${encodeURIComponent(jobId)}`);
        if (response.status === 404) {
          provisionalResults.remove(jobId);
          return;
        }
        if (!response.ok) throw await responseError(response);
        const body = await response.json() as { revision?: number; result?: JobResults };
        if (Number.isInteger(body.revision) && body.result && typeof body.result === 'object') {
          provisionalResults.apply(jobId, Number(body.revision), body.result, true);
        }
      } catch (error) {
        this.update({ error: error instanceof Error ? error.message : String(error) });
      }
    })();
    this.partialRefreshes.set(jobId, refresh);
    const clear = () => {
      if (this.partialRefreshes.get(jobId) === refresh) this.partialRefreshes.delete(jobId);
    };
    void refresh.then(clear, clear);
    return refresh;
  }

  private async refreshJobUntilStable(jobId: string, generation: number): Promise<void> {
    for (let attempt = 0; attempt < MAX_JOB_REFRESH_ATTEMPTS; attempt += 1) {
      const mutationBaseline = this.jobMutationVersions.get(jobId) ?? 0;
      try {
        const response = await this.fetcher(`/api/status/${encodeURIComponent(jobId)}`);
        if (this.jobGenerations.get(jobId) !== generation) return;
        if ((this.jobMutationVersions.get(jobId) ?? 0) > mutationBaseline) continue;
        if (response.status === 404) {
          this.invalidateJob(jobId);
          this.markJobMutation(jobId);
          this.update({ jobs: this.snapshot.jobs.filter((job) => job.id !== jobId) });
          return;
        }
        if (!response.ok) throw await responseError(response);
        const job = await response.json() as JobItem;
        if (this.jobGenerations.get(jobId) !== generation) return;
        if ((this.jobMutationVersions.get(jobId) ?? 0) > mutationBaseline) continue;
        this.markJobMutation(jobId);
        const jobs = this.snapshot.jobs.filter((item) => item.id !== job.id);
        this.update({ jobs: this.sortJobs([...jobs, job]), error: null });
        return;
      } catch (error) {
        if (this.jobGenerations.get(jobId) !== generation) return;
        this.update({ error: error instanceof Error ? error.message : String(error) });
        return;
      }
    }
  }

  private nextJobGeneration(jobId: string): number {
    const generation = (this.jobGenerations.get(jobId) ?? 0) + 1;
    this.jobGenerations.set(jobId, generation);
    return generation;
  }

  private invalidateJob(jobId: string): void {
    this.nextJobGeneration(jobId);
  }

  private markJobMutation(jobId: string): void {
    this.jobMutationCounter += 1;
    this.jobMutationVersions.set(jobId, this.jobMutationCounter);
  }

  /** Apply a patch immediately, for local optimistic edits such as a rating. */
  private patchJob(jobId: string, patch: Partial<JobItem>): void {
    this.markJobMutation(jobId);
    const jobs = this.patchedJobs(jobId, patch);
    if (jobs !== null) this.update({ jobs });
  }

  /** Merge a patch into one job, returning null when nothing actually changes. */
  private patchedJobs(jobId: string, patch: Partial<JobItem>): JobItem[] | null {
    let changed = false;
    const jobs = this.snapshot.jobs.map((job) => {
      if (job.id !== jobId) return job;
      const merged = { ...job, ...patch };
      // A running solve emits progress, stage and log events continuously, and
      // many carry values the list already holds. Returning the same object
      // keeps the snapshot identity stable so nothing downstream re-renders.
      if (shallowEqual(job, merged)) return job;
      changed = true;
      return merged;
    });
    // No re-sort: created_at never changes, so a patch cannot reorder the list.
    return changed ? jobs : null;
  }

  private sortJobs(jobs: JobItem[]): JobItem[] {
    // Only for calls that change membership. Date.parse is called once per job
    // rather than twice per comparison, which matters at the 200-job page size.
    return jobs
      .map((job) => ({ job, at: Date.parse(job.created_at) }))
      .sort((a, b) => b.at - a.at || b.job.run_number - a.job.run_number)
      .map((entry) => entry.job);
  }

  private scheduleReconnect(): void {
    this.update({ connection: 'reconnecting', epoch: null });
    const delay = Math.min(5_000, 250 * 2 ** this.reconnectAttempt);
    this.reconnectAttempt += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect(true);
    }, delay);
  }

  private armHeartbeat(): void {
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    this.heartbeatTimer = setTimeout(() => this.socket?.close(), this.heartbeatMs);
  }

  private clearTimers(): void {
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.heartbeatTimer) clearTimeout(this.heartbeatTimer);
    this.reconnectTimer = null;
    this.heartbeatTimer = null;
  }

  private update(patch: Partial<JobsSnapshot>): void {
    const previous = this.snapshot;
    const next = { ...previous, ...patch };
    this.snapshot = next;
    if (patch.jobs) compareSelection.prune(new Set(patch.jobs.map((job) => job.id)));
    // Every subscriber reads the whole snapshot through useSyncExternalStore,
    // which compares by identity -- so any notification re-renders the Results
    // panel, the Jobs panel and the coordinator. Two things must therefore not
    // wake them: an update that changed nothing at all, and the event cursor,
    // which advances on every single event of a running solve but is internal
    // resume bookkeeping that no part of the interface displays. The value is
    // still written above, so a render triggered by anything else sees it.
    if (shallowEqual({ ...previous, cursor: next.cursor }, next)) return;
    this.listeners.forEach((listener) => listener());
  }
}

export const jobsSocket = new JobsSocketManager();
