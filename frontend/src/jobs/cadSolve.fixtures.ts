import refusedJob from './cadRefusedJob.fixture.json';
import type { CadOperationSummary } from '../api/cadOperations';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';

/** CAD job shapes as the backend serializer writes them; `cadRefusedJob.fixture.json`
 * is a refused intent serialized by `JobRuntime._serialize_job`. */
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
    cad_intent: operation.state === 'accepted' ? null : {
      type: refusedJob.cad_intent.type, operation_id: operation.operationId,
      bundle_path: refusedJob.cad_intent.bundle_path, return_id: refusedJob.cad_intent.return_id,
      manifest_sha256: operation.snapshot?.manifestSha256 ?? refusedJob.cad_intent.manifest_sha256,
      ...(operation.setupRevisionId ? { setup_revision_id: operation.setupRevisionId } : {}),
    },
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
        ...refusedJob.cad_state.preparation, ingest_id: 'wgi_prep1', preparation_id: operation.preparationId, report_sha256: 'report', blocking_finding_ids: [],
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
