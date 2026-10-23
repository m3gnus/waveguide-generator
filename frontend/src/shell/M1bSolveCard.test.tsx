import type { ModelSolvePress } from '../jobs/actions';
/**
 * PLAN.md M1b ("One Solve card") with M1e's automatic frame, end to end in the
 * application shell: the model card's one Solve captures the snapshot,
 * settings, frame and domain on screen, remembers the settings for the
 * project, confirms the frame shown, advances one durable operation -- a
 * waiting Fusion request for this snapshot is continued, never duplicated --
 * and ends at that run's results.
 *
 * The operation store, the frame store, the submission path, the Solve card
 * and the Results panel are the real ones; only the HTTP edge and the dock are
 * stood in for, with the production response shapes (`GET/PUT
 * /api/cadlink/solver-frame` as server/cadlink/interpretation/solver_frame.py answers).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadJobFixture, publishCadSummary } from '../jobs/cadSolve.fixtures';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary } from '../api/cadOperations';
import type { SolverFrameState } from '../api/solverFrame';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import type { CadReturnIngestRecord } from '../api/cadlink';
import { combineSpecEffective, resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetCadSolverFrameStore, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useImportedSolveOptionsStore } from '../stores/solveOptions';
import { expandLegacy, withPair } from '../results/crossoverSpec';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import fixtureV2 from '../viewport/solverFrame.v2.fixture.json';
import { SOLVER_FRAME_AXES, type SolverFrameAxis } from '../viewport/solverFrame';
import { JobsCoordinator, jobsCoordinatorBridge, solveControlStore } from './JobsCoordinator';
import { ResultsPanel } from './ResultsPanel';
import { SolveActions } from './TopBar';
import { CadSolveCard } from './CadSolveCard';
import { CadOperationsSection } from './CadOperationsSection';
import { solvedCadModels } from './cadlink/solvedModel';
import { resetSolveAttentionForTests, solveAttention } from './solveAttention';
import {
  bindWorkspaceNavigation,
  publishVisiblePanels,
  resetWorkspaceNavigationForTests,
  type WorkspacePanel,
} from './workspaceNavigation';

const mocks = vi.hoisted(() => ({
  solveModel: vi.fn(),
}));

vi.mock('../jobs/actions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/actions')>();
  return { ...actual, solveModel: mocks.solveModel };
});

/** The n-th press WG sent (POST /api/models/{id}/solve): [model, body]. */
const pressOf = (index = 0) => mocks.solveModel.mock.calls[index][1] as ModelSolvePress;
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({
    engines: [{ name: 'metal', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] }],
    engineSelection: { default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal'] },
    error: null,
    isLoading: false,
  }),
  useCapabilityRefreshOnReconnect: () => undefined,
  useLegacyBeatEngineMigration: () => undefined,
}));
vi.mock('../jobs/useSolvePlan', () => ({
  useSolvePlan: () => ({ plan: null, error: null, isPending: false }),
}));
vi.mock('../jobs/useImportedSolvePlan', () => ({
  useImportedSolvePlan: (enabled: boolean) => (enabled ? {
    plan: {
      ingest_id: 'wgi_first', requested: 'auto', engine: 'metal', code: null,
      reason: 'AUTO selected the first available engine', domain: 'full',
      engines: [{ name: 'metal', label: 'Metal', solves: true }, { name: 'bempp', label: 'BEMPP', solves: true }],
    },
    error: null,
    isPending: false,
  } : { plan: null, error: null, isPending: false }),
}));

const MANIFEST = `sha256:${'1'.repeat(64)}`;
const SECOND = `sha256:${'4'.repeat(64)}`;
const MSH = [
  '$MeshFormat', '2.2 0 8', '$EndMeshFormat',
  '$Nodes', '3', '1 0 0 0', '2 1 0 0', '3 0 1 0', '$EndNodes',
  '$Elements', '1', '1 2 2 1 1 1 2 3', '$EndElements',
].join('\n');

