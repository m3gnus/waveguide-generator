export type JobStatus = 'queued' | 'running' | 'complete' | 'error' | 'cancelled';
export type JobsConnection = 'idle' | 'connecting' | 'connected' | 'reconnecting' | 'disconnected';

export interface JobItem {
  id: string;
  status: JobStatus;
  progress: number;
  stage: string | null;
  stage_message: string | null;
  created_at: string;
  queued_at: string;
  started_at: string | null;
  completed_at: string | null;
  config_summary: Record<string, unknown>;
  has_results: boolean;
  has_mesh_artifact: boolean;
  label: string | null;
  error_message: string | null;
  cancellation_requested: boolean;
  mesh_stats: Record<string, unknown> | null;
  script_snapshot: Record<string, unknown> | null;
  rating: number | null;
  exported_files: string[];
  auto_export_completed_at: string | null;
  raw_results_file: string | null;
  mesh_artifact_file: string | null;
  log_tail: string[];
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

const OPEN = 1;
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

  async clearFailed(): Promise<void> {
    const response = await this.fetcher('/api/jobs/clear-failed', { method: 'DELETE' });
    if (!response.ok) throw await responseError(response);
    const body = await response.json() as { deleted_ids?: string[] };
    const deleted = new Set(body.deleted_ids ?? []);
    this.update({ jobs: this.snapshot.jobs.filter((job) => !deleted.has(job.id)) });
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
    this.patchJob(jobId, { rating });
    try {
      await this.patchMetadata(jobId, { rating });
    } catch (error) {
      this.patchJob(jobId, { rating: previous });
      throw error;
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
    let message: HelloMessage | SnapshotMessage | EventMessage | { kind?: string; epoch?: number };
    try {
      message = JSON.parse(raw) as typeof message;
    } catch {
      this.update({ error: 'Malformed jobs message' });
      return;
    }
    if (message.kind === 'hello') {
      const hello = message as HelloMessage;
      if (hello.v !== 1 || this.helloSeen) return;
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
    if (message.kind === 'snapshot') {
      const incoming = message as SnapshotMessage;
      this.update({ cursor: incoming.cursor, jobs: this.sortJobs(incoming.jobs), error: null });
      return;
    }
    if (message.kind === 'event') this.onEvent(message as EventMessage);
  }

  private onEvent(message: EventMessage): void {
    const cursor = this.snapshot.cursor;
    if (cursor !== null && message.cursor <= cursor) return;
    if (cursor !== null && message.cursor !== cursor + 1) {
      this.update({ cursor: message.cursor, error: `Jobs event gap (${cursor} → ${message.cursor}); resyncing` });
      void this.refetchJobs();
      return;
    }
    this.update({ cursor: message.cursor });
    if (message.type === 'deleted') {
      this.update({ jobs: this.snapshot.jobs.filter((job) => job.id !== message.jobId) });
      return;
    }
    this.applyDelta(message);
    void this.refreshJob(message.jobId);
  }

  private applyDelta(message: EventMessage): void {
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
      patch.log_tail = [...current, chunk].filter(Boolean).slice(-30);
    }
    if (message.type === 'completed') Object.assign(patch, { status: 'complete', progress: 1, has_results: true });
    if (message.type === 'failed') Object.assign(patch, { status: 'error', error_message: String(payload.message ?? 'Simulation failed') });
    if (message.type === 'cancelled') Object.assign(patch, { status: 'cancelled', error_message: String(payload.message ?? 'Simulation cancelled') });
    if (message.type === 'metadata' && payload.changed && typeof payload.changed === 'object') {
      Object.assign(patch, payload.changed as Partial<JobItem>);
    }
    this.patchJob(message.jobId, patch);
  }

  private async refetchJobs(): Promise<void> {
    const generation = ++this.refetchGeneration;
    try {
      const response = await this.fetcher('/api/jobs?limit=200&offset=0');
      if (!response.ok) throw await responseError(response);
      const body = await response.json() as { items: JobItem[] };
      if (generation !== this.refetchGeneration) return;
      this.update({ jobs: this.sortJobs(body.items), error: null });
    } catch (error) {
      if (generation !== this.refetchGeneration) return;
      this.update({ error: error instanceof Error ? error.message : String(error) });
    }
  }

  private async refreshJob(jobId: string): Promise<void> {
    try {
      const response = await this.fetcher(`/api/status/${encodeURIComponent(jobId)}`);
      if (response.status === 404) {
        this.update({ jobs: this.snapshot.jobs.filter((job) => job.id !== jobId) });
        return;
      }
      if (!response.ok) throw await responseError(response);
      const job = await response.json() as JobItem;
      const jobs = this.snapshot.jobs.filter((item) => item.id !== job.id);
      this.update({ jobs: this.sortJobs([...jobs, job]), error: null });
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : String(error) });
    }
  }

  private patchJob(jobId: string, patch: Partial<JobItem>): void {
    const jobs = this.snapshot.jobs.map((job) => job.id === jobId ? { ...job, ...patch } : job);
    this.update({ jobs: this.sortJobs(jobs) });
  }

  private sortJobs(jobs: JobItem[]): JobItem[] {
    return [...jobs].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
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
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }
}

export const jobsSocket = new JobsSocketManager();

