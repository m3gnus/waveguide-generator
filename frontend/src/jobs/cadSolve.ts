import { useSyncExternalStore } from 'react';
import type { components } from '../api/generated/openapi';
import { jsonRequest } from '../api/cadlink';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';

export type CadSolvePress = components['schemas']['CadSolveAgainRequest'];
export type CadSolveRequest = components['schemas']['CadSolveRequest'];
const PENDING = 'wg2.cad.solve.pending.v1:';
const CLAIM = 'wg2.cad.solve.claim.v1:';
const LEGACY = 'wg2.cad.manual-solve.v1:';

export interface CadSolveClaim {
  designName: string;
  completionAcknowledged: boolean;
  refusalAcknowledged?: boolean;
  /** The ingestion a WG press solved from; a Fusion request has none. */
  sourceIngestId?: string | null;
}
export interface PendingCadSolve {
  requestId: string;
  designName: string;
  label: string;
  jobId?: string;
  recoveredJobId?: string;
  press?: CadSolveRequest;
}
function read<T>(key: string): T | null {
  try { return JSON.parse(sessionStorage.getItem(key) ?? 'null') as T | null; }
  catch { return null; }
}
function write(key: string, value: unknown): void {
  sessionStorage.setItem(key, JSON.stringify(value));
}
export function pendingCadSolve(ingestId: string): PendingCadSolve | null {
  return read(PENDING + ingestId);
}
export function beginCadSolve(ingestId: string, designName: string, label: string): PendingCadSolve {
  const held = pendingCadSolve(ingestId);
  if (held) return held;
  const created = { requestId: crypto.randomUUID(), designName, label };
  write(PENDING + ingestId, created);
  return created;
}
export function retainCadSolvePress(ingestId: string, held: PendingCadSolve): void {
  write(PENDING + ingestId, held);
}
export function rememberCadSolve(key: string, designName: string, sourceIngestId: string | null = null): void {
  const held = read<CadSolveClaim>(CLAIM + key);
  if (!held) write(CLAIM + key, { designName, completionAcknowledged: false, sourceIngestId });
  else if (!held.designName && designName && !held.completionAcknowledged) write(CLAIM + key, { ...held, designName });
}
export function cadSolveClaim(job: JobItem): CadSolveClaim | null {
  return read(CLAIM + job.id) ?? (job.client_request_id ? read(CLAIM + job.client_request_id) : null);
}
export function acknowledgeCadSolve(job: JobItem, outcome: 'completion' | 'refusal'): CadSolveClaim | null {
  const claim = cadSolveClaim(job);
  if (!claim || (outcome === 'completion' ? claim.completionAcknowledged : claim.refusalAcknowledged)) return null;
  const next = { ...claim, [outcome === 'completion' ? 'completionAcknowledged' : 'refusalAcknowledged']: true };
  write(CLAIM + job.id, next);
  if (job.client_request_id) write(CLAIM + job.client_request_id, next);
  return claim;
}
export function releaseCadSolve(ingestId: string, held: PendingCadSolve, jobId: string): void {
  rememberCadSolve(jobId, held.designName, ingestId);
  sessionStorage.removeItem(PENDING + ingestId);
}

/** Read identities left by the previous build once their job is available.
 * Until then they remain in storage: a failed reconnect cannot discard work. */
export function recoverLegacyCadSolves(jobs: JobItem[]): void {
  for (const key of Object.keys(sessionStorage)) {
    if (!key.startsWith(LEGACY)) continue;
    const raw = sessionStorage.getItem(key)!;
    const held = read<{ operationId: string; designName?: string; prepareAcknowledged?: boolean; completionAcknowledged?: boolean }>(key)
      ?? { operationId: raw };
    const job = jobs.find((candidate) => candidate.client_request_id === `cad-solve:${held.operationId}`);
    if (!job) continue;
    write(CLAIM + job.id, {
      designName: held.designName ?? '', completionAcknowledged: held.completionAcknowledged === true,
    });
    if (!held.prepareAcknowledged) {
      write(PENDING + key.slice(LEGACY.length), {
        requestId: job.id, designName: held.designName ?? '', label: job.label ?? '', recoveredJobId: job.id,
      });
    }
    sessionStorage.removeItem(key);
  }
}

export function hasLegacyCadSolve(ingestId: string): boolean {
  return sessionStorage.getItem(LEGACY + ingestId) !== null;
}
export function forgetPendingCadSolve(ingestId: string, requestId: string): void {
  if (pendingCadSolve(ingestId)?.requestId === requestId) sessionStorage.removeItem(PENDING + ingestId);
}
export function discardUnsubmittedCadSolve(ingestId: string): void {
  if (!pendingCadSolve(ingestId)?.press) sessionStorage.removeItem(PENDING + ingestId);
}

