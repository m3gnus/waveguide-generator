/**
 * Model details (PLAN.md U4) says what the model is and what Solve uses; the
 * model's state and its one Solve are its status line (`CadModelStatusLine`). A run in progress is
 * reported in Results (`CurrentModelStatus`) through the same progress model the
 * parametric run list uses (`SolveProgressView`/`operationStageWord`/
 * `solveStageWord`, `./solveProgress`) -- not a CAD-only copy of the
 * stage/percentage text, and not two separate vocabularies either side of
 * the moment a job appears. A job here is indistinguishable in origin from
 * one submitted locally: the card reads only the operation's own state (or,
 * once accepted, its `jobId` and the matching `JobItem`), so a request that
 * reached any given point by any path -- including one a waiting Fusion
 * request advanced -- renders identically.
 */
import { preferencesStore } from '../prefs/preferences';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadJobFixture } from '../jobs/cadSolve.fixtures';
import { jobsSocket, JobsSocketManager, type JobsWebSocketLike, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { RECOVERED_NEGATIVE_HALF } from '../api/domainDecision.fixtures';
import { resetCadReturnStore } from '../stores/cadReturn';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { resetSolveStageClocksForTests } from './solveProgress';

vi.mock('../jobs/useImportedSolvePlan', () => ({ useImportedSolvePlan: () => ({ plan: null }) }));

const { CadModelDetails } = await import('./CadModelDetails');
const { CurrentModelStatus } = await import('./CurrentModelStatus');

/** The Results status of the model the card shows. */
const Run = () => <CurrentModelStatus record={record()} solveStatus/>;

const MANIFEST = `sha256:${'c'.repeat(64)}`;
const NOW = '2026-09-23T00:00:00Z';

function record(overrides: Partial<CadReturnIngestRecord> = {}): CadReturnIngestRecord {
  return {
    ingest_id: 'wgi_run', manifest_sha256: MANIFEST, artifact_sha256: 'artifact', report_sha256: 'report',
    findings: [], evidence: { fem_air_volumes: [] },
    polar_grid_derivation: { axes: { horizontal: { symmetry_accepted: true } } },
    scope: { status: 'clean', included: [{ object_id: 'body-1', name: 'Body79' }] },
    sources: [{ id: 'source-hf', role: 'HF' }],
    freshness: { verdict: 'per-instance', instances: [{ instance_id: 'instance-a', verdict: 'current' }] },
    symmetry: { cut_planes: ['x0', 'y0'], planes: { x0: { accepted: true, max_residual_step_units: 0.0028 }, y0: { accepted: true } } },
    healing: { performed: false },
    mesh_snapshot: {
      wall_size_mm: 25, grading_distance_mm: 100, symmetry: 'auto', source_size_mm: { 'source-hf': 6 },
      surface_groups: [], settings_origin: 'user',
    },
    mesh: { stats: { triangle_count: 3035 } },
    sizing_estimate: { n_triangles: 3035, ram_gb: 0.147, ram_bytes: 147379600, solve_seconds_cubic_upper_per_freq: 0.026, feasibility: 'ok' },
    ...overrides,
  } as unknown as CadReturnIngestRecord;
}

/** The details as the Imported mesh tab shows them, for a prepared model unless said. */
const Details = ({ value = record(), prepared = true, fetcher }: { value?: CadReturnIngestRecord; prepared?: boolean; fetcher?: typeof fetch }) =>
  <CadModelDetails record={value} label="PartyMEH" prepared={prepared} fetcher={fetcher}/>;

const head = () => document.querySelector<HTMLButtonElement>('.cad-model-details .section-head')!;
const body = () => document.querySelector<HTMLElement>('.cad-model-details .section-body')!;

/** Words a user never reads in Model details without developer details. */
const JARGON = ['$.', 'bundle', 'validation', 'wgi_', 'sha256', 'ingest', 'manifest', 'snapshot', 'Snapshot', 'Preparation',
  'cut plane', 'residual', 'ram bytes', 'cubic', 'instance-a', 'physical group', 'automatic axis', 'Prepare simulation'];
function expectPlain(text: string | null | undefined) {
  for (const word of JARGON) expect(text ?? '').not.toContain(word);
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

let currentOperation: CadOperationSummary | null = null;
function publishJobs(jobs: JobItem[]): void {
  jobs = jobs.map((item) => currentOperation ? { ...cadJobFixture(currentOperation), ...item, cad_state: cadJobFixture(currentOperation).cad_state } : item);
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function publishOperation(op: CadOperationSummary): void {
  currentOperation = op;
  const existing = jobsSocket.getSnapshot().jobs.find((job) => job.id === op.jobId);
  const item = { ...cadJobFixture(op), ...(existing ?? {}), cad_state: cadJobFixture(op).cad_state };
  publishJobs([item]);
}

describe('Model details, and the run status Results shows for its model', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    currentOperation = null;
    publishJobs([]);

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
    preferencesStore.resetForTests();
    host.remove();
    publishJobs([]);
    currentOperation = null;
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('folds the model into one collapsed Model details, in plain words, and leaves Solve to the status line', async () => {
    await act(async () => root.render(<Details/>));
    expect(head().textContent).toContain('Model details');
    expect(head().getAttribute('aria-expanded')).toBe('false');
    // Nothing needs the user: no chip, and the body is folded away.
    expect(head().querySelector('.cad-state-chip')).toBeNull();
    expect(body().hidden).toBe(true);
    act(() => head().click());
    expect(body().hidden).toBe(false);
    expect(host.querySelector('.cad-solve-summary')?.textContent).toBe('Body79 · 1 source (HF) · full model, WG mirrors it at x = 0 and y = 0');
    expect(host.querySelector('.cad-mesh-line')?.textContent).toContain('Mesh from Fusion · walls 25 mm · HF 6 mm · 3,035 triangles · about 0.1 GB');
    expect(host.querySelector('.cad-mesh-line')?.textContent).toContain('Change it in Fusion: Waveguide Generator ▸ Sources & Mesh…');
    expect(host.querySelector('.cad-solve-settings')).not.toBeNull();
    // Every check passed: one line, not one row per check.
    expect([...host.querySelectorAll('.cad-checks > .cad-check')].map((row) => row.textContent)).toEqual(['✓Checkspassed']);
    expect(host.querySelector('[data-action="solve"]')).toBeNull();
    expect(host.querySelector('[data-model-status]')).toBeNull();
    expect(host.textContent).not.toContain('Not solved yet');
    expectPlain(host.textContent);
  });

  it('keeps what the model is, and drops what Solve would use, for a model no longer prepared', async () => {
    await act(async () => root.render(<Details prepared={false}/>));
    expect(host.querySelector('.cad-solve-summary')).not.toBeNull();
    expect(host.querySelector('.cad-mesh-line')).not.toBeNull();
    expect(host.querySelector('.cad-checks')).not.toBeNull();
    expect(host.querySelector('.cad-solve-settings')).toBeNull();
    expect(host.querySelector('.cad-solver-frame')).toBeNull();
  });

  it('opens by itself on a check that needs the user, with only the checks that deviate', async () => {
    await act(async () => root.render(<Details value={record({
      scope: { status: 'degraded', degraded_skip_count: 1, included: [], skipped: [{ object_id: 'b', name: 'Body A', severity: 'warning', reason: 'unsupported' }] },
    } as never)}/>));
    expect(head().getAttribute('aria-expanded')).toBe('true');
    expect(head().querySelector('.cad-state-chip')?.textContent).toBe('1 need attention');
    expect(body().hidden).toBe(false);
    const rows = [...host.querySelectorAll('.cad-checks > .cad-check')];
    expect(rows.map((row) => row.querySelector('b')?.textContent)).toEqual(['Checks', 'Scope']);
    expect(rows[0].textContent).toContain('the others passed');
    expect(rows[1].textContent).toContain('Skipped · Body A');
    expectPlain(host.textContent);
  });

  it('says the mesh is near its limit beside its size, and leaves the sizing numbers to developer details', async () => {
    await act(async () => root.render(<Details value={record({ sizing_estimate: { n_triangles: 3035, ram_gb: 0.147, feasibility: 'caution' } } as never)}/>));
    expect(host.querySelector('.cad-mesh-line')?.className).toContain('cad-mesh-line-caution');
    expect(host.querySelector('.cad-mesh-line')?.textContent).toContain('about 0.1 GB · caution');
    expect(host.querySelector('.developer-details')).toBeNull();
  });

  it('names a mesh WG set without sending the user to Fusion', async () => {
    await act(async () => root.render(<Details value={record({
      mesh_snapshot: undefined, mesh_sizes: { rigid_size_mm: 8, transition_mm: 20, source_size_mm: { 'source-hf': 4 } },
    } as never)}/>));
    const line = host.querySelector('.cad-mesh-line')!;
    expect(line.getAttribute('data-mesh-origin')).toBe('wg');
    expect(line.textContent).toContain('Mesh · walls 8 mm · HF 4 mm · 3,035 triangles');
    expect(line.textContent).not.toContain('Fusion');
  });

  it('never rounds a small mesh to no memory at all', async () => {
    await act(async () => root.render(<Details value={record({ sizing_estimate: { n_triangles: 738, ram_gb: 0.004 } } as never)}/>));
    expect(host.querySelector('.cad-mesh-line')?.textContent).toContain('3,035 triangles · under 0.1 GB');
  });

  it('opens by itself when the model cannot be solved along its own axis, and draws no preview before Change', async () => {
    const frame = {
      linked: false, ingestId: 'wgi_run', contract: 'cad-solver-frame-v2', recordAxis: '+z', recordStatesFrame: true,
      confirmed: null, suggestion: null, preselected: null, differs: null,
      axes: [{ axis: '+z', allowed: true, reason: null, up: [0, 1, 0], upSource: 'default', solverFromAssembly: [], previewFromRecord: [] }],
      notAllowed: { axis: '+y', message: 'This model arrived cut in a way that cannot be solved along its radiation axis (+y).', nextStep: 'Send the whole model from Fusion.' },
    };
    const fetcher = vi.fn(async (input: RequestInfo | URL) => new Response(JSON.stringify(
      String(input).includes('/solver-frame') ? frame : { ingestId: 'wgi_run', available: true, pending: null },
    ), { status: 200 })) as unknown as typeof fetch;
    await act(async () => root.render(<Details value={record({ freshness: { verdict: 'unlinked', instances: [] } } as never)} fetcher={fetcher}/>));
    await act(async () => { await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull()); });
    expect(head().getAttribute('aria-expanded')).toBe('true');
    expect(host.querySelector('.cad-solver-frame-line')?.textContent).toBe('Radiates along +y · Change');
    expect(host.querySelectorAll('[data-action="change-solver-frame"]')).toHaveLength(1);
    expect(host.querySelector('.cad-frame-views')).toBeNull();
    // The preview needs the model's mesh, which is read only for the chooser.
    expect(vi.mocked(fetcher).mock.calls.some(([url]) => String(url).endsWith('/mesh'))).toBe(false);
  });

  it('shows every check with its evidence, the sizing numbers and the ids only with developer details', async () => {
    preferencesStore.update({ showDeveloperDetails: true });
    await act(async () => root.render(<Details/>));
    const names = [...host.querySelectorAll('.cad-checks > .cad-check')].map((row) => row.querySelector('b')?.textContent);
    expect(names).toEqual(['Scope', 'Freshness', 'Symmetry', 'Healing', 'Polar grid']);
    expect(host.textContent).toContain('max residual 0.0028 mm');
    expect(host.textContent).toContain('ram bytes147379600');
    expect(host.querySelector('.cad-model-provenance')?.textContent).toContain('Preparation wgi_run');
  });

  it('shows meshing and an update restart hold from real stage events', async () => {
    const socket: JobsWebSocketLike = {
      readyState: 1, onopen: null, onmessage: null, onerror: null, onclose: null,
      send: vi.fn(), close: vi.fn(),
    };
    const received = { ...job(), ...cadJobFixture(operation({ state: 'received', stage: 'received' })), stage: 'received' };
    const meshing = { ...received, stage: 'preparing-mesh', stage_message: 'Meshing the surface',
      cad_state: { ...received.cad_state!, state: 'processing', stage: 'preparing-mesh' } };
    const held = { ...meshing, stage: 'waiting-for-update-restart', stage_message: 'Restart approved',
      cad_state: { ...meshing.cad_state!, state: 'needs_user_input', reason: 'update_restart_pending', message: 'Restart approved' } };
    let response = meshing;
    const fetcher = vi.fn(async () => new Response(JSON.stringify(response), { headers: { 'Content-Type': 'application/json' } }));
    const manager = new JobsSocketManager(() => socket, fetcher, 'ws://test/ws/jobs');
    vi.spyOn(jobsSocket, 'getSnapshot').mockImplementation(manager.getSnapshot);
    vi.spyOn(jobsSocket, 'subscribe').mockImplementation(manager.subscribe);
    const deliver = (value: unknown) => socket.onmessage?.({ data: JSON.stringify(value) });
    manager.start();
    try {
      deliver({ v: 1, kind: 'hello', epoch: 1, heartbeatSec: 15 });
      deliver({ v: 1, kind: 'snapshot', epoch: 1, cursor: 1, jobs: [received] });
      expect(manager.getSnapshot().error).toBeNull();
      await act(async () => root.render(<Run/>));
      expect(host.querySelector('.job-stage-word')?.textContent).toBe('Received');
      await act(async () => {
        deliver({ v: 1, kind: 'event', epoch: 1, cursor: 2, jobId: received.id, type: 'stage', payload: { stage: 'preparing-mesh', message: 'Meshing the surface' } });
      });
      await vi.waitFor(() => expect(host.querySelector('.job-stage-word')?.textContent).toBe('Preparing mesh'));
      response = held;
      await act(async () => {
        deliver({ v: 1, kind: 'event', epoch: 1, cursor: 3, jobId: received.id, type: 'stage', payload: { stage: 'waiting-for-update-restart', message: 'Restart approved' } });
      });
      await vi.waitFor(() => expect(host.textContent).toContain('held for the update restart'));
      expect(manager.getSnapshot().jobs[0].cad_state?.reason).toBe('update_restart_pending');
      expect(fetcher).toHaveBeenCalledTimes(2);
    } finally { manager.stop(); }
  });

  it('walks the CAD operation pipeline and the job it hands off to as one monotonic sequence', async () => {
    // received, still validating: "Received".
    publishOperation(operation({ state: 'received', stage: 'validating', jobId: null }));
    await act(async () => root.render(<Run/>));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Received');

    // preparation.py's "preparing-mesh" stage: "Preparing mesh" -- never back
    // to "Received".
    act(() => publishOperation(operation({ state: 'processing', stage: 'preparing-mesh', jobId: null })));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Preparing mesh');

    // ready to submit, still no job: preparation has not stepped back.
    act(() => publishOperation(operation({ state: 'processing', stage: 'ready', jobId: null })));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Preparing mesh');

    // Accepted, job not in the list yet yet (within the grace window): says
    // so plainly, not a made-up stage.
    act(() => publishOperation(operation({ state: 'accepted', stage: 'submitted' })));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Preparing mesh');

    // The queued window continues mesh preparation.
    act(() => publishJobs([job({ status: 'queued', stage: null, stage_message: null })]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Preparing mesh');

    act(() => publishJobs([job({ status: 'running', stage: 'initializing', stage_message: 'Initializing solver', progress: 0.05 })]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Preparing mesh');

    // The runtime emits mesh after initializing; both stay in mesh preparation.
    act(() => publishJobs([job({ status: 'running', stage: 'mesh', stage_message: 'Building the surface mesh', progress: 0.1 })]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Preparing mesh');

    act(() => publishJobs([job({ stage: 'assemble', stage_message: 'Configuring Metal BEM solve', progress: 0.3 })]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Starting…');

    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 2/8 with Metal BEM', frequency_progress: { done: 2, total: 8 }, progress: 0.3 })]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Solving');
    expect(host.querySelector('.current-model-status')?.textContent).toContain('frequency 2 of 8');

    act(() => publishJobs([job({ stage: 'postprocess', stage_message: 'Packaging BEMPP BEM solver results', progress: 0.95 })]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Combining');
  });

  it('never shows a previous run\'s outcome under a new request for the same snapshot', async () => {
    // The first request finished and its job completed.
    publishOperation(operation({ operationId: 'op-old', state: 'accepted', jobId: 'job-old', updatedAt: '2026-09-22T23:00:00Z' }));
    publishJobs([job({ id: 'job-old', status: 'complete', progress: 1, completed_at: '2026-09-22T23:01:00Z' })]);
    await act(async () => root.render(<Run/>));

    // A second, newer request for the same snapshot starts -- still being
    // validated, no job of its own yet. Only op-old's operation store entry
    // is replaced (a real re-solve creates a new operation id); the old
    // job is still in the jobs list, exactly as it would be after a reload.
    act(() => publishOperation(operation({ operationId: 'op-new', state: 'received', stage: 'validating', jobId: null, createdAt: NOW, updatedAt: NOW })));
    expect(host.querySelector('.current-model-status')?.textContent).not.toContain('Solved');
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Received');
  });

  it('reports a running solve as it stands, whether pressed here or advanced by a waiting request', async () => {
    // Nothing in this render distinguishes "this browser pressed Solve" from
    // "a waiting request for this snapshot was advanced elsewhere": the card
    // reads only the operation's jobId and that job's own state.
    publishOperation(operation({ jobId: 'job-elsewhere' }));
    publishJobs([job({ id: 'job-elsewhere', stage: 'postprocess', stage_message: 'Packaging BEMPP BEM solver results', progress: 0.95 })]);
    await act(async () => root.render(<Run/>));
    const text = host.querySelector('.current-model-status')?.textContent ?? '';
    expect(text).toContain('Combining');
    expect(text).toContain('95%');
  });

  it('shows the same elapsed time, engine/sources/domain detail and ETA the run list shows, in its compact layout', async () => {
    publishOperation(operation());
    publishJobs([job({
      stage: 'solve', stage_message: 'Solving frequency 1/10 with Metal BEM', frequency_progress: { done: 1, total: 10 }, progress: 0.3,
    })]);
    await act(async () => root.render(<Run/>));
    expect(host.querySelector('.current-model-status')?.textContent).toContain('frequency 1 of 10');
    // Elapsed since the job started, at 0 s here.
    expect(host.querySelector('.current-model-status')?.textContent).toContain('0:00');
    // Engine/sources/domain, the same detail line the full variant shows.
    expect(host.querySelector('.current-model-status')?.textContent).toContain('Metal-BEM · 3 sources · half');

    await act(async () => { vi.advanceTimersByTime(20_000); });
    act(() => publishJobs([job({
      stage: 'solve', stage_message: 'Solving frequency 2/10 with Metal BEM', frequency_progress: { done: 2, total: 10 }, progress: 0.4,
    })]));
    const text = host.querySelector('.current-model-status')?.textContent ?? '';
    expect(text).toContain('frequency 2 of 10');
    expect(text).toContain('ETA 2:40');
  });

  it('ticks elapsed time without a jobs update and measures ETA from a late first frequency', async () => {
    publishOperation(operation());
    publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 5/10', frequency_progress: { done: 5, total: 10 }, progress: 0.6 })]);
    await act(async () => root.render(<Run/>));
    expect(host.querySelector('.current-model-status')?.textContent).not.toContain('ETA');
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(host.querySelector('.current-model-status')?.textContent).toContain('0:10');
    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 6/10', frequency_progress: { done: 6, total: 10 }, progress: 0.65 })]));
    expect(host.querySelector('.current-model-status')?.textContent).toContain('ETA 0:40');
  });

  it('uses overall work for a rear-facing imported axial drive whose printed count restarts', async () => {
    publishOperation(operation());
    publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 1/4 of drive channel 2/2 (rear) with BEAT Engine', frequency_progress: { done: 1, total: 4, channel: { index: 2, count: 2 } }, progress: 0.35 + 0.5 * 5 / 16 })]);
    await act(async () => root.render(<Run/>));
    await act(async () => { vi.advanceTimersByTime(10_000); });
    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 2/4 of drive channel 2/2 (rear) with BEAT Engine', frequency_progress: { done: 2, total: 4, channel: { index: 2, count: 2 } }, progress: 0.35 + 0.5 * 6 / 16 })]));
    expect(host.querySelector('.current-model-status')?.textContent).toContain('frequency 2 of 4 · channel 2 of 2 · work 38%');
    expect(host.querySelector('.current-model-status')?.textContent).toContain('ETA 1:40');
  });

  it.each([
    {
      name: 'plain BEAT sweep',
      firstMessage: 'Solving frequency 1/4 with BEAT Engine',
      firstCount: { done: 1, total: 4 },
      nextMessage: 'Solving frequency 2/4 with BEAT Engine',
      nextCount: { done: 2, total: 4 },
      firstProgress: 0.4,
      setupProgress: [0.42, 0.44],
      nextProgress: 0.48,
      lastFrequency: 'frequency 1 of 4',
      nextFrequency: 'frequency 2 of 4 · ETA 0:40',
    },
    {
      name: 'imported multi-channel BEAT sweep',
      firstMessage: 'Solving frequency 4/8 of drive channel 1/3 (hf) with BEAT Engine',
      firstCount: { done: 4, total: 8, channel: { index: 1, count: 3 } },
      nextMessage: 'Solving frequency 2/8 of drive channel 2/3 (mf) with BEAT Engine',
      nextCount: { done: 2, total: 8, channel: { index: 2, count: 3 } },
      firstProgress: 0.35 + 0.5 * 7 / 24,
      setupProgress: [0.35 + 0.5 * 8 / 24, 0.35 + 0.5 * 9 / 24],
      nextProgress: 0.35 + 0.5 * 10 / 24,
      lastFrequency: 'frequency 4 of 8 · channel 1 of 3 · work 38%',
      nextFrequency: 'frequency 2 of 8 · channel 2 of 3 · work 42% · ETA 1:33',
    },
  ])('keeps $name progress through interleaved setup logs in the CAD run status', async ({
    firstMessage, nextMessage, firstCount, nextCount, firstProgress, setupProgress, nextProgress, lastFrequency, nextFrequency,
  }) => {
    publishOperation(operation());
    const running = job({
      stage: 'solve', stage_message: firstMessage, frequency_progress: firstCount, progress: firstProgress,
      solve_options: { engine: 'beat', num_frequencies: 4 } as JobItem['solve_options'],
    });
    publishJobs([running]);
    await act(async () => root.render(<Run/>));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Solving');

    await act(async () => { vi.advanceTimersByTime(4_000); });
    act(() => publishJobs([{
      ...running, stage: 'setup', stage_message: 'Setting up BEAT solver context', progress: setupProgress[0],
    }]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Solving');
    expect(host.querySelector('.current-model-status')?.textContent).toContain(lastFrequency.split(' · work ')[0]);

    await act(async () => { vi.advanceTimersByTime(6_000); });
    act(() => publishJobs([{
      ...running, stage: 'setup', stage_message: 'Initializing BEAT sweep worker', progress: setupProgress[1],
    }]));
    const duringSetup = host.querySelector('.current-model-status')?.textContent ?? '';
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Solving');
    expect(duringSetup).toContain(lastFrequency.split(' · work ')[0]);
    if (lastFrequency.includes('work')) expect(duringSetup).toContain(lastFrequency);

    await act(async () => { vi.advanceTimersByTime(10_000); });
    act(() => publishJobs([{
      ...running, stage: 'solve', stage_message: nextMessage, frequency_progress: nextCount, progress: nextProgress,
    }]));
    expect(host.querySelector('.current-model-status .job-stage-word')?.textContent).toBe('Solving');
    expect(host.querySelector('.current-model-status')?.textContent).toContain(nextFrequency);
  });

  it('drops a finished job clock before a later solve sample with the same id', async () => {
    publishOperation(operation());
    publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 3/10', frequency_progress: { done: 3, total: 10 }, progress: 0.5 })]);
    await act(async () => root.render(<Run/>));
    await act(async () => { vi.advanceTimersByTime(10_000); });
    act(() => publishJobs([job({ status: 'complete', stage: 'postprocess', progress: 1 })]));
    act(() => publishJobs([job({ stage: 'solve', stage_message: 'Solving frequency 5/10', frequency_progress: { done: 5, total: 10 }, progress: 0.6 })]));
    expect(host.querySelector('.current-model-status')?.textContent).not.toContain('ETA');
  });

  it('shows the imported instance\'s engine and sweep, not the parametric ones', async () => {
    useSolveOptionsStore.getState().setEngine('bempp');
    useImportedSolveOptionsStore.getState().setFrequencyMode('list');
    useImportedSolveOptionsStore.getState().setFrequencyListText('300 700 1500');
    await act(async () => root.render(<Details/>));
    const line = host.querySelector('.cad-solve-settings')?.textContent ?? '';
    expect(line).toContain('3 listed frequencies');
    expect(line).toContain('Auto');
    expect(line).not.toContain('BEMPP');
  });

  it('says when WG solved the model with its default settings, and where to change them', async () => {
    const note = "Solved with WG's default settings \u2014 change them in WG.";
    publishOperation(operation({ message: note, setupDefaults: true }));
    publishJobs([job({ status: 'complete', progress: 1, stage: 'postprocess', stage_message: null, completed_at: '2026-09-23T00:01:00Z' })]);
    await act(async () => root.render(<Details/>));
    expect(host.querySelector('.cad-solve-defaults')?.textContent).toBe(note);
  });

  it('says "Using" WG\'s default settings while the run is going, or after it failed', async () => {
    const note = "Solved with WG's default settings \u2014 change them in WG.";
    publishOperation(operation({ message: note, setupDefaults: true }));
    publishJobs([job()]);
    await act(async () => root.render(<Details/>));
    expect(host.querySelector('.cad-solve-defaults')?.textContent)
      .toBe("Using WG's default settings \u2014 change them in WG.");
    act(() => publishJobs([job({ status: 'error', error_message: 'solver crashed' })]));
    expect(host.querySelector('.cad-solve-defaults')?.textContent)
      .toBe("Using WG's default settings \u2014 change them in WG.");
  });

  it('says nothing about defaults for a run solved with settings someone chose', async () => {
    publishOperation(operation({ setupDefaults: false }));
    publishJobs([job({ status: 'complete', progress: 1, stage: 'postprocess', stage_message: null, completed_at: '2026-09-23T00:01:00Z' })]);
    await act(async () => root.render(<Details/>));
    expect(host.querySelector('.cad-solve-defaults')).toBeNull();
  });

  it('names no axis outside its one line in Model details: no run line with its own Change', async () => {
    publishOperation(operation({ frameAxisAutomatic: '+x' }));
    publishJobs([job({ status: 'complete', progress: 1, stage: 'postprocess', stage_message: null, completed_at: '2026-09-23T00:01:00Z' })]);
    await act(async () => root.render(<Details/>));
    expect(host.textContent).not.toContain('automatic axis');
    expect(host.querySelectorAll('[data-action="change-automatic-frame"]')).toHaveLength(0);
  });

  it('states the concluded domain from the record\'s decision in Model details', async () => {
    const interpretation = {
      contract: 'cad-domain-interpretation-v1', reading: 'reduced', planes: ['x0'], domain_planes: ['x0'],
      looks_cut: [], ambiguous: [], evidence: { source: null, recovered: true }, reflected_planes: ['x0'], choices: [],
    };
    const fetcher = (async () => new Response(JSON.stringify({ ingestId: 'wgi_run', available: true, pending: null }), { status: 200 })) as typeof fetch;
    await act(async () => root.render(<Details
      value={{ ...record(), domain_interpretation: interpretation, domain_decision: RECOVERED_NEGATIVE_HALF } as CadReturnIngestRecord}
      fetcher={fetcher}
    />));
    expect(host.querySelector('.cad-domain-line')?.textContent)
      .toBe('Cut in CAD at x = 0 — recovered by mirroring (the x ≤ 0 side reflected)');
    expect(host.querySelector('[data-domain-input="cut"]')).not.toBeNull();
  });
});
