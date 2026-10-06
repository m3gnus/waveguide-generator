import type { CadOperationSummary } from '../api/cadOperations';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';

/** Shared protocol fixtures for the solve cutover tests. */
export function cadJobFixture(operation: CadOperationSummary, overrides: Partial<JobItem> = {}): JobItem {
  const id = operation.jobId ?? operation.operationId;
  return {
    id, client_request_id: `cad-solve:${operation.operationId}`, parent_job_id: null,
    status: operation.state === 'accepted' ? 'queued'
      : operation.state === 'cancelled' ? 'cancelled'
      : operation.state === 'needs_user_input' || operation.state === 'rejected' ? 'error' : 'preparing',
    created_at: operation.createdAt ?? '', queued_at: operation.createdAt ?? '',
    config_summary: {}, solve_options: { engine: 'metal' }, log_tail: [], exported_files: [],
    auto_export_formats: {}, has_results: false, has_mesh_artifact: false,
    cad_intent: { operation_id: operation.operationId, ingest_id: 'wgi_prep1' },
    cad_provenance: operation.setupRevisionId ? {
      operation_id: operation.operationId,
      setup: { revision_id: operation.setupRevisionId, digest: 'setup', origin: operation.setupDefaults ? 'wg_defaults' : 'user' },
    } : { operation_id: operation.operationId },
    cad_state: {
      operation_id: operation.operationId, job_id: operation.jobId,
      state: operation.state, stage: operation.stage, reason: operation.reason, message: operation.message,
      snapshot: operation.snapshot ? {
        manifest_sha256: operation.snapshot.manifestSha256, artifact_sha256: null,
        document_name: operation.snapshot.documentName ?? null, project_lineage_id: operation.snapshot.projectLineageId ?? null,
      } : null,
      preparation: operation.preparationId ? {
        preparation_id: operation.preparationId, report_sha256: 'report', blocking_finding_ids: [],
      } : null,
      approvals: [], setup_defaults: Boolean(operation.setupDefaults), frame_axis_automatic: operation.frameAxisAutomatic ?? null,
      received_at: operation.createdAt, updated_at: operation.updatedAt,
    },
    ...overrides,
  } as JobItem;
}
export function publishCadJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}
export function publishCadSummary(operation: CadOperationSummary): void {
  const job = cadJobFixture(operation);
  const existing = jobsSocket.getSnapshot().jobs;
  publishCadJobs([...existing.filter((item) => item.id !== job.id && item.cad_state?.operation_id !== operation.operationId), job]);
}
