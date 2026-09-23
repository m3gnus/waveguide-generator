/**
 * CAD Link's Solve card reports a solve in progress through the same
 * progress component the parametric run list uses (`SolveProgressView`,
 * `./solveProgress`) -- not a CAD-only copy of the stage/percentage text. A
 * job here is indistinguishable in origin from one submitted locally: the
 * card reads nothing but the operation's `jobId` and the matching `JobItem`
 * from the jobs socket, so an operation that reached "accepted" by any path
 * -- including one a waiting Fusion request advanced -- renders identically.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore } from '../stores/solveOptions';

vi.mock('./CadOperationsSection', () => ({ OnScreenSolveStatus: () => null }));
vi.mock('../jobs/useImportedSolvePlan', () => ({ useImportedSolvePlan: () => ({ plan: null }) }));

const { CadSolveCard } = await import('./CadSolveCard');

const MANIFEST = `sha256:${'c'.repeat(64)}`;

function record(): CadReturnIngestRecord {
  return {
    ingest_id: 'wgi_run', manifest_sha256: MANIFEST, artifact_sha256: 'artifact', report_sha256: 'report',
    findings: [], evidence: { fem_air_volumes: [] }, polar_grid_derivation: {},
    scope: { included: [] }, sources: [],
  } as unknown as CadReturnIngestRecord;
}

function operation(overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId: 'op-run', kind: 'prepare_and_solve', state: 'accepted', stage: 'submitted',
    reason: null, message: null, jobId: 'job-run', attemptGeneration: 1,
    setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: MANIFEST, documentName: 'PartyMEH' },
    legacy: false, createdAt: '2026-09-23T00:00:00Z', updatedAt: '2026-09-23T00:00:00Z',
    ...overrides,
  } as CadOperationSummary;
}

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    id: 'job-run', run_number: 1, parent_job_id: null, label: null, rating: 0,
    status: 'running', progress: 0.2, stage: 'mesh', stage_message: 'Building the surface mesh',
    created_at: '2026-09-23T00:00:00Z', queued_at: '2026-09-23T00:00:00Z', started_at: '2026-09-23T00:00:00Z', completed_at: null,
    config_summary: { drive_channel_ids: ['a', 'b', 'c'] },
    solve_options: { engine: 'metal', symmetry: 'half', num_frequencies: 8 } as JobItem['solve_options'],
    has_results: false, has_mesh_artifact: false, error_message: null, cancellation_requested: false, mesh_stats: null,
    script_snapshot: {}, design_revision: 1, polar_grid: {}, exported_files: [], auto_export_completed_at: null,
    auto_export_formats: {}, raw_results_file: null, mesh_artifact_file: null, log_tail: [],
    ...overrides,
  } as JobItem;
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

describe('CAD Solve card run status', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadOperationsStore();
    resetCadReturnStore();
    resetSolveOptionsStore();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    useCadOperationsStore.setState({ operations: {} });
  });

  it('shows the same stage words and frequency reading the run list uses, through every stage', async () => {
    useCadOperationsStore.setState({ operations: { 'op-run': operation() } });
    publishJobs([job({ stage: 'mesh', stage_message: 'Building the surface mesh', progress: 0.1 })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run')?.textContent).toBe('Preparing mesh · 10%');

    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 2/8 with Metal BEM', progress: 0.3 })]));
    expect(host.querySelector('.cad-solve-run')?.textContent).toBe('Solving · frequency 2 of 8');
  });

  it('reports a solve as it stands, whether pressed here or advanced by a waiting request', async () => {
    // Nothing in this render distinguishes "this browser pressed Solve" from
    // "a waiting request for this snapshot was advanced elsewhere": the card
    // reads only the operation's jobId and that job's own state.
    useCadOperationsStore.setState({ operations: { 'op-run': operation({ jobId: 'job-elsewhere' }) } });
    publishJobs([job({ id: 'job-elsewhere', stage: 'postprocess', stage_message: 'Packaging BEMPP BEM solver results', progress: 0.95 })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run')?.textContent).toBe('Combining · 95%');
  });

  it('says a run is done once its job completes', async () => {
    useCadOperationsStore.setState({ operations: { 'op-run': operation() } });
    publishJobs([job({ status: 'complete', progress: 1, stage: 'postprocess', stage_message: null, completed_at: '2026-09-23T00:01:00Z' })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run')?.textContent).toBe('Solved · its results are in Results.');
  });
});