/** `GET /api/cadlink/solver-frame` as the server answers it for a v2 record meshed as modelled. */
function frameAnswer(options: {
  confirmed?: SolverFrameAxis | null;
  preselected?: SolverFrameState['preselected'];
  suggestion?: SolverFrameState['suggestion'];
  differs?: SolverFrameState['differs'];
  allowed?: readonly SolverFrameAxis[];
} = {}): SolverFrameState {
  const allowed = options.allowed ?? SOLVER_FRAME_AXES;
  return {
    linked: false,
    ingestId: 'wgi_first',
    contract: 'cad-solver-frame-v2',
    requirement: { contract: 'cad-solver-frame-v2', export_frame: 'root-component', document_up: null },
    recordAxis: '+z',
    recordStatesFrame: true,
    confirmed: options.confirmed ? { axis: options.confirmed, confirmedAt: '2026-09-22T10:00:00Z', frame: null } : null,
    axes: SOLVER_FRAME_AXES.map((axis) => ({
      axis,
      allowed: allowed.includes(axis),
      reason: allowed.includes(axis) ? null : 'a half or quarter model is solved only as modelled (+z)',
      up: fixtureV2.up[axis],
      upSource: 'default',
      solverFromAssembly: fixtureV2.axes[axis],
      previewFromRecord: fixtureV2.axes[axis],
    })),
    suggestion: options.suggestion ?? null,
    preselected: options.preselected ?? null,
    differs: options.differs ?? null,
  };
}

const automatic = (axis: SolverFrameAxis): SolverFrameState['suggestion'] => ({
  status: 'automatic', axis, confidence: 0.8, reason: `Radiates along ${axis}: the source normals and the visibility survey agree.`,
  reasonCode: 'inferred', algorithm: 'frame-infer-v1', snapshotSha256: MANIFEST,
});

const asking: SolverFrameState['suggestion'] = {
  status: 'ask', axis: null, confidence: 0,
  reason: 'The sources face different ways, so WG cannot tell which way the model radiates. Pick the front.',
  reasonCode: 'conflicting-evidence', algorithm: 'frame-infer-v1', snapshotSha256: MANIFEST,
};

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function cadJob(id: string, status: JobItem['status'] = 'complete', extra: Partial<JobItem> = {}): JobItem {
  return {
    id, run_number: 1, parent_job_id: null, label: id, status, progress: status === 'complete' ? 1 : 0.4,
    stage: null, stage_message: null, created_at: '2026-09-22T00:00:00Z',
    queued_at: '2026-09-22T00:00:00Z', started_at: null, completed_at: status === 'complete' ? '2026-09-22T00:00:01Z' : null,
    config_summary: { geometry_type: 'imported' }, solve_options: {} as JobItem['solve_options'],
    has_results: status === 'complete', has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null,
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
    cad_source: {
      ingest_id: 'wgi_first', design_id: null, lineage_id: 'wgl_test', archive_stem: null,
      manifest_sha256: MANIFEST, document_name: 'Speaker', return_state_hash: null,
    },
    ...extra,
  } as JobItem;
}

function operation(operationId: string, state = 'received', overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId, kind: 'prepare_and_solve', state, stage: 'received', reason: null, message: null,
    jobId: null, attemptGeneration: 0, setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: MANIFEST, documentName: 'Speaker', projectLineageId: 'wgl_test' }, legacy: false,
    createdAt: '2026-09-22T10:00:00Z', updatedAt: '2026-09-22T10:00:00Z',
    ...overrides,
  };
}


function readyCad(): CadReturnIngestRecord {
  const record = {
    ingest_id: 'wgi_first',
    created_at: '2026-09-22T10:00:00Z',
    manifest_sha256: MANIFEST,
    artifact_sha256: `sha256:${'2'.repeat(64)}`,
    report_sha256: `sha256:${'3'.repeat(64)}`,
    freshness: { verdict: 'unlinked', instances: [] },
    project: { lineage_id: 'wgl_test', design_id: null, document_native_id: 'urn:doc', document_name: 'Speaker', archive_stem: null },
    scope: { status: 'clean', degraded_skip_count: 0, included: [{ object_id: 'b1', name: 'Body1' }] },
    sources: [{ id: 'source-hf', role: 'HF', required: true, instance_id: null, default_drive_channel_id: 'drive-hf', suggested_resolution_mm: 4 }],
    symmetry: { planes: {}, cut_planes: ['x0'], domain_planes: ['x0'] },
    sizing_estimate: { measured: { solve_seconds_per_freq: 7.5 } },
    findings: [], evidence: { fem_air_volumes: [] }, polar_grid_derivation: {},
  } as unknown as CadReturnIngestRecord;
  useCadReturnStore.setState({
    selectedBundle: {
      name: 'speaker.wgreturn', bundlePath: 'returns/speaker.wgreturn', modifiedAt: '2026-09-22T10:00:00Z',
      readable: true, documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 0,
      designIds: [], sources: [{ id: 'source-hf', role: 'HF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' }],
    },
    projectLineageId: 'wgl_test', ingestRecord: record, needsIngest: false,
    driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
    sourceSizesMm: { 'source-hf': 4 }, rigidSizeMm: 8, transitionMm: 12, skippedSourceIds: [],
  });
  importedMeshStore.setCad({ name: 'Speaker', source: 'cad', ingestId: 'wgi_first' } as ImportedMeshScene);
  return record;
}

