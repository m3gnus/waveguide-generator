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
 * /api/cadlink/solver-frame` as server/cadlink/solver_frame.py answers).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadJobFixture, publishCadSummary } from '../jobs/cadSolve.fixtures';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary, CadSolveSetup } from '../api/cadOperations';
import type { SolverFrameState } from '../api/solverFrame';
import { resetCadOperationsStore } from '../stores/cadOperations';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetCadSolverFrameStore, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { expandLegacy, withPair } from '../results/crossoverSpec';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import fixtureV2 from '../viewport/solverFrame.v2.fixture.json';
import { SOLVER_FRAME_AXES, type SolverFrameAxis } from '../viewport/solverFrame';
import { JobsCoordinator, jobsCoordinatorBridge } from './JobsCoordinator';
import { ResultsPanel } from './ResultsPanel';
import { SolveActions } from './TopBar';
import { CadSolveCard } from './CadSolveCard';
import { CadOperationsSection } from './CadOperationsSection';
import { resetSolveAttentionForTests } from './solveAttention';
import {
  bindWorkspaceNavigation,
  publishVisiblePanels,
  resetWorkspaceNavigationForTests,
  type WorkspacePanel,
} from './workspaceNavigation';

const mocks = vi.hoisted(() => ({
  submitImported: vi.fn(),
  createSetupRevision: vi.fn(),
  getSetupRevision: vi.fn(),
  putProjectSetup: vi.fn(),
  submitCadSolve: vi.fn(),
  solveCadAgain: vi.fn(),
}));

vi.mock('../api/cadOperations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/cadOperations')>();
  return {
    ...actual,
    createSetupRevision: mocks.createSetupRevision,
    getSetupRevision: mocks.getSetupRevision,
    putProjectSetup: mocks.putProjectSetup,
  };
});
vi.mock('../jobs/cadSolve', async (importOriginal) => ({
  ...await importOriginal<typeof import('../jobs/cadSolve')>(),
  submitCadSolve: mocks.submitCadSolve, solveCadAgain: mocks.solveCadAgain,
}));
vi.mock('../jobs/actions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/actions')>();
  return { ...actual, submitImported: mocks.submitImported };
});
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
  /** Every `PUT /solver-frame` body, in order. */
  let puts: Array<{ ingestId?: string; operationId?: string; axis: string }>;
  /** Setup revisions by id, as `GET /setup-revisions/{id}` returns them. */
  let revisions: Map<string, CadSolveSetup>;

  const dockActivate = (panel: WorkspacePanel) => {
    activations.push(panel);
    if (panel === 'results') visible.delete('cadlink');
    if (panel === 'cadlink') visible.delete('results');
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
    resetCadOperationsStore();
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
    visible = new Set(['viewport', 'cadlink', 'geometry']);
    publishVisiblePanels(visible);
    bindWorkspaceNavigation(dockActivate);
    frame = frameAnswer({ suggestion: automatic('+x'), preselected: { axis: '+x', source: 'suggested' } });
    puts = [];
    revisions = new Map();
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
    const store = (setup: CadSolveSetup) => {
      // Identical content is one revision, as the server stores them.
      const key = JSON.stringify(setup);
      for (const [id, held] of revisions) if (JSON.stringify(held) === key) return id;
      const id = `wgs_${revisions.size + 1}`;
      revisions.set(id, setup);
      return id;
    };
    mocks.createSetupRevision.mockImplementation(async (setup: CadSolveSetup) => ({
      revisionId: store(setup), contentSha256: 'sha256:setup', createdAt: 'now',
    }));
    mocks.putProjectSetup.mockImplementation(async (request: { lineageId: string; setup: CadSolveSetup }) => ({
      lineageId: request.lineageId, inventorySha256: 'sha256:inv', revisionId: store(request.setup),
    }));
    mocks.getSetupRevision.mockImplementation(async (revisionId: string) => {
      const setup = revisions.get(revisionId);
      if (!setup) throw new CadLinkApiError('Unknown setup revision', [], 404);
      return { revisionId, contentSha256: 'sha256:setup', createdAt: 'now', setup };
    });
    mocks.submitCadSolve.mockResolvedValue({ job_id: 'job-1' });
    mocks.solveCadAgain.mockResolvedValue({ job_id: 'job-child' });
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
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+x' }]);
    expect(mocks.submitCadSolve).toHaveBeenCalledWith(expect.objectContaining({ ingest_id: 'wgi_first', frame_axis: '+x', setup_revision_id: expect.any(String) }));
    const press = mocks.submitCadSolve.mock.calls[0][0];
    expect(revisions.get(press.setup_revision_id)?.options).toMatchObject({ frequency_range: [50, 20000], num_frequencies: 36 });
    expect(mocks.putProjectSetup.mock.calls[0][0]).toMatchObject({ lineageId: 'wgl_test' });
    await jobs([cadJobFixture(operation(`manual-solve:${press.client_request_id}`, 'accepted', { jobId: 'job-1' }), cadJob('job-1'))]);
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
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
      expect(mocks.submitCadSolve).toHaveBeenCalledWith(expect.objectContaining({ ingest_id: 'wgi_first' }));
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
    act(() => { useCadReturnStore.setState({ frequencyStartHz: 80, frequencyEndHz: 12000, frequencyCount: 12 }); useSolveOptionsStore.getState().setEngine('bempp'); });
    await pressSolve();
    expect(mocks.submitCadSolve).not.toHaveBeenCalled();
    const [id, press] = mocks.solveCadAgain.mock.calls[0];
    expect(id).toBe('cmd-fusion');
    const setup = revisions.get(press.setup_revision_id)!;
    expect(setup.options).toMatchObject({ engine: 'bempp', frequency_range: [80, 12000], num_frequencies: 12 });
    await jobs([cadJobFixture(operation('cmd-fusion', 'accepted', { jobId: 'job-child' }), { ...cadJob('job-child', 'error'), parent_job_id: 'cmd-fusion' })]);
    const retry = vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue();
    await act(async () => jobsCoordinatorBridge.getSnapshot().retry('job-child'));
    expect(retry).toHaveBeenCalledWith('job-child');
    expect(useSolveOptionsStore.getState().engine).toBe('bempp');
    expect(useCadReturnStore.getState().frequencyCount).toBe(12);
    expect(mocks.solveCadAgain).toHaveBeenCalledOnce();
  });

  it('keeps a continuation on its exact setup revision when the displayed inputs have not changed', async () => {
    await mount();
    await pressSolve();
    const first = mocks.submitCadSolve.mock.calls[0][0];
    await deliver(operation('manual-solve:first', 'needs_user_input', { jobId: 'job-1', reason: 'findings_need_review', setupRevisionId: first.setup_revision_id }));
    await pressSolve();
    expect(mocks.solveCadAgain.mock.calls[0][1].setup_revision_id).toBe(first.setup_revision_id);
    expect(mocks.createSetupRevision).toHaveBeenCalledOnce();
  });

  it('uses a fresh revision when the bound revision cannot be read', async () => {
    await deliver(operation('cmd-fusion', 'needs_user_input', { reason: 'setup_required', setupRevisionId: 'gone' }));
    await mount(); await pressSolve();
    expect(mocks.solveCadAgain.mock.calls[0][1].setup_revision_id).not.toBe('gone');
  });

  it('preserves a deliberate axis pick during a pending settings save', async () => {
    await mount();
    act(() => useCadSolverFrameStore.getState().pick('wgi_first', '-y'));
    let release!: (value: { revisionId: string }) => void;
    mocks.putProjectSetup.mockReturnValueOnce(new Promise((resolve) => { release = resolve; }));
    await act(async () => { solveButton().click(); await flush(); });
    act(() => useCadSolverFrameStore.getState().pick('wgi_first', '+z'));
    await act(async () => { release({ revisionId: 'saved' }); await flush(12); });
    expect(mocks.submitCadSolve.mock.calls[0][0].frame_axis).toBe('-y');
  });

  it('never solves along an axis the card did not show: a changed project frame stops the job visibly', async () => {
    frame = frameAnswer({ confirmed: '+z', preselected: { axis: '+z', source: 'confirmed' } });
    await mount();
    mocks.putProjectSetup.mockImplementationOnce(async () => { frame = frameAnswer({ confirmed: '+x', preselected: { axis: '+x', source: 'confirmed' } }); return { revisionId: 'saved' }; });
    await pressSolve();
    expect(mocks.submitCadSolve.mock.calls[0][0].frame_axis).toBe('+z');
    await deliver(operation('manual-solve:first', 'needs_user_input', { jobId: 'job-1', reason: 'frame_confirmation_required', message: 'The project frame changed elsewhere.' }));
    expect(host.textContent).toContain('check which way it radiates');
    await act(async () => useCadSolverFrameStore.getState().load('wgi_first'));
    expect(host.textContent).toContain('changed');
    await pressSolve();
    expect(mocks.solveCadAgain.mock.calls[0][1].frame_axis).toBe('+x');
  });

  it('captures the original snapshot and settings while the initial frame read waits', async () => {
    let release!: () => void;
    const read = new Promise<void>((resolve) => { release = resolve; });
    const api = vi.fn(async () => { await read; return json(frame); });
    const loading = useCadSolverFrameStore.getState().load('wgi_first', api);
    let solve!: Promise<unknown>;
    await act(async () => { root.render(<JobsCoordinator><span>ready</span></JobsCoordinator>); });
    await act(async () => { solve = jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); await flush(); });
    act(() => { readyCad(); useCadReturnStore.setState({ ingestRecord: { ...useCadReturnStore.getState().ingestRecord!, ingest_id: 'wgi_second', manifest_sha256: SECOND } }); useCadReturnStore.setState({ frequencyCount: 99 }); });
    await act(async () => { release(); await loading; await solve; });
    const press = mocks.submitCadSolve.mock.calls[0][0];
    expect(press.ingest_id).toBe('wgi_first');
    expect(revisions.get(press.setup_revision_id)?.options.num_frequencies).toBe(36);
  });

  it('holds Solve on the job already preparing the displayed snapshot', async () => {
    await deliver(operation('cmd-fusion', 'processing'));
    await mount();
    expect(solveButton().disabled).toBe(true);
    expect(host.textContent).toContain('Preparing the request from Fusion');
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); });
    expect(mocks.submitCadSolve).not.toHaveBeenCalled();
    expect(mocks.solveCadAgain).not.toHaveBeenCalled();
  });

  it('ignores a new request for another snapshot while saving the pressed settings', async () => {
    await mount();
    mocks.putProjectSetup.mockImplementationOnce(async () => {
      readyCad(); useCadReturnStore.setState({ ingestRecord: { ...useCadReturnStore.getState().ingestRecord!, ingest_id: 'wgi_second', manifest_sha256: SECOND } });
      publishCadSummary(operation('cmd-second', 'processing', { snapshot: { manifestSha256: SECOND } }));
      return { revisionId: 'saved' };
    });
    await pressSolve();
    expect(mocks.submitCadSolve.mock.calls[0][0].ingest_id).toBe('wgi_first');
    expect(mocks.solveCadAgain).not.toHaveBeenCalled();
  });

  it('shows failed and cancelled execution without revealing Results', async () => {
    await mount(); await pressSolve();
    const press = mocks.submitCadSolve.mock.calls[0][0];
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
      await act(async () => useCadReturnStore.getState().updateCombineSpec((spec) => withPair(spec, 'drive-mf→drive-hf', { hz })));
      expect(solveButton().disabled).toBe(true);
      expect(host.querySelector('.cad-solve-blocker')?.textContent).toContain(`The ${hz} Hz crossover is below the 200 Hz sweep start`);
    }
    for (const hz of [200, 800]) {
      await act(async () => useCadReturnStore.getState().updateCombineSpec((spec) => withPair(spec, 'drive-mf→drive-hf', { hz })));
      expect(solveButton().disabled).toBe(false);
      expect(host.querySelector('.cad-solve-blocker')).toBeNull();
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
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+z' }]);
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
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '-z' }]);
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
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+y' }]);
  });

});