export function cadJobOperationId(job: JobItem): string | null {
  const id = job.cad_provenance?.operation_id ?? job.cad_intent?.operation_id;
  return typeof id === 'string' && id ? id : null;
}

/** Retain only the current child of a chain; timestamps order separate presses. */
export function latestCadJobs(jobs: JobItem[]): JobItem[] {
  const byId = new Map(jobs.map((job) => [job.id, job]));
  const parents = new Set(jobs.map((job) => job.parent_job_id).filter(Boolean));
  const chains = new Map<string, JobItem>();
  for (const job of jobs) {
    if (!job.cad_state || parents.has(job.id)) continue;
    let root = job;
    let rootId = job.id;
    const visited = new Set<string>();
    while (root.parent_job_id && !visited.has(root.parent_job_id)) {
      rootId = root.parent_job_id;
      visited.add(rootId);
      const parent = byId.get(rootId);
      if (!parent) break;
      root = parent;
    }
    const previous = chains.get(rootId);
    if (!previous || job.created_at > previous.created_at) chains.set(rootId, job);
  }
  return [...chains.values()].sort((a, b) => b.created_at.localeCompare(a.created_at));
}

/** Existing presentation vocabulary, supplied entirely by the job read model. */
export function cadJobSummary(job: JobItem): CadOperationSummary {
  const state = job.cad_state!;
  return {
    operationId: state.operation_id ?? cadJobOperationId(job) ?? job.id,
    kind: 'prepare_and_solve', state: state.state, stage: state.stage,
    reason: state.reason, message: state.message, jobId: job.id,
    attemptGeneration: 0, preparationId: state.preparation?.preparation_id ?? null,
    setupRevisionId: job.cad_provenance?.setup?.revision_id
      ?? (typeof job.cad_intent?.setup_revision_id === 'string' ? job.cad_intent.setup_revision_id : null),
    setupDefaults: state.setup_defaults, frameAxisAutomatic: state.frame_axis_automatic,
    snapshot: state.snapshot ? {
      manifestSha256: state.snapshot.manifest_sha256, documentName: state.snapshot.document_name,
      projectLineageId: state.snapshot.project_lineage_id,
    } : null,
    legacy: false, createdAt: state.received_at, updatedAt: state.updated_at,
  };
}
export function cadJobSummaries(jobs: JobItem[]): Record<string, CadOperationSummary> {
  return Object.fromEntries(latestCadJobs(jobs).map((job) => [job.id, cadJobSummary(job)]));
}
export function useCadJobs(): JobItem[] {
  return useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
}
async function post<T>(path: string, body: T): Promise<{ job_id: string }> {
  return jsonRequest(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }, fetch);
}
export function submitCadSolve(request: CadSolveRequest): Promise<{ job_id: string }> {
  return post('/api/jobs/cad-solve', request);
}
export function solveCadAgain(jobId: string, press: CadSolvePress): Promise<{ job_id: string }> {
  const { setup_revision_id, frame_axis, approvals, submit } = press;
  return post(`/api/jobs/${encodeURIComponent(jobId)}/solve-again`, { setup_revision_id, frame_axis, approvals, submit });
}
export async function approveCadJob(job: JobItem, preparationId: string, findingIds: string[]): Promise<void> {
  const approvals = { preparation_id: preparationId, finding_ids: findingIds };
  await post(`/api/jobs/${encodeURIComponent(job.id)}/approvals`, approvals);
  const child = await solveCadAgain(job.id, { approvals });
  rememberCadSolve(child.job_id, '');
  await jobsSocket.refresh();
}
export async function dismissCadJob(job: JobItem): Promise<void> {
  if (job.status === 'preparing' || job.status === 'queued' || job.status === 'running') await jobsSocket.stopJob(job.id);
  else {
    const ancestors: JobItem[] = [];
    let parentId = job.parent_job_id;
    const seen = new Set([job.id]);
    while (parentId && !seen.has(parentId)) {
      seen.add(parentId);
      const parent = jobsSocket.getSnapshot().jobs.find((item) => item.id === parentId);
      if (!parent?.cad_intent || (parent.status !== 'error' && parent.status !== 'cancelled')) break;
      ancestors.unshift(parent);
      parentId = parent.parent_job_id;
    }
    for (const parent of ancestors) await jobsSocket.deleteJob(parent.id);
    await jobsSocket.deleteJob(job.id);
  }
  // Refresh also drops ancestors absent from a local, older snapshot.
  await jobsSocket.refresh();
}
