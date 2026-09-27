/**
 * CAD Link's Solve card reports a solve through the same progress model the
 * parametric run list uses (`SolveProgressView`/`operationStageWord`/
 * `solveStageWord`, `./solveProgress`) -- not a CAD-only copy of the
 * stage/percentage text, and not two separate vocabularies either side of
 * the moment a job appears. A job here is indistinguishable in origin from
 * one submitted locally: the card reads only the operation's own state (or,
 * once accepted, its `jobId` and the matching `JobItem`), so a request that
 * reached any given point by any path -- including one a waiting Fusion
 * request advanced -- renders identically.
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
import { resetSolveStageClocksForTests } from './solveProgress';

vi.mock('./CadOperationsSection', () => ({ OnScreenSolveStatus: () => null }));
vi.mock('../jobs/useImportedSolvePlan', () => ({ useImportedSolvePlan: () => ({ plan: null }) }));

const { CadSolveCard } = await import('./CadSolveCard');

const MANIFEST = `sha256:${'c'.repeat(64)}`;
const NOW = '2026-09-23T00:00:00Z';

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
    legacy: false, createdAt: NOW, updatedAt: NOW,
    ...overrides,
  } as CadOperationSummary;
}

function job(overrides: Partial<JobItem> = {}): JobItem {
  return {
    id: 'job-run', run_number: 1, parent_job_id: null, label: null, rating: 0,
    status: 'running', progress: 0.2, stage: 'mesh', stage_message: 'Building the surface mesh',
    created_at: NOW, queued_at: NOW, started_at: NOW, completed_at: null,
    config_summary: { drive_channel_ids: ['a', 'b', 'c'], symmetry: { resolved: 'half' } },
    solve_options: { engine: 'metal', symmetry: 'auto', num_frequencies: 8 } as JobItem['solve_options'],
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

function publishOperation(op: CadOperationSummary): void {
  useCadOperationsStore.setState({ operations: { [op.operationId]: op } });
}

describe('CAD Solve card run status', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resetCadOperationsStore();
    resetCadReturnStore();
    resetSolveOptionsStore();
    vi.useFakeTimers();
    vi.setSystemTime(new Date(NOW));
    resetSolveStageClocksForTests();
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    useCadOperationsStore.setState({ operations: {} });
    vi.useRealTimers();
  });

  it('walks the CAD operation pipeline and the job it hands off to as one monotonic sequence', async () => {
    // received, still validating: "Received".
    publishOperation(operation({ state: 'received', stage: 'validating', jobId: null }));
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Received');

    // preparation.py's "preparing-mesh" stage: "Preparing mesh" -- never back
    // to "Received".
    act(() => publishOperation(operation({ state: 'processing', stage: 'preparing-mesh', jobId: null })));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    // ready to submit, still no job: preparation has not stepped back.
    act(() => publishOperation(operation({ state: 'processing', stage: 'ready', jobId: null })));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    // Accepted, job not in the list yet yet (within the grace window): says
    // so plainly, not a made-up stage.
    act(() => publishOperation(operation({ state: 'accepted', stage: 'submitted' })));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    // The queued window continues mesh preparation.
    act(() => publishJobs([job({ status: 'queued', stage: null, stage_message: null })]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    act(() => publishJobs([job({ status: 'running', stage: 'initializing', stage_message: 'Initializing solver', progress: 0.05 })]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    // The runtime emits mesh after initializing; both stay in mesh preparation.
    act(() => publishJobs([job({ status: 'running', stage: 'mesh', stage_message: 'Building the surface mesh', progress: 0.1 })]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    act(() => publishJobs([job({ stage: 'assemble', stage_message: 'Configuring Metal BEM solve', progress: 0.3 })]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Starting…');

    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 2/8 with Metal BEM', progress: 0.3 })]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Solving');
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('frequency 2 of 8');

    act(() => publishJobs([job({ stage: 'postprocess', stage_message: 'Packaging BEMPP BEM solver results', progress: 0.95 })]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Combining');
  });

  it('shows a clear waiting-for-you state for needs_user_input, not a pipeline stage', async () => {
    publishOperation(operation({ state: 'needs_user_input', stage: 'validating', reason: 'setup_required', jobId: null }));
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Waiting for you');
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('needs its solve settings');
    expect(host.querySelector('.cad-solve-run')?.className).toContain('cad-solve-run-warn');
  });

  it('never shows a previous run\'s outcome under a new request for the same snapshot', async () => {
    // The first request finished and its job completed.
    publishOperation(operation({ operationId: 'op-old', state: 'accepted', jobId: 'job-old', updatedAt: '2026-09-22T23:00:00Z' }));
    publishJobs([job({ id: 'job-old', status: 'complete', progress: 1, completed_at: '2026-09-22T23:01:00Z' })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Done');

    // A second, newer request for the same snapshot starts -- still being
    // validated, no job of its own yet. Only op-old's operation store entry
    // is replaced (a real re-solve creates a new operation id); the old
    // job is still in the jobs list, exactly as it would be after a reload.
    act(() => useCadOperationsStore.setState({
      operations: {
        'op-old': operation({ operationId: 'op-old', state: 'accepted', jobId: 'job-old', updatedAt: '2026-09-22T23:00:00Z' }),
        'op-new': operation({ operationId: 'op-new', state: 'received', stage: 'validating', jobId: null, updatedAt: NOW }),
      },
    }));
    expect(host.querySelector('.cad-solve-run')?.textContent).not.toContain('Solved');
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Received');
  });

  it('reports a running solve as it stands, whether pressed here or advanced by a waiting request', async () => {
    // Nothing in this render distinguishes "this browser pressed Solve" from
    // "a waiting request for this snapshot was advanced elsewhere": the card
    // reads only the operation's jobId and that job's own state.
    publishOperation(operation({ jobId: 'job-elsewhere' }));
    publishJobs([job({ id: 'job-elsewhere', stage: 'postprocess', stage_message: 'Packaging BEMPP BEM solver results', progress: 0.95 })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    const text = host.querySelector('.cad-solve-run')?.textContent ?? '';
    expect(text).toContain('Combining');
    expect(text).toContain('95%');
  });

  it('shows the same elapsed time, engine/sources/domain detail and ETA the run list shows, in its compact layout', async () => {
    publishOperation(operation());
    publishJobs([job({
      stage: 'solve', stage_message: 'Solving frequency 1/10 with Metal BEM', progress: 0.3,
    })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('frequency 1 of 10');
    // Elapsed since the job started, at 0 s here.
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('0:00');
    // Engine/sources/domain, the same detail line the full variant shows.
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('Metal · 3 sources · half');

    await act(async () => { vi.advanceTimersByTime(20_000); });
    act(() => publishJobs([job({
      stage: 'solve', stage_message: 'Solving frequency 2/10 with Metal BEM', progress: 0.4,
    })]));
    const text = host.querySelector('.cad-solve-run')?.textContent ?? '';
    expect(text).toContain('frequency 2 of 10');
    expect(text).toContain('ETA 2:40');
  });

  it('ticks elapsed time without a jobs update and measures ETA from a late first frequency', async () => {
    publishOperation(operation());
    publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 5/10', progress: 0.6 })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run')?.textContent).not.toContain('ETA');
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('0:10');
    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 6/10', progress: 0.65 })]));
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('ETA 0:40');
  });

  it('uses overall work for a rear-facing imported axial drive whose printed count restarts', async () => {
    publishOperation(operation());
    publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 1/4 of drive channel 2/2 (rear) with BEAT Engine', progress: 0.35 + 0.5 * 5 / 16 })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    await act(async () => { vi.advanceTimersByTime(10_000); });
    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 2/4 of drive channel 2/2 (rear) with BEAT Engine', progress: 0.35 + 0.5 * 6 / 16 })]));
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('frequency 2 of 4 · channel 2 of 2 · work 38%');
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('ETA 1:40');
  });

  it.each([
    {
      name: 'plain BEAT sweep',
      firstMessage: 'Solving frequency 1/4 with BEAT Engine',
      nextMessage: 'Solving frequency 2/4 with BEAT Engine',
      firstProgress: 0.4,
      setupProgress: [0.42, 0.44],
      nextProgress: 0.48,
      lastFrequency: 'frequency 1 of 4',
      nextFrequency: 'frequency 2 of 4 · ETA 0:40',
    },
    {
      name: 'imported multi-channel BEAT sweep',
      firstMessage: 'Solving frequency 4/8 of drive channel 1/3 (hf) with BEAT Engine',
      nextMessage: 'Solving frequency 2/8 of drive channel 2/3 (mf) with BEAT Engine',
      firstProgress: 0.35 + 0.5 * 7 / 24,
      setupProgress: [0.35 + 0.5 * 8 / 24, 0.35 + 0.5 * 9 / 24],
      nextProgress: 0.35 + 0.5 * 10 / 24,
      lastFrequency: 'frequency 4 of 8 · channel 1 of 3 · work 38%',
      nextFrequency: 'frequency 2 of 8 · channel 2 of 3 · work 42% · ETA 1:33',
    },
  ])('keeps $name progress through interleaved setup logs in the CAD solve card', async ({
    firstMessage, nextMessage, firstProgress, setupProgress, nextProgress, lastFrequency, nextFrequency,
  }) => {
    publishOperation(operation());
    const running = job({
      stage: 'solve', stage_message: firstMessage, progress: firstProgress,
      solve_options: { engine: 'beat', num_frequencies: 4 } as JobItem['solve_options'],
    });
    publishJobs([running]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Solving');

    await act(async () => { vi.advanceTimersByTime(4_000); });
    act(() => publishJobs([{
      ...running, stage: 'setup', stage_message: 'Setting up BEAT solver context', progress: setupProgress[0],
    }]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Solving');
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain(lastFrequency.split(' · work ')[0]);

    await act(async () => { vi.advanceTimersByTime(6_000); });
    act(() => publishJobs([{
      ...running, stage: 'setup', stage_message: 'Initializing BEAT sweep worker', progress: setupProgress[1],
    }]));
    const duringSetup = host.querySelector('.cad-solve-run')?.textContent ?? '';
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Solving');
    expect(duringSetup).toContain(lastFrequency.split(' · work ')[0]);
    if (lastFrequency.includes('work')) expect(duringSetup).toContain(lastFrequency);

    await act(async () => { vi.advanceTimersByTime(10_000); });
    act(() => publishJobs([{
      ...running, stage: 'solve', stage_message: nextMessage, progress: nextProgress,
    }]));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Solving');
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain(nextFrequency);
  });

  it('drops a finished job clock before a later solve sample with the same id', async () => {
    publishOperation(operation());
    publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 3/10', progress: 0.5 })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    await act(async () => { vi.advanceTimersByTime(10_000); });
    act(() => publishJobs([job({ status: 'complete', stage: 'postprocess', progress: 1 })]));
    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 5/10', progress: 0.6 })]));
    expect(host.querySelector('.cad-solve-run')?.textContent).not.toContain('ETA');
  });

  it('says a run is done once its job completes', async () => {
    publishOperation(operation());
    publishJobs([job({ status: 'complete', progress: 1, stage: 'postprocess', stage_message: null, completed_at: '2026-09-23T00:01:00Z' })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Done');
  });

  it('does not invent a reason for a cancelled job', async () => {
    publishOperation(operation());
    publishJobs([job({ status: 'cancelled', stage: 'cancelled', error_message: null })]);
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run')?.textContent).toBe('Cancelled');
    act(() => publishJobs([job({ status: 'cancelled', stage: 'cancelled', error_message: 'Simulation cancelled by user' })]));
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('Cancelled · Simulation cancelled by user');
  });

  it('stops assuming a missing job is about to appear once the accepted operation is stale', async () => {
    // Just accepted: the ordinary gap before the job arrives.
    publishOperation(operation({ updatedAt: NOW }));
    await act(async () => root.render(<CadSolveCard record={record()} label="PartyMEH"/>));
    expect(host.querySelector('.cad-solve-run .job-stage-word')?.textContent).toBe('Preparing mesh');

    // The same operation, accepted well over the grace window ago, and still
    // no matching job -- as a reload might find. No job is ever published in
    // this test.
    act(() => publishOperation(operation({ updatedAt: '2026-09-22T23:59:00Z' })));
    const text = host.querySelector('.cad-solve-run')?.textContent ?? '';
    expect(text).not.toBe('Preparing mesh');
    expect(text.toLowerCase()).toContain("isn't showing in the jobs list");
  });
});
