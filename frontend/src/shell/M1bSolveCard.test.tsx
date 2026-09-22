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
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationDetail, CadOperationSummary, CadSolveSetup } from '../api/cadOperations';
import type { SolverFrameState } from '../api/solverFrame';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetCadSolverFrameStore, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
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
  createCadOperation: vi.fn(),
  prepareCadOperation: vi.fn(),
  getCadOperation: vi.fn(),
}));

vi.mock('../api/cadOperations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/cadOperations')>();
  return {
    ...actual,
    createSetupRevision: mocks.createSetupRevision,
    getSetupRevision: mocks.getSetupRevision,
    putProjectSetup: mocks.putProjectSetup,
    createCadOperation: mocks.createCadOperation,
    prepareCadOperation: mocks.prepareCadOperation,
    getCadOperation: mocks.getCadOperation,
  };
});
vi.mock('../jobs/actions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/actions')>();
  return { ...actual, submitImported: mocks.submitImported };
});
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({
    engines: [{ name: 'metal', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] }],
    engineSelection: { default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal'], axisymmetricRunner: 'axisym' },
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

function detail(summary: CadOperationSummary): CadOperationDetail {
  return { ...summary, approvals: [], preparation: null };
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
  const solveButtons = () => [...host.querySelectorAll<HTMLButtonElement>('button:not(.solve-button)')]
    .filter((button) => /solve/i.test(button.textContent ?? '') || button.dataset.action === 'solve');
  const solveButton = () => host.querySelector<HTMLButtonElement>('button[data-action="solve"]')!;

  async function pressSolve(): Promise<void> {
    expect(solveButton().disabled).toBe(false);
    await act(async () => { solveButton().click(); await flush(12); });
  }

  async function deliver(summary: CadOperationSummary): Promise<void> {
    await act(async () => { useCadOperationsStore.getState().apply(summary); await flush(); });
  }

  async function jobs(list: JobItem[]): Promise<void> {
    await act(async () => { publishJobs(list); await flush(8); });
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
    mocks.createCadOperation.mockImplementation(async ({ operationId }: { operationId: string }) => operation(operationId));
    mocks.prepareCadOperation.mockImplementation(async (operationId: string) => (
      useCadOperationsStore.getState().operations[operationId] ?? operation(operationId, 'processing', { stage: 'validating' })
    ));
    mocks.getCadOperation.mockImplementation(async (operationId: string) => {
      const held = useCadOperationsStore.getState().operations[operationId];
      if (!held) throw new CadLinkApiError('Unknown CAD operation', [], 404);
      return detail(held);
    });
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

  it('shows the automatic frame as one line, and one press solves: one operation, the frame confirmed, the settings remembered', async () => {
    await mount();
    const line = host.querySelector('.cad-solver-frame-line')!;
    expect(line.textContent).toBe('Radiates along +x · Change');
    // No question while WG has an answer, and never its code.
    expect(host.querySelector('input[type="radio"]')).toBeNull();
    expect(host.textContent).not.toContain('inferred');
    expect(host.textContent).not.toContain('cannot know which way it radiates');
    // The preview arrow is the solver +Z of the axis shown.
    expect(host.querySelector('[data-frame-preview-axis="+x"]')).not.toBeNull();
    // The model and the settings Solve uses, each in one line.
    expect(host.querySelector('.cad-solve-summary')!.textContent).toBe('Body1 · 1 source (HF) · full model, WG mirrors it at x = 0');
    expect(host.querySelector('.cad-solve-settings > span')!.textContent).toBe('200 Hz–20 kHz · 24 freq · AUTO (Metal) · ~3 min');
    expect(solveButtons()).toHaveLength(1);

    await pressSolve();

    // The frame shown, confirmed once; the server decides "suggested".
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+x' }]);
    // The settings used, remembered for this model's project.
    expect(mocks.putProjectSetup).toHaveBeenCalledOnce();
    expect(mocks.putProjectSetup.mock.calls[0][0]).toMatchObject({ lineageId: 'wgl_test', inventory: [{ id: 'source-hf', role: 'HF', required: true }] });
    // Exactly one operation, prepared once with the settings on screen.
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    const operationId = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation).toHaveBeenCalledWith(operationId, { setupRevisionId: expect.any(String), submit: true });
    const bound = revisions.get(mocks.prepareCadOperation.mock.calls[0][1].setupRevisionId as string)!;
    expect(bound.options).toMatchObject({ frequency_range: [200, 20_000], num_frequencies: 24 });
    // The remembered settings are the ones solved, without the run's name.
    const remembered = mocks.putProjectSetup.mock.calls[0][0].setup as CadSolveSetup;
    expect({ ...remembered.options, solver_mode: 'full_3d' }).toEqual(bound.options);
    expect(remembered.label).toBeUndefined();

    // It follows the job to the result, and reveals it once.
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', stage: 'submitted', updatedAt: '2026-09-22T10:00:05Z' }));
    await jobs([cadJob('job-1', 'running')]);
    expect(host.querySelector('.cad-solve-run')!.textContent).toBe('Solving · 40%');
    expect(activations).not.toContain('results');
    await jobs([cadJob('job-1')]);
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'job-1', awaiting: null });
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    // Delivered again: no second reveal.
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', stage: 'submitted', updatedAt: '2026-09-22T10:00:07Z' }));
    await jobs([cadJob('job-1')]);
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
  });

  it('asks only when WG has no answer: the reason in words, Solve disabled until an axis is chosen, and the choice confirmed', async () => {
    frame = frameAnswer({ suggestion: asking, preselected: null });
    await mount();
    expect(host.querySelector('.cad-solver-frame-line')).toBeNull();
    expect(host.querySelector('.cad-solver-frame-question')!.textContent).toBe('Which way does the mouth face? (CAD axes)');
    expect(host.querySelector('.cad-solver-frame-reason')!.textContent).toBe(asking!.reason);
    expect(host.textContent).not.toContain('conflicting-evidence');
    const radios = [...host.querySelectorAll<HTMLInputElement>('input[type="radio"]')];
    expect(radios.map((radio) => radio.value)).toEqual([...SOLVER_FRAME_AXES]);
    expect(radios.some((radio) => radio.checked)).toBe(false);
    expect(solveButton().disabled).toBe(true);
    expect(host.querySelector('.cad-solve-blocker')!.textContent).toContain('Choose which way this model radiates');
    // Every other caller of the one Solve command is held by the same rule.
    await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).rejects.toThrow('Choose which way this model radiates');
    expect(mocks.createCadOperation).not.toHaveBeenCalled();

    await act(async () => { host.querySelector<HTMLInputElement>('input[value="-y"]')!.click(); await flush(); });
    expect(host.querySelector('[data-frame-preview-axis="-y"]')).not.toBeNull();
    expect(host.textContent).toContain('model -y → solver +Z');
    expect(puts).toEqual([]);
    await pressSolve();
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '-y' }]);
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
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

  it('waits for a frame read still in flight, then confirms the axis it shows (Bring in & solve)', async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    let answer!: () => void;
    const held = new Promise<void>((resolve) => { answer = resolve; });
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/cadlink/solver-frame?')) await held;
      return base(input, init);
    }));
    await act(async () => {
      const record = useCadReturnStore.getState().ingestRecord!;
      root.render(<JobsCoordinator now={() => new Date(2026, 8, 22, 12)}><CadSolveCard record={record} label="Speaker"/></JobsCoordinator>);
      await flush(8);
    });
    expect(host.querySelector('[data-frame-preview="loading"]')).not.toBeNull();
    let outcome!: Promise<'submitted' | 'busy'>;
    await act(async () => { outcome = jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); await flush(); });
    // Held: nothing is prepared on a frame nobody has seen yet, and a second
    // call while it waits is the busy one.
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    await act(async () => { await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('busy'); });
    await act(async () => { answer(); await expect(outcome).resolves.toBe('submitted'); });
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+x' }]);
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
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

  it('leaves the frame to the backend gate when it cannot be read, and never guesses one', async () => {
    const base = vi.mocked(fetch).getMockImplementation()!;
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).startsWith('/api/cadlink/solver-frame')) return json({ detail: 'The CAD store is busy.' }, 503);
      return base(input, init);
    }));
    await act(async () => {
      const record = useCadReturnStore.getState().ingestRecord!;
      root.render(<JobsCoordinator now={() => new Date(2026, 8, 22, 12)}><CadSolveCard record={record} label="Speaker"/></JobsCoordinator>);
      await flush(8);
    });
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-frame')!.textContent).toContain('Solve still stops to ask'));
    await pressSolve();
    expect(puts).toEqual([]);
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
  });

  it('keeps the project frame when a new version looks different, and switches only on the one click offered', async () => {
    frame = frameAnswer({
      confirmed: '+z',
      suggestion: automatic('+x'),
      preselected: { axis: '+z', source: 'confirmed' },
      differs: {
        confirmedAxis: '+z', suggestedAxis: '+x',
        message: 'This version looks like it faces +x; this project is set to +z. WG keeps +z until you switch.',
      },
    });
    await mount();
    expect(host.querySelector('.cad-solver-frame-line')!.textContent).toBe('Radiates along +z · Change');
    const notice = host.querySelector('.cad-solver-frame-differs')!;
    expect(notice.getAttribute('role')).toBe('status');
    expect(notice.textContent).toContain('WG keeps +z until you switch');
    // Never switched silently: nothing is confirmed by showing it.
    expect(puts).toEqual([]);
    await act(async () => { notice.querySelector<HTMLButtonElement>('button[data-action="switch-solver-frame"]')!.click(); await flush(8); });
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+x' }]);
    await vi.waitFor(() => expect(host.querySelector('.cad-solver-frame-line')!.textContent).toBe('Radiates along +x · Change'));
    expect(host.querySelector('.cad-solver-frame-differs')).toBeNull();
    // Already confirmed: Solve sends no second confirmation.
    await pressSolve();
    expect(puts).toHaveLength(1);
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
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

  it('opens Fusion’s "Solve in WG" as this same card, armed, and continues that one request', async () => {
    await mount();
    await deliver(operation('cmd-fusion', 'needs_user_input', {
      reason: 'frame_confirmation_required', stage: 'ready', attemptGeneration: 1,
      setupRevisionId: null, preparationId: 'wgp_1', message: 'Confirm this model’s solver frame in WG first.',
      updatedAt: '2026-09-22T10:00:02Z',
    }));
    // One card: the request's state is on it, and the only Solve is the card's.
    const cards = host.querySelectorAll('.cad-operation');
    expect(cards).toHaveLength(1);
    expect(cards[0].closest('.cad-solve-card')).not.toBeNull();
    expect(cards[0].textContent).toContain('Fusion asked for a solve');
    expect(cards[0].textContent).toContain('press Solve to confirm it');
    expect(solveButtons()).toHaveLength(1);
    expect(host.querySelector('[data-action="confirm-frame"]')).toBeNull();
    expect(solveButton().disabled).toBe(false);

    await pressSolve();
    // The same operation id: nothing created, nothing superseded.
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation.mock.calls[0][0]).toBe('cmd-fusion');
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+x' }]);
    await deliver(operation('cmd-fusion', 'accepted', { jobId: 'job-f', attemptGeneration: 2, updatedAt: '2026-09-22T10:00:09Z' }));
    await jobs([cadJob('job-f')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-f');
    expect(activations).toContain('results');
  });

  it('holds Solve on a Fusion request the backend is still preparing, then continues it at its gate and reveals its result', async () => {
    await mount();
    await deliver(operation('cmd-fusion', 'processing', { stage: 'preparing-mesh', updatedAt: '2026-09-22T10:00:01Z' }));
    // One card, with the request's progress; Solve held with that status,
    // the top bar's too.
    const card = host.querySelector('.cad-solve-card .cad-operation')!;
    expect(card.textContent).toContain('Preparing the request from Fusion…');
    expect(solveButton().disabled).toBe(true);
    expect(host.querySelector('.cad-solve-blocker')!.textContent).toBe('Preparing the request from Fusion…');
    const topBar = host.querySelector<HTMLButtonElement>('.topbar .solve-button')!;
    expect(topBar.disabled).toBe(true);
    expect(topBar.title).toBe('Preparing the request from Fusion…');
    // Every other caller of the one command resolves to that request.
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
    });
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).not.toHaveBeenCalled();
    expect(puts).toEqual([]);

    // It stops at the frame gate: Solve is the normal continuation.
    await deliver(operation('cmd-fusion', 'needs_user_input', {
      reason: 'frame_confirmation_required', stage: 'ready', attemptGeneration: 1,
      preparationId: 'wgp_1', updatedAt: '2026-09-22T10:00:03Z',
    }));
    await pressSolve();
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation.mock.calls[0][0]).toBe('cmd-fusion');
    expect(puts).toEqual([{ ingestId: 'wgi_first', axis: '+x' }]);
    await deliver(operation('cmd-fusion', 'accepted', { jobId: 'job-f', attemptGeneration: 2, updatedAt: '2026-09-22T10:00:09Z' }));
    await jobs([cadJob('job-f')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-f');
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
  });

  it('reveals the result of a request that finishes while Solve is held on it', async () => {
    await mount();
    await deliver(operation('cmd-fusion', 'processing', { updatedAt: '2026-09-22T10:00:01Z' }));
    expect(solveButton().disabled).toBe(true);
    await deliver(operation('cmd-fusion', 'accepted', { jobId: 'job-f', updatedAt: '2026-09-22T10:00:09Z' }));
    await jobs([cadJob('job-f')]);
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(compareSelection.getSnapshot().primary).toBe('job-f');
    expect(activations).toContain('results');
    expect(solveButton().disabled).toBe(false);
  });

  it('resolves a press that races a request going in flight to that request, never a second one', async () => {
    await mount();
    // The press starts with nothing in flight; while its settings are being
    // recorded, Fusion's request for this snapshot arrives and is prepared.
    mocks.putProjectSetup.mockImplementationOnce(async (request: { lineageId: string; setup: CadSolveSetup }) => {
      useCadOperationsStore.getState().apply(operation('cmd-race', 'processing', { updatedAt: '2026-09-22T10:00:01Z' }));
      return { lineageId: request.lineageId, inventorySha256: 'sha256:inv', revisionId: 'wgs_r' };
    });
    await pressSolve();
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).not.toHaveBeenCalled();
    // The press's arm went to that request: its result follows the user.
    await deliver(operation('cmd-race', 'accepted', { jobId: 'job-r', updatedAt: '2026-09-22T10:00:09Z' }));
    await jobs([cadJob('job-r')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-r');
    expect(activations).toContain('results');
  });

  it('lets Solve recover its own solve after a lost response: the same operation again, never a second', async () => {
    await mount();
    mocks.prepareCadOperation.mockRejectedValueOnce(new Error('connection closed'));
    await act(async () => { solveButton().click(); await flush(12); });
    const operationId = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    // Its own request, created and not yet prepared as far as this page knows.
    expect(useCadOperationsStore.getState().operations[operationId].state).toBe('received');
    expect(host.querySelector('.cad-solve-card .cad-operation')!.textContent).toContain('Preparing your solve…');
    await pressSolve();
    expect(mocks.createCadOperation.mock.calls.map((call) => call[0].operationId)).toEqual([operationId, operationId]);
    expect(mocks.prepareCadOperation.mock.calls.map((call) => call[0])).toEqual([operationId, operationId]);
  });

  it('solves and remembers the settings as edited, not as they were', async () => {
    await mount();
    act(() => {
      useCadReturnStore.getState().setSweep({ frequencyStartHz: 300, frequencyEndHz: 12_000, frequencyCount: 40 });
      useSolveOptionsStore.getState().setEngine('bempp');
    });
    await act(async () => { await flush(); });
    expect(host.querySelector('.cad-solve-settings > span')!.textContent).toBe('300 Hz–12 kHz · 40 freq · BEMPP · ~5 min');
    await pressSolve();
    const remembered = mocks.putProjectSetup.mock.calls[0][0].setup as CadSolveSetup;
    expect(remembered.options).toMatchObject({ frequency_range: [300, 12_000], num_frequencies: 40, engine: 'bempp' });
    const bound = revisions.get(mocks.prepareCadOperation.mock.calls[0][1].setupRevisionId as string)!;
    expect(bound.options).toMatchObject({ frequency_range: [300, 12_000], num_frequencies: 40, engine: 'bempp' });
  });

  it('acceptance: a Fusion request, its settings and engine changed in WG, then Solve -- one job with the displayed choices, and a retry reverts nothing', async () => {
    await mount();
    // Fusion's request, prepared by the backend from the project's recorded
    // setup (Metal, 24 frequencies), stops at the frame gate.
    const fusionSetup: CadSolveSetup = {
      schema_version: 1, geometry: {}, options: { engine: 'metal', num_frequencies: 24 },
    };
    revisions.set('wgs_fusion', fusionSetup);
    await deliver(operation('cmd-fusion', 'needs_user_input', {
      reason: 'frame_confirmation_required', stage: 'ready', attemptGeneration: 1,
      setupRevisionId: 'wgs_fusion', preparationId: 'wgp_1', updatedAt: '2026-09-22T10:00:02Z',
    }));
    act(() => {
      useCadReturnStore.getState().setSweep({ frequencyCount: 40 });
      useSolveOptionsStore.getState().setEngine('bempp');
    });
    await pressSolve();
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).toHaveBeenCalledTimes(1);
    const [id, request] = mocks.prepareCadOperation.mock.calls[0];
    expect(id).toBe('cmd-fusion');
    // The displayed choices are bound deliberately, not the request's.
    const bound = revisions.get(request.setupRevisionId as string)!;
    expect(bound.options).toMatchObject({ engine: 'bempp', num_frequencies: 40 });
    expect(request.setupRevisionId).not.toBe('wgs_fusion');

    // The preparation fails; Solve again is a retry of the same request with
    // the same inputs: it keeps the setup now bound and reverts nothing.
    await deliver(operation('cmd-fusion', 'needs_user_input', {
      reason: 'preparation_failed', stage: 'ready', attemptGeneration: 2,
      setupRevisionId: request.setupRevisionId as string, preparationId: null,
      message: 'Preparing the mesh failed: worker crashed', updatedAt: '2026-09-22T10:00:04Z',
    }));
    await pressSolve();
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).toHaveBeenCalledTimes(2);
    expect(mocks.prepareCadOperation.mock.calls[1]).toEqual(['cmd-fusion', { submit: true }]);
    expect(useSolveOptionsStore.getState().engine).toBe('bempp');
    // Remembered for the project as displayed, both times.
    expect(mocks.putProjectSetup.mock.calls.map((call) => (call[0].setup as CadSolveSetup).options.engine)).toEqual(['bempp', 'bempp']);

    // Exactly one job, with the displayed choices; its results are revealed.
    await deliver(operation('cmd-fusion', 'accepted', { jobId: 'job-1', attemptGeneration: 3, updatedAt: '2026-09-22T10:00:09Z' }));
    await jobs([cadJob('job-1', 'running', { solve_options: { engine: 'bempp' } as JobItem['solve_options'] })]);
    await jobs([cadJob('job-1', 'complete', { solve_options: { engine: 'bempp' } as JobItem['solve_options'] })]);
    expect(mocks.submitImported).not.toHaveBeenCalled();
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
  });

  it('keeps a continuation with unchanged settings on the setup the request holds (the control)', async () => {
    await mount();
    // The request already holds exactly the settings on screen: Solve keeps them.
    await pressSolve();
    const first = mocks.prepareCadOperation.mock.calls[0];
    const operationId = first[0] as string;
    await deliver(operation('cmd-held', 'needs_user_input', {
      reason: 'frame_confirmation_required', stage: 'ready', attemptGeneration: 1,
      setupRevisionId: first[1].setupRevisionId as string, preparationId: 'wgp_2', updatedAt: '2026-09-22T10:00:02Z',
      snapshot: { manifestSha256: MANIFEST, documentName: 'Speaker', projectLineageId: 'wgl_test' },
    }));
    await deliver(operation(operationId, 'accepted', { jobId: 'job-own', updatedAt: '2026-09-22T10:00:03Z' }));
    await pressSolve();
    expect(mocks.prepareCadOperation.mock.calls[1]).toEqual(['cmd-held', { submit: true }]);
  });

  it('binds the settings on screen when the setup a request holds cannot be read', async () => {
    await mount();
    await deliver(operation('cmd-fusion', 'needs_user_input', {
      reason: 'preparation_failed', stage: 'ready', attemptGeneration: 1,
      setupRevisionId: 'wgs_gone', preparationId: null, updatedAt: '2026-09-22T10:00:02Z',
    }));
    await pressSolve();
    expect(mocks.getSetupRevision).toHaveBeenCalledWith('wgs_gone');
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation.mock.calls[0]).toEqual(['cmd-fusion', { setupRevisionId: expect.any(String), submit: true }]);
  });

  it('shows a failure and a cancellation, and reveals nothing for them', async () => {
    await mount();
    await pressSolve();
    const operationId = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', updatedAt: '2026-09-22T10:00:05Z' }));
    await jobs([cadJob('job-1', 'error', { has_results: false, error_message: 'Out of memory at 12 kHz' })]);
    expect(host.querySelector('.cad-solve-run')!.textContent).toBe('Solve failed: Out of memory at 12 kHz');
    expect(activations).not.toContain('results');
    await jobs([cadJob('job-1', 'cancelled', { has_results: false, error_message: 'Cancelled by user' })]);
    expect(host.querySelector('.cad-solve-run')!.textContent).toBe('Solve cancelled: Cancelled by user');
    expect(activations).not.toContain('results');
  });

  it('never lets an older completion replace a newer workflow', async () => {
    await mount();
    await pressSolve();
    const first = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    await deliver(operation(first, 'accepted', { jobId: 'job-a', updatedAt: '2026-09-22T10:00:05Z' }));
    await jobs([cadJob('job-a', 'running')]);
    // Solve B while A still runs: a new run of its own.
    await pressSolve();
    expect(mocks.createCadOperation).toHaveBeenCalledTimes(2);
    const second = mocks.createCadOperation.mock.calls[1][0].operationId as string;
    expect(second).not.toBe(first);
    await deliver(operation(second, 'accepted', { jobId: 'job-b', updatedAt: '2026-09-22T10:00:07Z' }));
    await jobs([cadJob('job-b'), cadJob('job-a', 'running')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-b');
    const reveals = activations.filter((panel) => panel === 'results').length;
    // A finishes later: it must not take B's place.
    await jobs([cadJob('job-b'), cadJob('job-a')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-b');
    expect(activations.filter((panel) => panel === 'results')).toHaveLength(reveals);
  });
});