function resultResponse(): Response {
  const digest = 'a'.repeat(64);
  return new Response(JSON.stringify({
    result_kind: 'parametric', result_contract_version: 1, client_request_id: null, client_metadata: {},
    provenance: {
      schema_version: 1, wg_version: 'test', dependency_shas: {}, request_sha256: digest,
      geometry_sha256: digest, solve_options_sha256: digest, request_identity: 'execution',
      execution_request_sha256: digest, execution_geometry_sha256: digest, execution_solve_options_sha256: digest,
      effective_request_sha256: digest, effective_geometry_sha256: digest, effective_solve_options_sha256: digest,
      resolved_engine: 'test',
    },
    frequencies: [100, 200], metadata: {},
  }), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json' },
});

const flush = async (times = 4) => {
  for (let index = 0; index < times; index += 1) await Promise.resolve();
};

describe('M1b: one Solve card, to the revealed result', () => {
  let host: HTMLDivElement;
  let root: Root;
  let activations: WorkspacePanel[];
  let visible: Set<WorkspacePanel>;
  /** What `GET /solver-frame` answers now; a confirmation updates it as the server does. */
  let frame: SolverFrameState;
  /** Every `PUT /solver-frame` body, in order: the card's own confirmations (Solve's go with its press). */
  let puts: Array<{ ingestId?: string; operationId?: string; axis: string }>;

  const dockActivate = (panel: WorkspacePanel) => {
    activations.push(panel);
    if (panel === 'results') visible.delete('geometry');
    if (panel === 'geometry') visible.delete('results');
    visible.add(panel);
    publishVisiblePanels(visible);
    return true;
  };

  async function mount(): Promise<void> {
    await act(async () => {
      const record = useCadReturnStore.getState().ingestRecord!;
      root.render(<JobsCoordinator now={() => new Date(2026, 8, 22, 12)}>
        <CadSolveCard record={record} label="Speaker"/>
        <CadOperationsSection record={record} solves={false}/>
        <div className="topbar"><SolveActions/></div>
        <ResultsPanel/>
      </JobsCoordinator>);
      await flush(8);
    });
    await vi.waitFor(() => expect(host.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
  }

  // The CAD Link panel's buttons; the top bar's Solve is the same command, elsewhere.
  const solveButton = () => host.querySelector<HTMLButtonElement>('button[data-action="solve"]')!;

  async function pressSolve(): Promise<void> {
    expect(solveButton().disabled).toBe(false);
    await act(async () => { solveButton().click(); await flush(12); });
  }

  async function deliver(summary: CadOperationSummary): Promise<void> {
    await act(async () => { publishCadSummary(summary); await flush(); });
  }

  async function jobs(list: JobItem[]): Promise<void> {
    await act(async () => {
      publishJobs(list.map((job) => {
        const previous = jobsSocket.getSnapshot().jobs.find((item) => item.id === job.id);
        return { ...previous, ...job, ...(previous?.cad_state ? { cad_state: previous.cad_state } : {}) };
      })); await flush(8);
    });
  }

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    resetDocumentStore();
    useDocumentStore.getState().setDesignName('horn');
    resetDesignStore();
    resetCadReturnStore();

    resetCadSolverFrameStore();
    resetSolveOptionsStore();
    resetSolveAttentionForTests();
    resetWorkspaceNavigationForTests();
    sessionStorage.clear();
    importedMeshStore.clear();
    resultsCache.clear();
    provisionalResults.clear();
    compareSelection.clear();
    publishJobs([]);
    activations = [];
    visible = new Set(['viewport', 'geometry']);
    publishVisiblePanels(visible);
    bindWorkspaceNavigation(dockActivate);
    frame = frameAnswer({ suggestion: automatic('+x'), preselected: { axis: '+x', source: 'suggested' } });
    puts = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/cadlink/solver-frame?ingestId=wgi_first' && !init?.method) return json(frame);
      if (url === '/api/cadlink/solver-frame' && init?.method === 'PUT') {
        const body = JSON.parse(String(init.body)) as { axis: SolverFrameAxis };
        puts.push(body);
        frame = { ...frame, confirmed: { axis: body.axis, confirmedAt: 'now', frame: null }, preselected: { axis: body.axis, source: 'confirmed' }, differs: null };
        return json(frame);
      }
      if (url.startsWith('/api/cadlink/ingest/wgi_first/')) return new Response(MSH, { status: 200 });
      return resultResponse();
    }));
    mocks.solveModel.mockResolvedValue('job-1');
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    readyCad();
    workspaceModeStore.setMode('cad');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    workspaceModeStore.setMode('parametric');
  });

  it('captures the displayed setup and axis, remembers them for the project, and follows the run', async () => {
    await mount();
    expect(host.querySelector('.cad-solve-settings > span')!.textContent).toContain('AUTO (Metal)');
    await pressSolve();
    // One call: the server confirms the axis shown and records the setup for the model.
    expect(puts).toEqual([]);
    expect(mocks.solveModel).toHaveBeenCalledWith('wgl_test', expect.objectContaining({ prepared_id: 'wgi_first', frame_axis: '+x' }));
    const press = pressOf();
    expect(press.setup!.options).toMatchObject({ frequency_range: [50, 20000], num_frequencies: 36 });
    await jobs([cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'accepted', { jobId: 'job-1' }), cadJob('job-1'))]);
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
  });

  it('hands the solved-model adoption the ingestion the press solved from', async () => {
    const claim = vi.spyOn(solvedCadModels, 'claim');
    await mount();
    await pressSolve();
    const press = pressOf();
    await jobs([cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'accepted', { jobId: 'job-1' }), cadJob('job-1'))]);
    // Without it, adoption would replace a newer rebuild of the same return.
    expect(claim).toHaveBeenCalledWith('job-1', expect.objectContaining({ sourceIngestId: 'wgi_first' }));
    expect(claim.mock.calls.every(([, value]) => value.sourceIngestId === 'wgi_first')).toBe(true);
  });

  it('keeps the press ingestion for a child an Approve claimed before it arrived', async () => {
    const { rememberCadSolve } = await import('../jobs/cadSolve');
    const claim = vi.spyOn(solvedCadModels, 'claim');
    await mount();
    await pressSolve();
    const press = pressOf();
    const parent = cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'needs_user_input', { jobId: 'job-1', reason: 'findings_need_review' }));
    await jobs([parent]);
    // approveCadJob claims the child it got back before the coordinator sees it.
    rememberCadSolve('job-2', '');
    const child = { ...cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'accepted', { jobId: 'job-2' })), parent_job_id: 'job-1' };
    await jobs([parent, child, { ...cadJob('job-2'), parent_job_id: 'job-1' }]);
    expect(claim).toHaveBeenCalledWith('job-2', expect.objectContaining({ sourceIngestId: 'wgi_first' }));
  });

  it('solves from the card when the dock renders it in its own React root, outside the coordinator', async () => {
    // Workspace.tsx renders every dock panel with its own createRoot, so the
    // CAD Link panel's card never sits under the coordinator's context.
    const panelHost = document.createElement('div');
    document.body.append(panelHost);
    const panelRoot = createRoot(panelHost);
    try {
      await act(async () => {
        root.render(<JobsCoordinator now={() => new Date(2026, 8, 22, 12)}><div className="topbar"><SolveActions/></div></JobsCoordinator>);
        await flush(8);
      });
      const record = useCadReturnStore.getState().ingestRecord!;
      await act(async () => { panelRoot.render(<CadSolveCard record={record} label="Speaker"/>); await flush(8); });
      await vi.waitFor(() => expect(panelHost.querySelector('[data-frame-preview="ready"]')).not.toBeNull());
      const button = panelHost.querySelector<HTMLButtonElement>('button[data-action="solve"]')!;
      await vi.waitFor(() => expect(button.disabled).toBe(false));
      expect(button.title).not.toBe('Solve is not available here.');
      await act(async () => { button.click(); await flush(12); });
      expect(mocks.solveModel).toHaveBeenCalledWith('wgl_test', expect.objectContaining({ prepared_id: 'wgi_first' }));
      // Once the coordinator is gone, the card has no command to borrow.
      await act(async () => { root.render(<div/>); await flush(); });
      expect(panelHost.querySelector<HTMLButtonElement>('button[data-action="solve"]')!.disabled).toBe(true);
    } finally {
      act(() => panelRoot.unmount());
      panelHost.remove();
    }
  });

  it('acceptance: a Fusion request, settings and engine changed in WG, then Solve uses those choices and retry reverts nothing', async () => {
    await deliver(operation('cmd-fusion', 'needs_user_input', { reason: 'setup_required' }));
    await mount();
    act(() => { useCadReturnStore.setState({ frequencyStartHz: 80, frequencyEndHz: 12000, frequencyCount: 12 }); useImportedSolveOptionsStore.getState().setEngine('bempp'); });
    // The server continues the waiting Fusion request with the settings on screen.
    mocks.solveModel.mockResolvedValueOnce('job-child');
    await pressSolve();
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    expect(pressOf().setup!.options).toMatchObject({ engine: 'bempp', frequency_range: [80, 12000], num_frequencies: 12 });
    await jobs([cadJobFixture(operation('cmd-fusion', 'accepted', { jobId: 'job-child' }), { ...cadJob('job-child', 'error'), parent_job_id: 'cmd-fusion' })]);
    const bind = vi.spyOn(solveAttention, 'bindRun');
    const retry = vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue('retry-child');
    await act(async () => jobsCoordinatorBridge.getSnapshot().continueRun('job-child'));
    expect(retry).toHaveBeenCalledWith('job-child');
    expect(compareSelection.getSnapshot().awaiting).toBe('retry-child');
    expect(bind).toHaveBeenCalledWith('retry-child');
    expect(useImportedSolveOptionsStore.getState().engine).toBe('bempp');
    expect(useCadReturnStore.getState().frequencyCount).toBe(12);
    expect(mocks.solveModel).toHaveBeenCalledOnce();
  });

  it('never solves along an axis the card did not show: a changed project frame stops the job visibly', async () => {
    frame = frameAnswer({ confirmed: '+z', preselected: { axis: '+z', source: 'confirmed' } });
    await mount();
    // The project's frame is changed elsewhere while the press is answered.
    mocks.solveModel.mockImplementationOnce(async () => { frame = frameAnswer({ confirmed: '+x', preselected: { axis: '+x', source: 'confirmed' } }); return 'job-1'; });
    await pressSolve();
    expect(pressOf().frame_axis).toBe('+z');
    await deliver(operation('manual-solve:first', 'needs_user_input', { jobId: 'job-1', reason: 'frame_confirmation_required', message: 'The project frame changed elsewhere.' }));
    expect(host.textContent).toContain('check which way it radiates');
    await act(async () => useCadSolverFrameStore.getState().load('wgi_first'));
    expect(host.textContent).toContain('changed');
    await pressSolve();
    expect(pressOf(1).frame_axis).toBe('+x');
  });

  it('leaves Solve disabled while the initial frame read waits', async () => {
    let release!: () => void;
    const read = new Promise<void>((resolve) => { release = resolve; });
    const api = vi.fn(async () => { await read; return json(frame); });
    const loading = useCadSolverFrameStore.getState().load('wgi_first', api);
    await act(async () => { root.render(<JobsCoordinator><CadSolveCard record={useCadReturnStore.getState().ingestRecord!} label="Speaker"/></JobsCoordinator>); });
    expect(solveButton().disabled).toBe(true);
    await expect(solveControlStore.getSnapshot()!.solve()).resolves.toBe('blocked');
    await act(async () => { release(); await loading; });
    expect(mocks.solveModel).not.toHaveBeenCalled();
    await pressSolve();
    expect(pressOf().prepared_id).toBe('wgi_first');
  });

  it('leaves the job already preparing the displayed snapshot to the server: Solve is one model call', async () => {
    await deliver(operation('cmd-fusion', 'processing', { jobId: 'job-fusion' }));
    await mount();
    // No frontend hold: Solve stays available.
    expect(solveButton().disabled).toBe(false);
    mocks.solveModel.mockResolvedValueOnce('job-fusion');
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    // The server answers the job in flight for the same settings; nothing else is made here.
    expect(mocks.solveModel).toHaveBeenCalledOnce();
  });

  it('shows failed and cancelled execution without revealing Results', async () => {
    await mount(); await pressSolve();
    const press = pressOf();
    await jobs([cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'accepted', { jobId: 'job-1' }), cadJob('job-1', 'error'))]);
    expect(activations).not.toContain('results');
    expect(host.querySelector('.cad-solve-run')?.textContent).toContain('Failed');
    await jobs([cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'accepted', { jobId: 'job-1' }), cadJob('job-1', 'cancelled'))]);
    expect(activations).not.toContain('results');
  });

  it('shows the sweep blocker on the CAD Solve card and keeps catalog advice nonblocking', async () => {
    const state = useCadReturnStore.getState();
    useCadReturnStore.setState({
      selectedBundle: { ...state.selectedBundle!, sources: [
        { id: 'source-mf', role: 'MF', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-mf' },
        ...state.selectedBundle!.sources,
      ] },
      driveChannels: [
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
        ...state.driveChannels,
      ],
      channelDrivers: { 'drive-hf': {
        fields: {}, preset: {
          id: 'Acme::HF::8', label: 'Acme HF', source: 'database', kind: 'cd',
          z_ohm: 8, xo_min_hz: 1000,
          base: { sd_cm2: 26, bl_t_m: 12.4, re_ohm: 6.2, mms_g: 2.4, fs_hz: 620 },
        },
      } },
      combineEnabled: true,
      combineSpec: expandLegacy(['drive-mf', 'drive-hf'], [800]),
      // A sweep starting above WG's default, so the crossovers below it are blocked.
      frequencyStartHz: 200,
    });
    await mount();
    await vi.waitFor(() => expect(solveButton().disabled).toBe(false));
    for (const hz of [8, 80, 199]) {
      await act(async () => useCadReturnStore.getState().setCombineSpec(withPair(combineSpecEffective(useCadReturnStore.getState())!, 'drive-mf→drive-hf', { hz })));
      expect(solveButton().disabled).toBe(true);
      expect(host.querySelector('.cad-solve-card .solve-readiness')?.textContent).toContain(`The ${hz} Hz crossover is below the 200 Hz sweep start`);
    }
    for (const hz of [200, 800]) {
      await act(async () => useCadReturnStore.getState().setCombineSpec(withPair(combineSpecEffective(useCadReturnStore.getState())!, 'drive-mf→drive-hf', { hz })));
      expect(solveButton().disabled).toBe(false);
      expect(host.querySelector('.cad-solve-card .solve-readiness')).toBeNull();
    }
  });

  it('offers only the axes the backend supports for this model', async () => {
    frame = frameAnswer({
      allowed: ['+z'],
      suggestion: { ...asking!, reason: 'This model faces -y, which a declared half cannot be solved along yet. Pick the front.', reasonCode: 'unsupported-axis' },
    });
    await mount();
    const radios = [...host.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
    expect(radios.filter((radio) => !radio.disabled).map((radio) => radio.value)).toEqual(['+z']);
    expect(radios.find((radio) => radio.value === '-y')!.closest('label')!.title).toContain('only as modelled');
    // A disabled axis cannot be picked into what Solve confirms.
    act(() => useCadSolverFrameStore.getState().pick('wgi_first', '-y'));
    expect(useCadSolverFrameStore.getState().frames.wgi_first.axis).toBeNull();
    expect(solveButton().disabled).toBe(true);
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="+z"]')!.click(); await flush(); });
    await pressSolve();
    expect(puts).toEqual([]);
    expect(pressOf().frame_axis).toBe('+z');
  });

  it('changes the axis on the card, and Solve confirms the one shown', async () => {
    await mount();
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="change-solver-frame"]')!.click(); });
    const checked = () => host.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.value;
    expect(checked()).toBe('+x');
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-z"]')!.click(); await flush(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="done-solver-frame"]')!.click(); });
    expect(host.querySelector('.cad-solver-frame-line')!.textContent).toBe('Radiates along -z · Change');
    expect(host.querySelector('.cad-solver-frame-source')!.textContent).toBe('your choice');
    // Changing it confirms nothing by itself.
    expect(puts).toEqual([]);
    await pressSolve();
    // Solve sends the axis shown; the server confirms it with the press.
    expect(pressOf().frame_axis).toBe('-z');
  });

  it('opens the axis chooser when the run line asks to change an automatic axis', async () => {
    await mount();
    expect(host.querySelector('input[type="radio"]')).toBeNull();
    await act(async () => { useCadSolverFrameStore.getState().requestChange('wgi_first'); await flush(); });
    expect(host.querySelector<HTMLInputElement>('input[type="radio"]:checked')?.value).toBe('+x');
    // Nothing is confirmed by asking to change it, or by Done without a pick.
    expect(puts).toEqual([]);
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="done-solver-frame"]')!.click(); await flush(); });
    expect(puts).toEqual([]);
  });

  it('confirms the axis picked after Change from an automatic run, so the next solve uses it', async () => {
    await mount();
    await act(async () => { useCadSolverFrameStore.getState().requestChange('wgi_first'); await flush(); });
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-z"]')!.click(); await flush(); });
    expect(puts).toEqual([]);
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="done-solver-frame"]')!.click(); await flush(); });
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '-z' }]);
  });

  function selectSecond(): void {
    const first = useCadReturnStore.getState().ingestRecord!;
    useCadReturnStore.setState({
      ingestRecord: { ...first, ingest_id: 'wgi_second', manifest_sha256: SECOND } as CadReturnIngestRecord,
      frequencyCount: 99,
    });
    importedMeshStore.setCad({ name: 'Speaker B', source: 'cad', ingestId: 'wgi_second' } as ImportedMeshScene);
  }

  it('does not carry a Change from one model to the next: Done there waits for Solve', async () => {
    await mount();
    await act(async () => { useCadSolverFrameStore.getState().requestChange('wgi_first'); await flush(); });
    expect(host.querySelector('input[type="radio"]')).not.toBeNull();
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input) === '/api/cadlink/solver-frame?ingestId=wgi_second' && !init?.method) {
        return json({ ...frame, ingestId: 'wgi_second', confirmed: null });
      }
      return base(input, init);
    }));
    act(() => selectSecond());
    await mount();
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-frame')?.getAttribute('data-solver-frame')).not.toBeNull());
    await vi.waitFor(() => expect(host.querySelector('button[data-action="change-solver-frame"]')).not.toBeNull());
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="change-solver-frame"]')!.click(); await flush(); });
    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-z"]')!.click(); await flush(); });
    await act(async () => { host.querySelector<HTMLButtonElement>('button[data-action="done-solver-frame"]')!.click(); await flush(); });
    expect(puts).toEqual([]);
  });

  it('does not reopen the chooser on a later remount', async () => {
    await mount();
    await act(async () => { useCadSolverFrameStore.getState().requestChange('wgi_first'); await flush(); });
    expect(useCadSolverFrameStore.getState().changeRequests.wgi_first).toBe(0);
    await act(async () => { root.render(<div/>); await flush(); });
    await mount();
    expect(host.querySelector('input[type="radio"]')).toBeNull();
  });

  it('keeps a confirmation made under frame contract v1: preselected, not asked again, and confirmed by Solve', async () => {
    frame = frameAnswer({ confirmed: null, suggestion: automatic('-x'), preselected: { axis: '+y', source: 'carried' } });
    await mount();
    expect(host.querySelector('.cad-solver-frame-line')!.textContent).toBe('Radiates along +y · Change');
    expect(host.querySelector('.cad-solver-frame-source')!.textContent).toBe('this project’s frame');
    expect(host.querySelector('input[type="radio"]')).toBeNull();
    await pressSolve();
    expect(pressOf().frame_axis).toBe('+y');
  });

});
