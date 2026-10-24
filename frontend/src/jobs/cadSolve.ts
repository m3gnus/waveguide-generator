import { useSyncExternalStore } from 'react';
import type { components } from '../api/generated/openapi';
import { jsonRequest } from '../api/cadlink';
import { jobsSocket, type JobItem } from '../api/jobsSocket';
import { operationNeedsUser, type CadOperationSummary } from '../api/cadOperations';

export type CadSolvePress = components['schemas']['CadSolveAgainRequest'];
/** The completion claims WG keeps for the solves its Solve started (guarantee row 8):
 * a finished run is shown and named once, whichever response or event comes first.
 * The press itself is the server's (`POST /api/models/{id}/solve`); nothing caches it. */
const CLAIM = 'wg2.cad.solve.claim.v1:';

export interface CadSolveClaim {
  designName: string;
  completionAcknowledged: boolean;
  /** The Fusion display owner has opened this run's retained model. Result
   * selection alone does not mean its geometry reached the viewport. */
  modelDisplayAcknowledged?: boolean;
  refusalAcknowledged?: boolean;
  /** The ingestion a WG press solved from; a Fusion request has none. */
  sourceIngestId?: string | null;
}
function read<T>(key: string): T | null {
  try { return JSON.parse(sessionStorage.getItem(key) ?? 'null') as T | null; }
  catch { return null; }
}
function write(key: string, value: unknown): void {
  sessionStorage.setItem(key, JSON.stringify(value));
}
export function rememberCadSolve(key: string, designName: string, sourceIngestId: string | null = null): void {
  const held = read<CadSolveClaim>(CLAIM + key);
  if (!held) write(CLAIM + key, { designName, completionAcknowledged: false, sourceIngestId });
  else if (!held.completionAcknowledged && ((!held.designName && designName) || (!held.sourceIngestId && sourceIngestId))) {
    // A claim made before the press knew its ingestion (an event can beat
    // the response) gains what it lacked; nothing it recorded is replaced.
    write(CLAIM + key, { ...held, designName: held.designName || designName, sourceIngestId: held.sourceIngestId ?? sourceIngestId });
  }
}
/** Drop the claim a press made before its request, once the server refused it: no job exists for it. */
export function forgetCadSolve(key: string): void {
  sessionStorage.removeItem(CLAIM + key);
}
export function cadSolveClaim(job: Pick<JobItem, 'id' | 'client_request_id'>): CadSolveClaim | null {
  return read(CLAIM + job.id) ?? (job.client_request_id ? read(CLAIM + job.client_request_id) : null);
}
export function acknowledgeCadSolve(job: JobItem, outcome: 'completion' | 'refusal' | 'display'): CadSolveClaim | null {
  const claim = cadSolveClaim(job);
  const field = outcome === 'completion' ? 'completionAcknowledged'
    : outcome === 'display' ? 'modelDisplayAcknowledged' : 'refusalAcknowledged';
  if (!claim || claim[field]) return null;
  const next = { ...claim, [field]: true };
  write(CLAIM + job.id, next);
  if (job.client_request_id) write(CLAIM + job.client_request_id, next);
  return claim;
}
export function cadJobOperationId(job: JobItem): string | null {
  const id = job.cad_provenance?.operation_id ?? job.cad_intent?.operation_id;
  return typeof id === 'string' && id ? id : null;
}

/** Historical refusals remain waiting requests after upgrading. */
export function isHeldCadJob(job: JobItem): boolean {
  return job.cad_state?.held ?? (job.status === 'held' || (job.status === 'error'
    && job.cad_state?.state === 'needs_user_input' && !!job.cad_state.reason));
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
    reason: state.reason, message: state.message, blocker: state.blocker, jobId: job.id,
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
export function solveCadAgain(jobId: string, press: CadSolvePress): Promise<{ job_id: string }> {
  return post(`/api/jobs/${encodeURIComponent(jobId)}/solve-again`, press);
}
/** Record approval only; JobsCoordinator owns continuing the solve. */
export async function approveCadJob(jobId: string, approvals: NonNullable<CadSolvePress['approvals']>): Promise<void> {
  await post(`/api/jobs/${encodeURIComponent(jobId)}/approvals`, approvals);
}
export async function dismissCadJob(job: JobItem): Promise<void> {
  const current = jobsSocket.getSnapshot().jobs.find((item) => item.id === job.id) ?? job;
  if (!isHeldCadJob(current) || !current.cad_state || !operationNeedsUser(cadJobSummary(current))) {
    throw new Error('Only a refused solve can be dismissed. Use Stop for an active solve.');
  }
  // The backend deletes the refused chain in one transaction, so a reconnect
  // can never revive a parent this snapshot did not know about.
  await post(`/api/jobs/${encodeURIComponent(job.id)}/dismiss`, {});
  await jobsSocket.refresh();
}
