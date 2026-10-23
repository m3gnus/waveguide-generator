import './cadlink/restores';
import { archivedRequest, job as archivedJob, json, mesh as archivedMesh, record as archivedRecord, workspaceFor } from './cadlink/selection.fixtures';
import { openImported, resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';

/** Reopen a CAD run as history does: `openImported` of the run, shown by the CAD rail. */
const restoreCadJobModel = (run: JobItem, display = {}, fetcher: typeof fetch = fetch) =>
  openImported({ kind: 'run', job: run, display }, fetcher).catch(() => false);
import { currentModel, currentModelStatus, runVerdict, shownResult, spatialResult } from '../model/currentModel';
import { previewSocket } from '../api/previewSocket';
import { currentDocumentLoad, serializeDesign } from '../stores/design';
import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadJobFixture, publishCadSummary } from '../jobs/cadSolve.fixtures';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary, CadSolveSetup } from '../api/cadOperations';
import { compareSelection } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import { SolvePlanRefused, SolveSubmissionRefused, type ImportedSolveSubmission, type ModelSolvePress } from '../jobs/actions';
import { SOLVE_PLAN_DEBOUNCE_MS } from '../jobs/planQueryPolicy';
import { bundleIdentity, resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resolveOuterBodyMode } from '../design/ParamPanel';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useImportedSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { solveOptionsStoreForMode, useScopedSolveOptions } from '../stores/solveOptionsContext';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import { useMeshImportStore } from '../stores/meshImport';
import { readLastModel, rememberLastModel } from '../stores/lastModel';
import type { RegisteredMesh } from '../api/meshImport';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { cadWorkspace } from '../stores/cadWorkspace';
import { useCadSolverFrameStore } from '../stores/cadSolverFrame';
import {
  JobsCoordinator,
  jobsCoordinatorBridge,
  solveControlStore,
  refreshedArchiveJob,
  useSolveControl,
  useOptionalSolveControl,
} from './JobsCoordinator';
import { SolveActions, TopBar } from './TopBar';
import { CadSolveCard } from './CadSolveCard';
import { CadCrossover } from '../design/CrossoverSection';
import { latestCombine } from '../results/latestCombine';
import { toWire } from '../results/crossoverSpec';
import { CrossoverAdvanced } from '../design/CrossoverAdvanced';
import { expandLegacy } from '../results/crossoverSpec';
import { JobsPanel } from './JobsPanel';
import { ResultsPanel } from './ResultsPanel';
import { resetSolveAttentionForTests, solveAttention } from './solveAttention';

const mocks = vi.hoisted(() => ({
  planSolveDesign: vi.fn(),
  submitDesign: vi.fn(),
  solveModel: vi.fn(),
  postImportedSolvePlan: vi.fn(),
  useRealImportedPlan: false,
  useRealSolvePlan: false,
  getCadOperation: vi.fn(),
  solvePlan: {
    engine: 'metal', formulation: 'full-3d' as const,
    reason: "explicit solver_mode='full_3d'", eligibility_reasons: [] as string[],
  } as { engine: string; formulation: 'axisymmetric' | 'full-3d'; reason: string; eligibility_reasons: string[] } | null,
  solvePlanError: null as string | null,
  solvePlanPending: false,
  // The server's verdict on the CAD return (POST /api/solve/imported-plan).
  importedPlan: {
    plan: {
      ingest_id: 'wgi_test', requested: 'auto', engine: 'metal' as string | null, code: null,
      reason: 'AUTO selected the first available engine', domain: 'full',
      engines: [{ name: 'metal', label: 'Metal', solves: true }],
    },
    error: null as string | null,
    isPending: false,
  },
  capabilities: {
    engines: [] as Array<{ name: string; available: boolean; reason: string | null; version: string | null; fast_paths: string[]; formulations?: string[]; mountings?: string[] }>,
    engineSelection: {
      default: 'auto', resolvedDefault: 'metal' as string | null,
      full3dOrder: ['metal', 'bempp', 'dryrun'],
    },
  },
}));

vi.mock('../api/cadOperations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/cadOperations')>();
  return { ...actual, getCadOperation: mocks.getCadOperation };
});

vi.mock('../jobs/actions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/actions')>();
  return {
    ...actual,
    planSolveDesign: mocks.planSolveDesign,
    submitDesign: mocks.submitDesign,
    solveModel: mocks.solveModel,
    postImportedSolvePlan: mocks.postImportedSolvePlan,
  };
});
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({
    engines: mocks.capabilities.engines,
    engineSelection: mocks.capabilities.engineSelection,
    error: null,
    isLoading: false,
  }),
  useCapabilityRefreshOnReconnect: () => undefined,
  useLegacyBeatEngineMigration: () => undefined,
}));
vi.mock('../jobs/useSolvePlan', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/useSolvePlan')>();
  return {
    useSolvePlan: (...args: Parameters<typeof actual.useSolvePlan>) => mocks.useRealSolvePlan
      ? actual.useSolvePlan(...args) : ({
        plan: mocks.solvePlan,
        error: mocks.solvePlanError,
        isPending: mocks.solvePlanPending,
      }),
  };
});
vi.mock('../jobs/useImportedSolvePlan', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/useImportedSolvePlan')>();
  return {
  useImportedSolvePlan: (enabled: boolean) => (
    mocks.useRealImportedPlan ? actual.useImportedSolvePlan(enabled)
      : enabled ? mocks.importedPlan : { plan: null, error: null, isPending: false }
  ),
  };
});

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as {
    snapshot: JobsSnapshot;
    listeners: Set<() => void>;
  };
  manager.snapshot = {
    connection: 'connected', epoch: 1, cursor: 1, jobs, error: null,
  };
  manager.listeners.forEach((listener) => listener());
}

function failedJob(): JobItem {
  return {
    id: 'failed-job', run_number: 1, parent_job_id: null,
    label: 'failed_run', status: 'error', progress: 1,
    stage: 'solve', stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: '2026-08-08T00:00:00Z',
    completed_at: '2026-08-08T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'], has_results: false,
    has_mesh_artifact: false, error_message: 'solver failed', cancellation_requested: false,
    mesh_stats: null,
    script_snapshot: {
      version: 1,
      design: { formula: 'OSSE', L: 120, a: 45, a0: 10, r0: 12.7, k: 1 },
    },
    design_revision: 7, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
  };
}

function importedSubmission(ingestId: string): ImportedSolveSubmission {
  return {
    geometry: {
      type: 'imported', ingest_id: ingestId, manifest_sha256: `sha256:m:${ingestId}`, artifact_sha256: `sha256:a:${ingestId}`,
      drive_channels: [{ id: 'drive', source_ids: ['source'], motion: 'normal' }],
      mesh: { rigid_size_mm: 8, transition_mm: 8, source_size_mm: { source: 4 } }, acknowledged_findings: [], skipped_source_ids: [], exterior_only: false,
    },
    options: {
      engine: 'metal', symmetry: 'auto', mesh_validation_mode: 'warn', verbose: false, frequency_spacing: 'log',
      frequency_range: [200, 20_000], num_frequencies: 24,
      polar_config: { angle_range: [0, 180, 37], angle_step: 5, distance: 2, norm_angle: 5, inclination: 45, enabled_axes: ['horizontal'], observation_origin: 'mouth', spherical_sampling: false, field_plane: true },
    },
  };
}

function operation(
  operationId: string,
  state = 'received',
  overrides: Partial<CadOperationSummary> = {},
): CadOperationSummary {
  return {
    operationId, kind: 'prepare_and_solve', state, stage: 'received', reason: null, message: null,
    jobId: null, attemptGeneration: 0, setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: `sha256:${'1'.repeat(64)}` }, legacy: false,
    createdAt: '2026-09-15T10:00:00Z', updatedAt: '2026-09-15T10:00:00Z',
    ...overrides,
  };
}

function readyCad(ingestId: string): CadReturnIngestRecord {
  const record = {
    ingest_id: ingestId,
    manifest_sha256: `sha256:${'1'.repeat(64)}`,
    artifact_sha256: `sha256:${'2'.repeat(64)}`,
    report_sha256: `sha256:${'3'.repeat(64)}`,
    findings: [], evidence: { fem_air_volumes: [] }, polar_grid_derivation: {},
  } as unknown as CadReturnIngestRecord;
  useCadReturnStore.setState({
    selectedBundle: {
      name: 'speaker.wgreturn', bundlePath: 'returns/speaker.wgreturn', modifiedAt: '2026-09-15T10:00:00Z',
      readable: true, documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 1,
      designIds: [], sources: [{ id: 'source-hf', role: 'source', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' }],
    },
    projectLineageId: 'wgl_test', ingestRecord: record, needsIngest: false,
    driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
    sourceSizesMm: { 'source-hf': 4 }, rigidSizeMm: 8, transitionMm: 12, skippedSourceIds: [],
  });
  importedMeshStore.setCad({ name: 'Fusion speaker', source: 'cad', ingestId } as ImportedMeshScene);
  return record;
}

function MainSolveButton() {
  const solve = useSolveControl();
  return <button disabled={solve.disabled} title={solve.title} onClick={solve.solve}>{solve.label}</button>;
}

/** Exercise the public command after putting the test design on screen. */
function runCurrentDesign(design: ReturnType<typeof designForFamily>, _revision?: number) {
  act(() => { if (useDesignStore.getState().design !== design) useDesignStore.getState().replaceDocument(design); });
  return solveControlStore.getSnapshot()!.solve();
}

/** The solve-options store of the workspace mode that is currently on screen. */
const activeOptions = () => solveOptionsStoreForMode(workspaceModeStore.getSnapshot().mode).getState();

function EngineSelect() {
  const options = useScopedSolveOptions();
  return <select value={options.engine} onChange={(event) => options.setEngine(event.target.value)}>
    <option value="auto">AUTO</option><option value="bempp">BEMPP</option><option value="metal">Metal</option>
  </select>;
}

describe('solve invocation mutex', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    mocks.useRealImportedPlan = false;
    mocks.useRealSolvePlan = false;
    preferencesStore.resetForTests();
    resetDocumentStore();
    useDocumentStore.getState().setDesignName('horn');
    resetDesignStore();
    resetCadReturnStore();

    resetSolveOptionsStore();
    sessionStorage.clear();
    resetSolveAttentionForTests();
    importedMeshStore.clear();
    workspaceModeStore.setImportedInput('cad');
    resetImportedModelStore();
    workspaceModeStore.setMode('parametric');
    mocks.capabilities.engines = [
      { name: 'metal', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'bempp', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'dryrun', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
    ];
    mocks.capabilities.engineSelection = {
      default: 'auto', resolvedDefault: 'metal',
      full3dOrder: ['metal', 'bempp', 'dryrun'],
    };
    mocks.solvePlan = {
      engine: 'metal', formulation: 'full-3d',
      reason: "explicit solver_mode='full_3d'", eligibility_reasons: [],
    };
    mocks.solvePlanError = null;
    mocks.solvePlanPending = false;
    mocks.planSolveDesign.mockResolvedValue(mocks.solvePlan);
    mocks.solveModel.mockReset().mockResolvedValue('job-cad');
    mocks.submitDesign.mockReset();

    mocks.getCadOperation.mockRejectedValue(new CadLinkApiError('Unknown CAD operation', [], 404));
    compareSelection.clear();
    publishJobs([]);
    resetCadReturnStore();
    importedMeshStore.clear();
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await act(async () => { root.render(<JobsCoordinator now={() => new Date(2026, 7, 12, 12)}><span>ready</span></JobsCoordinator>); });
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
    vi.clearAllMocks();
    workspaceModeStore.setImportedInput('cad');
    workspaceModeStore.setMode('parametric');
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it.each(['parametric', 'expression', 'file', 'cad'] as const)('%s: shows a completed solve with the exact submitted snapshot', async kind => {
    const parametric = kind === 'parametric' || kind === 'expression';
    const actions = await vi.importActual<typeof import('../jobs/actions')>('../jobs/actions');
    mocks.submitDesign.mockImplementation(actions.submitDesign);
    mocks.solveModel.mockImplementation(actions.solveModel);
    let completed: JobItem | undefined;
    const posts: string[] = [];
    const anchor = failedJob();
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST' && (url === '/api/solve' || /\/api\/models\/.+\/solve$/.test(url))) {
        posts.push(url);
        const body = JSON.parse(String(init.body));
        // Mock only the server's persistence projection: identity comes from
        // the real HTTP submission, never from modelForRun/currentModel.
        completed = { ...anchor, id: 'round-trip', status: 'complete', has_results: true,
          script_snapshot: parametric ? body.design_snapshot : anchor.script_snapshot,
          config_summary: parametric ? {} : { geometry_type: 'imported', import_origin: kind === 'file' ? 'mesh-file' : 'fusion' },
          ...(parametric ? {} : { cad_source: { archive_stem: null, design_id: null, document_name: null, lineage_id: null, manifest_sha256: null, return_state_hash: null, ingest_id: body.prepared_id,
            model_id: decodeURIComponent(url.split('/')[3]), solve_model_sha256: 'round-trip-sha' },
            solve_options: body.setup.options, cad_setup: body.setup.geometry }),
        };
        if (parametric) {
          if (kind === 'expression') expect(body.design.R).toEqual({ value: null, raw: '70*(1+cos(p))' });
          expect(body.design).toEqual(body.design_snapshot.design);
          expect(body.design.simulation.f1).toBe(useDesignStore.getState().design.simulation.f1);
          expect(body.design.simulation.num_frequencies).toBe(useDesignStore.getState().design.simulation.num_frequencies);
        }
        return json({ job_id: completed!.id });
      }
      if (url === '/api/results/round-trip') return json({ result_kind: 'parametric', result_contract_version: 1, frequencies: [1000], metadata: {} });
      return json({ checkout: { kind: 'source' } });
    }));
    act(() => {
      if (parametric) {
        const design = structuredClone(useDesignStore.getState().design);
        design._absent = ['simulation.f1', 'simulation.f2', 'simulation.num_frequencies'];
        useDesignStore.getState().replaceDocument(design);
        if (kind === 'expression') useDesignStore.getState().updateExpression('R', { value: null, raw: '70*(1+cos(p))' });
        expect(serializeDesign(design).simulation).toMatchObject({ f1: null, f2: null, num_frequencies: null });
      } else if (kind === 'cad') {
        Object.assign(readyCad('round-trip-cad'), { model_id: 'round-trip-model', solve_model_sha256: 'round-trip-sha' });
        workspaceModeStore.setMode('cad');
      } else {
        workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
        useMeshImportStore.setState({ busy: false, filename: 'round-trip.msh' });
        void openImported({ kind: 'file', prepared: { ingest_id: 'round-trip-file', model_id: 'mesh-file:round-trip',
          solve_model_sha256: 'round-trip-sha', manifest_sha256: 'sha256:mesh', artifact_sha256: 'sha256:file',
          source_tags: { group_2: 2 } } as unknown as RegisteredMesh });
        importedMeshStore.setFile({ name: 'round-trip.msh', source: 'file', ingestId: 'round-trip-file' } as ImportedMeshScene);
      }
    });
    // A real accepted preview for the document on screen.
    vi.spyOn(previewSocket, 'getSnapshot').mockReturnValue({ ...previewSocket.getSnapshot(),
      awaitingDocumentFrame: false, connection: 'connected', displayedRevision: useDesignStore.getState().designRevision,
      frame: { documentLoad: currentDocumentLoad() } as never });
    const before = currentModel();
    expect(before.viewportShowing).toBe(true);
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(posts).toEqual([parametric ? '/api/solve' : kind === 'file'
      ? '/api/models/mesh-file%3Around-trip/solve' : '/api/models/round-trip-model/solve']);
    expect(completed).toBeDefined();
    act(() => { publishJobs([completed!]); compareSelection.setPrimary(completed!.id); });
    expect(currentModel().key).toBe(before.key);
    expect(runVerdict(completed!, currentModel())).toBe('current');
    expect(shownResult(currentModel(), compareSelection.getSnapshot(), [completed!])).toMatchObject({ job: completed, canRender: true, ids: [completed!.id] });
    expect(spatialResult(currentModel(), compareSelection.getSnapshot(), [completed!])).toBe(completed);
    expect(currentModelStatus(currentModel(), [completed!]).kind).toBe('solved');
    await act(async () => {
      preferencesStore.update({ chartTypes: ['summary'] });
      root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>);
    });
    expect(host.querySelector('[data-result-primary]')?.getAttribute('data-result-primary')).toBe(completed!.id);
    expect(host.querySelector('.result-grid')).not.toBeNull();
    if (kind === 'expression') {
      const scalar = useDesignStore.getState().design.R;
      act(() => useDesignStore.getState().updateExpression('R', { value: null, raw: '90*(1+cos(p))' }));
      expect(useDesignStore.getState().design.R).toBe(scalar);
      expect(runVerdict(completed!, currentModel())).toBe('earlier-version');
      expect(spatialResult(currentModel(), compareSelection.getSnapshot(), [completed!])).toBeNull();
      expect(host.querySelector('.result-grid')).toBeNull();
    }
  });

  // CAD-only controls have no parametric/file counterpart. Every surface that
  // exists in each mode is exercised through its real UI, down to HTTP paths.
  const surfaceCases = [
    ...['top bar', 'palette', 'Cmd+Enter', 'CAD card', 'Results empty', 'Results popover', 'crossover'].map((surface) => ({ mode: 'cad', surface })),
    ...['top bar', 'palette', 'Cmd+Enter', 'Results empty', 'Results popover'].map((surface) => ({ mode: 'file', surface })),
    ...['top bar', 'palette', 'Cmd+Enter', 'Results popover'].map((surface) => ({ mode: 'parametric', surface })),
  ];
  it.each(surfaceCases)('$mode: $surface submits only the on-screen model endpoint', async ({ mode, surface }) => {
    if (surface === 'crossover') {
      act(() => root.unmount());
      root = createRoot(host);
      vi.useFakeTimers();
      mocks.useRealImportedPlan = true;
      mocks.postImportedSolvePlan.mockReset().mockResolvedValue(mocks.importedPlan.plan);
    }
    const actions = await vi.importActual<typeof import('../jobs/actions')>('../jobs/actions');
    mocks.submitDesign.mockImplementation(actions.submitDesign);
    mocks.solveModel.mockImplementation(actions.solveModel);
    const posts: Array<{ url: string; body: Record<string, unknown> }> = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (init?.method === 'POST' && (url === '/api/solve' || /\/api\/models\/.+\/solve$/.test(url))) {
        posts.push({ url, body: JSON.parse(String(init.body)) });
        return json({ job_id: 'table-run' });
      }
      if (url.includes('/results')) return json({ detail: 'No retained results in this test' }, 404);
      return json({ checkout: { kind: 'source' } });
    }));
    const tableClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      if (mode === 'cad') {
        readyCad('wgi_surface').model_id = 'wgl_test';
        workspaceModeStore.setMode('cad');
        useCadReturnStore.setState({ driveChannels: [
          { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
          { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
        ], sourceSizesMm: { 'source-mf': 4, 'source-hf': 4 },
          combineSpec: expandLegacy(['drive-mf', 'drive-hf'], [100]), combineEnabled: true });
      } else if (mode === 'file') {
        workspaceModeStore.setImportedInput('file');
        workspaceModeStore.setMode('cad');
        useMeshImportStore.setState({ busy: false, filename: 'surface.msh' });
        void openImported({ kind: 'file', prepared: {
          ingest_id: 'wgi_file', model_id: 'mesh-file:surface',
          manifest_sha256: 'sha256:mesh', artifact_sha256: 'sha256:surface', source_tags: { group_2: 2 },
        } as unknown as RegisteredMesh });
        importedMeshStore.setFile({ name: 'surface.msh', source: 'file', ingestId: 'wgi_file' } as ImportedMeshScene);
      }
      if (surface === 'Results popover') {
        publishJobs([{ ...failedJob(), id: 'older-run', status: 'complete', has_results: true }]);
        compareSelection.setPrimary('older-run');
      }
    });
    await act(async () => { root.render(<QueryClientProvider client={tableClient}><JobsCoordinator>
      {surface === 'top bar' ? <SolveActions/> : surface === 'palette' ? <TopBar onResetLayout={() => undefined}/>
        : surface === 'CAD card' ? <CadSolveCard record={useCadReturnStore.getState().ingestRecord!} label="Speaker"/>
          : surface.startsWith('Results') ? <ResultsPanel/>
            : surface === 'crossover' ? <CadCrossover/> : <span/>}
    </JobsCoordinator></QueryClientProvider>); });
    if (surface === 'crossover') await flushPlan();
    const sweepPlan = deferred<typeof mocks.importedPlan.plan>();
    if (surface === 'crossover') mocks.postImportedSolvePlan.mockReturnValueOnce(sweepPlan.promise);
    if (surface === 'crossover') act(() => latestCombine.publish({
      jobId: 'shown', channelId: 'combined', canApply: true, blockedReason: null, recall: null,
      solvedBandHz: [200, 20000], onApplied: () => undefined,
      combine: { ...toWire(expandLegacy(['drive-mf', 'drive-hf'], [100])), crossovers_hz: [100] } as never,
    }));
    if (surface === 'palette') {
      act(() => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'k', metaKey: true, bubbles: true })));
    }
    if (surface === 'Results popover') {
      const marker = host.querySelector<HTMLButtonElement>('button.result-context-marker')!;
      expect(marker).not.toBeNull();
      act(() => marker.click());
    }
    await act(async () => {
      if (surface === 'Cmd+Enter') window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true }));
      else {
        const button = surface === 'top bar' ? host.querySelector<HTMLButtonElement>('.solve-button') : surface === 'palette' ? document.querySelector<HTMLButtonElement>('#palette-solve')
          : surface === 'Results popover' ? [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'Solve this version')
            : surface === 'crossover' ? [...host.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.startsWith('Re-solve with sweep'))
              : [...host.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === 'Solve');
        expect(button).toBeTruthy();
        expect(button!.disabled, button!.title).toBe(false);
        button!.click();
      }
    });
    if (surface === 'crossover') {
      expect(posts).toHaveLength(0);
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      expect(posts).toHaveLength(0);
      await act(async () => { sweepPlan.resolve(mocks.importedPlan.plan); });
      await flushPlan();
    }
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toBe(mode === 'parametric' ? '/api/solve' : mode === 'file' ? '/api/models/mesh-file%3Asurface/solve' : '/api/models/wgl_test/solve');
    if (mode !== 'parametric') expect(posts[0].body.prepared_id).toBe(mode === 'file' ? 'wgi_file' : 'wgi_surface');
    else expect(posts[0].body.design).toMatchObject({ formula: useDesignStore.getState().design.formula });
    if (surface === 'crossover') expect((posts[0].body.setup as { options: { frequency_range: number[] } }).options.frequency_range[0]).toBe(100);
    latestCombine.publish(null);
    tableClient.clear();
  });

  it('regression: Results Solve continues the refused CAD model, never the open parametric design', async () => {
    const actions = await vi.importActual<typeof import('../jobs/actions')>('../jobs/actions');
    mocks.solveModel.mockImplementation(actions.solveModel);
    const fetcher = vi.fn<typeof fetch>(async () => json({ job_id: 'continued-cad' }));
    vi.stubGlobal('fetch', fetcher);
    act(() => { readyCad('wgi_refused_results').model_id = 'wgl_test'; workspaceModeStore.setMode('cad'); });
    const refused = cadJobFixture(operation('fusion-refused', 'needs_user_input', {
      jobId: 'refused-cad', reason: 'engine_unavailable', message: 'BEAT Metal needs preparing.',
    }));
    act(() => publishJobs([refused]));
    await act(async () => root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>));
    expect(host.textContent).toContain('Solve refused');
    expect(host.textContent).toContain('BEAT Metal needs preparing.');
    const solve = host.querySelector<HTMLButtonElement>('.empty-state .solve-button')!;
    expect(solve.disabled).toBe(false);
    await act(async () => solve.click());
    expect(mocks.solveModel).toHaveBeenCalledExactlyOnceWith('wgl_test', expect.objectContaining({ prepared_id: 'wgi_refused_results' }));
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url]) => url)).toEqual(['/api/models/wgl_test/solve']);
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it.each(['accepted', 'approval refused', 'continuation refused'])('CAD continuation holds the Solve lock through both requests: %s', async (outcome) => {
    const approval = deferred<Response>();
    const retry = vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue('retry-child');
    const release = vi.spyOn(solveAttention, 'releaseOperation');
    const fetcher = vi.fn<typeof fetch>(async (input) => String(input).endsWith('/approvals') ? approval.promise
      : outcome === 'continuation refused' ? json({ detail: 'continuation refused' }, 422) : json({ job_id: 'approved-child' }));
    vi.stubGlobal('fetch', fetcher);
    let continuation!: Promise<unknown>;
    await act(async () => {
      continuation = jobsCoordinatorBridge.getSnapshot().continueRun('refused-parent', {
        approvals: { preparation_id: 'reviewed-preparation', finding_ids: ['finding'] },
      }).catch((error: unknown) => error);
      expect(await jobsCoordinatorBridge.getSnapshot().continueRun('another-run')).toBeNull();
      expect(await solveControlStore.getSnapshot()!.solve()).toBe('busy');
    });
    expect(retry).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(solveAttention.operationArmed('refused-parent')).toBe(true);
    await act(async () => {
      approval.resolve(outcome === 'approval refused' ? json({ detail: 'wrong preparation' }, 422) : json({}));
      await continuation;
    });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(outcome === 'approval refused'
      ? ['/api/jobs/refused-parent/approvals'] : ['/api/jobs/refused-parent/approvals', '/api/jobs/refused-parent/solve-again']);
    if (outcome === 'accepted') {
      expect(compareSelection.getSnapshot().awaiting).toBe('approved-child');
      expect(solveAttention.operationArmed('approved-child')).toBe(true);
    } else {
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(outcome === 'approval refused' ? 'wrong preparation' : 'continuation refused');
      expect(release).toHaveBeenCalledWith('refused-parent');
      expect(solveAttention.operationArmed('refused-parent')).toBe(false);
    }
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().continueRun('next-run'); });
    expect(retry).toHaveBeenCalledExactlyOnceWith('next-run');
  });

  it.each(['parametric', 'file', 'retry'] as const)('%s uses plain acceptance for refusal, child selection, attention, refresh and locking', async (path) => {
    const refused = deferred<string>();
    const accepted = deferred<string>();
    const request = path === 'retry' ? vi.spyOn(jobsSocket, 'retryJob')
      : path === 'file' ? mocks.solveModel : mocks.submitDesign;
    request.mockReturnValueOnce(refused.promise).mockReturnValueOnce(accepted.promise);
    if (path === 'file') {
      act(() => {
        workspaceModeStore.setImportedInput('file');
        workspaceModeStore.setMode('cad');
        useMeshImportStore.setState({ busy: false, filename: 'standalone.msh' });
        void openImported({ kind: 'file', prepared: {
          ingest_id: 'wgi_file', model_id: 'mesh-file:sha256:original',
          manifest_sha256: 'sha256:mesh', artifact_sha256: 'sha256:original', source_tags: { group_2: 2 },
        } as unknown as RegisteredMesh });
        useImportedSolveOptionsStore.getState().setSweep({ startHz: 100, endHz: 200, count: 2 });
        importedMeshStore.setFile({ name: 'standalone.msh', source: 'file', ingestId: 'wgi_file' } as ImportedMeshScene);
      });
    }
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    const invoke = () => path === 'retry' ? jobsCoordinatorBridge.getSnapshot().continueRun('parent')
      : path === 'parametric' ? runCurrentDesign(designForFamily('OSSE'))
      : Promise.resolve(host.querySelector<HTMLButtonElement>('button')!.click());
    const arm = vi.spyOn(solveAttention, 'armSolve');
    const release = vi.spyOn(solveAttention, 'releaseSolve');
    const bind = vi.spyOn(solveAttention, 'bindRun');
    const blocked = path === 'retry' ? null : vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue('must-not-submit');
    const refresh = vi.mocked(jobsSocket.refresh);
    let refusal!: Promise<unknown>;
    await act(async () => {
      refusal = invoke().catch((error: unknown) => error);
      await Promise.resolve();
      expect(await jobsCoordinatorBridge.getSnapshot().continueRun('blocked')).toBeNull();
    });
    expect(request).toHaveBeenCalledOnce();
    expect(arm).toHaveBeenCalledOnce();
    expect(arm.mock.invocationCallOrder[0]).toBeLessThan(request.mock.invocationCallOrder[0]);
    await act(async () => { refused.reject(new Error('submission refused')); await refusal; });
    expect(release).toHaveBeenCalledOnce();
    expect(bind).not.toHaveBeenCalled();
    expect(refresh).not.toHaveBeenCalled();
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('submission refused');
    expect(compareSelection.getSnapshot().awaiting).toBeNull();
    solveAttention.bindRun('unrelated');
    expect(solveAttention.resultsReady('unrelated')).toBe('indicated');

    const refreshed = deferred<void>();
    refresh.mockReturnValueOnce(refreshed.promise);
    let submitting!: Promise<unknown>;
    await act(async () => { submitting = invoke(); await Promise.resolve(); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBeNull();
    expect(host.querySelector<HTMLButtonElement>('button')!.disabled).toBe(true);
    await act(async () => { accepted.resolve('new-child'); await accepted.promise; });
    expect(compareSelection.getSnapshot().awaiting).toBe('new-child');
    expect(bind).toHaveBeenCalledWith('new-child');
    expect(refresh).toHaveBeenCalledOnce();
    expect(bind.mock.invocationCallOrder.at(-1)!).toBeLessThan(refresh.mock.invocationCallOrder[0]);
    await act(async () => {
      expect(await jobsCoordinatorBridge.getSnapshot().continueRun('blocked')).toBeNull();
      refreshed.resolve(); await submitting;
    });
    if (blocked) expect(blocked).not.toHaveBeenCalled();
    expect(request).toHaveBeenCalledTimes(2);
    expect(host.querySelector<HTMLButtonElement>('button')!.disabled).toBe(false);
    expect(solveAttention.resultsReady('new-child')).toBe('revealed');
  });

  it('shares its plan with a viewport consumer in a separate dock root', async () => {
    const observed = { control: null as ReturnType<typeof useOptionalSolveControl> };
    function DockPlan() {
      observed.control = useOptionalSolveControl();
      return null;
    }
    const dock = document.createElement('div');
    const dockRoot = createRoot(dock);
    try {
      await act(async () => dockRoot.render(<DockPlan />));
      expect(observed.control?.plan).toBe(mocks.solvePlan);
      mocks.solvePlan = { ...mocks.solvePlan!, engine: 'bempp' };
      await act(async () => root.render(<JobsCoordinator><span>updated</span></JobsCoordinator>));
      expect(observed.control?.plan).toBe(mocks.solvePlan);
      mocks.solvePlan = null;
      mocks.solvePlanPending = true;
      await act(async () => root.render(<JobsCoordinator><span>pending</span></JobsCoordinator>));
      expect(observed.control?.plan).toBeNull();
    } finally {
      act(() => dockRoot.unmount());
    }
  });

  const flushPlan = async () => {
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  };
  it.each(['parametric', 'cad'] as const)('never reopens a saved model or changes the mode at startup in %s mode', async (mode) => {
    act(() => root.unmount());
    rememberLastModel({ model_id: 'mesh-file:sha256:gone', input: 'file' });
    localStorage.setItem('wg-saved-mesh-setup', JSON.stringify({ model_id: 'mesh-file:sha256:old', prepared_id: 'wgi_old' }));
    workspaceModeStore.setMode(mode);
    const fetcher = vi.fn<typeof fetch>(async () => new Response('{}', { status: 404 }));
    vi.stubGlobal('fetch', fetcher);
    root = createRoot(host);
    await act(async () => { root.render(<JobsCoordinator><span>ready</span></JobsCoordinator>); });
    await act(async () => { workspaceModeStore.setMode('parametric'); workspaceModeStore.setMode(mode); });
    // The coordinator holds no recovery of its own: that is `observeLastModel` in the CAD workspace owners.
    expect(fetcher.mock.calls.filter(([url]) => String(url).startsWith('/api/models'))).toHaveLength(0);
    expect(workspaceModeStore.getSnapshot().mode).toBe(mode);
    expect(readLastModel('file')).toEqual({ model_id: 'mesh-file:sha256:gone', input: 'file' });
  });
  const mountPlanning = async (mode: 'parametric' | 'cad') => {
    act(() => root.unmount());
    root = createRoot(host);
    vi.useFakeTimers();
    mocks.useRealSolvePlan = true;
    mocks.useRealImportedPlan = true;
    mocks.postImportedSolvePlan.mockReset().mockResolvedValue(mocks.importedPlan.plan);
    const fetcher = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify(mocks.solvePlan)));
    vi.stubGlobal('fetch', fetcher);
    mocks.submitDesign.mockResolvedValue('job-fresh');
    if (mode === 'cad') readyCad('wgi_select');
    workspaceModeStore.setMode(mode);
    const client = new QueryClient();
    await act(async () => {
      root.render(<QueryClientProvider client={client}><JobsCoordinator><EngineSelect/><MainSolveButton/></JobsCoordinator></QueryClientProvider>);
    });
    await flushPlan();
    expect(host.querySelector('button')!.disabled).toBe(false);
    return { client, fetcher };
  };
  const changeEngine = (engine = 'bempp') => act(() => {
    const select = host.querySelector('select')!;
    select.value = engine;
    select.dispatchEvent(new Event('change', { bubbles: true }));
  });
  const pressSolve = (press: string) => act(() => {
    if (press === 'click') host.querySelector('button')!.click();
    else window.dispatchEvent(new KeyboardEvent('keydown', {
      key: 'Enter', metaKey: press === 'Cmd+Enter', ctrlKey: press === 'Ctrl+Enter', bubbles: true, cancelable: true,
    }));
  });

  it('cancels a queued CAD Solve when File is selected within Imported mesh', async () => {
    const { client } = await mountPlanning('cad');
    const plan = deferred<typeof mocks.importedPlan.plan>();
    mocks.postImportedSolvePlan.mockReturnValueOnce(plan.promise);
    changeEngine();
    pressSolve('click');
    act(() => { importedMeshStore.beginIntent(); workspaceModeStore.setImportedInput('file'); });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS);
      plan.resolve({ ...mocks.importedPlan.plan, requested: 'bempp', engine: 'bempp' });
    });
    await flushPlan();
    expect(mocks.solveModel).not.toHaveBeenCalled();
    client.clear();
  });

  describe.each(['parametric', 'cad'] as const)('%s planning gap', (mode) => {
    it.each(['click', 'Cmd+Enter', 'Ctrl+Enter'])('queues one immediate Solve %s after a select change', async (press) => {
      const { client, fetcher } = await mountPlanning(mode);
      const parametric = deferred<Response>();
      const imported = deferred<typeof mocks.importedPlan.plan>();
      if (mode === 'parametric') fetcher.mockReturnValueOnce(parametric.promise);
      else mocks.postImportedSolvePlan.mockReturnValueOnce(imported.promise);
      changeEngine();
      expect(host.querySelector('button')!.disabled).toBe(false);
      pressSolve(press);
      pressSolve(press);
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      await act(async () => {
        parametric.resolve(new Response(JSON.stringify(mocks.solvePlan)));
        imported.resolve({ ...mocks.importedPlan.plan, requested: 'bempp', engine: 'bempp' });
      });
      await flushPlan();
      if (mode === 'parametric') {
        expect(mocks.submitDesign).toHaveBeenCalledOnce();
        expect(mocks.submitDesign.mock.calls[0][1].engine).toBe('bempp');
        expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string).options.engine).toBe('bempp');
      } else {
        expect(mocks.solveModel).toHaveBeenCalledOnce();
        expect((mocks.solveModel.mock.calls[0][1].setup as CadSolveSetup).options.engine).toBe('bempp');
        expect(JSON.parse(mocks.postImportedSolvePlan.mock.calls[1][0]).options.engine).toBe('bempp');
      }
      await flushPlan();
      expect(mocks.submitDesign.mock.calls.length + mocks.solveModel.mock.calls.length).toBe(1);
      client.clear();
    });

    it('cancels the intent on plan error and does not replay it after recovery', async () => {
      const { client, fetcher } = await mountPlanning(mode);
      if (mode === 'parametric') {
        fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ detail: 'settings refused' }), { status: 422 }));
      } else mocks.postImportedSolvePlan.mockRejectedValueOnce(new SolvePlanRefused('settings refused', 422));
      changeEngine();
      pressSolve('click');
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan();
      expect(host.querySelector('button')!.disabled).toBe(true);
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('settings refused');
      changeEngine('metal');
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan();
      expect(host.querySelector('button')!.disabled).toBe(false);
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      client.clear();
    });

    it('cancels the intent when the workspace mode changes', async () => {
      const { client } = await mountPlanning(mode);
      changeEngine();
      pressSolve('Ctrl+Enter');
      act(() => workspaceModeStore.setMode(mode === 'cad' ? 'parametric' : 'cad'));
      await flushPlan();
      act(() => workspaceModeStore.setMode(mode));
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan();
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      client.clear();
    });

    it('waits for the latest inputs when another select change races the pending response', async () => {
      const { client, fetcher } = await mountPlanning(mode);
      const oldParametric = deferred<Response>();
      const oldImported = deferred<typeof mocks.importedPlan.plan>();
      if (mode === 'parametric') fetcher.mockReturnValueOnce(oldParametric.promise);
      else mocks.postImportedSolvePlan.mockReturnValueOnce(oldImported.promise);
      changeEngine();
      pressSolve('click');
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      changeEngine('metal');
      await act(async () => {
        oldParametric.resolve(new Response(JSON.stringify(mocks.solvePlan)));
        oldImported.resolve({ ...mocks.importedPlan.plan, requested: 'bempp', engine: 'bempp' });
      });
      await flushPlan();
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan();
      if (mode === 'parametric') {
        expect(mocks.submitDesign).toHaveBeenCalledOnce();
        expect(mocks.submitDesign.mock.calls[0][1].engine).toBe('metal');
      } else {
        expect(mocks.solveModel).toHaveBeenCalledOnce();
        expect((mocks.solveModel.mock.calls[0][1].setup as CadSolveSetup).options.engine).toBe('metal');
      }
      client.clear();
    });

    it('cancels the intent if an input blocker appears before planning finishes', async () => {
      const { client } = await mountPlanning(mode);
      changeEngine();
      pressSolve('click');
      act(() => activeOptions().updatePolar({ angleStep: 0 }));
      expect(host.querySelector('button')!.disabled).toBe(true);
      act(() => activeOptions().updatePolar({ angleStep: 5 }));
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan();
      expect(host.querySelector('button')!.disabled).toBe(false);
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      client.clear();
    });
  });

  it('cancels a queued CAD Solve when the fresh verdict has no eligible engine', async () => {
    const { client } = await mountPlanning('cad');
    mocks.postImportedSolvePlan.mockResolvedValueOnce({ ...mocks.importedPlan.plan, engine: null, reason: 'unsupported settings' });
    changeEngine();
    pressSolve('click');
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    await flushPlan();
    expect(host.querySelector('button')!.disabled).toBe(true);
    changeEngine('metal');
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    await flushPlan();
    expect(host.querySelector('button')!.disabled).toBe(false);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    client.clear();
  });

  it('submits only once when Solve is invoked twice in the same tick', async () => {
    const pending = deferred<string>();
    mocks.submitDesign.mockReturnValue(pending.promise);
    const design = designForFamily('OSSE');
    let first!: Promise<unknown>;
    let second!: Promise<unknown>;

    await act(async () => {
      const run = runCurrentDesign;
      first = run(design);
      second = run(design);
      await second;
    });

    expect(mocks.submitDesign).toHaveBeenCalledTimes(1);
    await act(async () => {
      pending.resolve('job-one');
      await first;
    });
  });

  it('blocks an ineligible design when its explicit full-3D engine is offline', async () => {
    mocks.capabilities.engines = [
      { name: 'beat', available: false, reason: 'GPU backend is offline', version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'bempp', available: true, reason: null, version: '1', fast_paths: [], formulations: ['full-3d'] },
    ];
    mocks.solvePlan = null;
    mocks.solvePlanError = "Solve engine 'beat' is unavailable. GPU backend is offline";
    useSolveOptionsStore.setState({ engine: 'beat', solverMode: 'full_3d' });
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });

    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(true);
    expect(solve.title).toBe("Solve engine 'beat' is unavailable. GPU backend is offline");
    solve.click();
    expect(mocks.planSolveDesign).not.toHaveBeenCalled();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('rechecks the exact design at invocation before creating a job', async () => {
    mocks.planSolveDesign.mockRejectedValue(
      new Error("Solve engine 'beat' is unavailable. GPU backend is offline"),
    );

    await act(async () => {
      await expect(runCurrentDesign(designForFamily('FREEFORM')))
        .resolves.toBe('blocked');
    });

    expect(mocks.planSolveDesign).toHaveBeenCalledOnce();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('names the mounting-compatible engine selected by an AUTO full-3D plan', async () => {
    mocks.solvePlan = {
      engine: 'bempp', formulation: 'full-3d',
      reason: "explicit solver_mode='full_3d'", eligibility_reasons: [],
    };
    useSolveOptionsStore.setState({ engine: 'auto', solverMode: 'full_3d' });
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });

    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(false);
    expect(solve.title).toBe('Solve current design with AUTO (BEMPP)');
  });

  // A result picked by hand pins the primary slot, and pinning outlived the
  // solve that came after it: every later run finished into a rail that still
  // showed the old one. Pressing Solve is a request to see that solve, so the
  // submission claims the slot for its own run; shell/ResultsPanel hands it
  // over once that run has results.
  it('claims the primary slot for the run it submits', async () => {
    mocks.submitDesign.mockResolvedValue('fresh-run');
    compareSelection.setPrimary('pinned-run');

    await act(async () => { await runCurrentDesign(designForFamily('OSSE')); });

    expect(compareSelection.getSnapshot()).toMatchObject({
      primary: 'pinned-run', following: false, awaiting: 'fresh-run',
    });
  });

  it('submits again after the first invocation resolves', async () => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');

    await act(async () => { await runCurrentDesign(design); });
    await act(async () => { await runCurrentDesign(design); });

    expect(mocks.submitDesign).toHaveBeenCalledTimes(2);
    expect(mocks.submitDesign.mock.calls.map((call) => call[3].label)).toEqual(['horn1', 'horn2']);
  });

  // Choosing "Bare shell" and pressing Solve used to flip the Outer body
  // control straight back to "Thickened waveguide (freestanding)": the browser
  // wrote BEMPP's 5 mm closed-wall default into the live document before
  // submitting. The server applies that default to the run's own copy
  // (server/jobs/runtime/ `_apply_bempp_wall_default`), so pressing Solve
  // must leave the design exactly as the user left it -- on every engine.
  it.each(['bempp', 'metal', 'auto'])('does not edit the design when solving a bare shell on %s', async (engine) => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');
    design.mesh.wall_thickness = 0;
    design.enclosure.depth = 0;
    design.simulation.sim_type = 'freestanding';
    act(() => {
      useDesignStore.getState().replaceDocument(design);
      useSolveOptionsStore.getState().setEngine(engine);
    });
    const revision = useDesignStore.getState().designRevision;

    await act(async () => {
      const state = useDesignStore.getState();
      await runCurrentDesign(state.design, state.designRevision);
    });

    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(0);
    expect(resolveOuterBodyMode(useDesignStore.getState().design)).toBe('bare');
    // No revision bump means no autosave rewrite for a solve that changed
    // nothing.
    expect(useDesignStore.getState().designRevision).toBe(revision);
    expect(mocks.submitDesign).toHaveBeenCalledOnce();
    expect(mocks.submitDesign.mock.calls[0][0].mesh.wall_thickness).toBe(0);
    expect(mocks.submitDesign.mock.calls[0][3].designRevision).toBe(revision);
  });

  it.each(['half_xz', 'half_yz', 'quarter'] as const)('blocks native submission with persisted %s symmetry', async (symmetry) => {
    act(() => useSolveOptionsStore.getState().setSymmetry(symmetry));
    await expect(runCurrentDesign(designForFamily('OSSE-ARC')))
      .resolves.toBe('blocked');
    expect(solveControlStore.getSnapshot()!.disabled).toBe(true);
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('numbers each run of the design in sequence and never renames the design', async () => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');

    await act(async () => { await runCurrentDesign(design); });
    const changed = structuredClone(design);
    changed.simulation.f2 += 1_000;
    await act(async () => { await runCurrentDesign(changed); });
    await act(async () => { await runCurrentDesign(changed); });

    expect(mocks.submitDesign.mock.calls.map((call) => call[3].label)).toEqual(['horn1', 'horn2', 'horn3']);
    expect(useDocumentStore.getState().designName).toBe('horn');
  });

  it('does not overwrite a rename made while a submission was in flight', async () => {
    const pending = deferred<string>();
    mocks.submitDesign.mockReturnValue(pending.promise);
    let run!: Promise<unknown>;
    await act(async () => {
      run = runCurrentDesign(designForFamily('OSSE'));
      await Promise.resolve();
    });
    act(() => useDocumentStore.getState().setDesignName('user-choice'));

    await act(async () => {
      pending.resolve('job-one');
      await run;
    });

    expect(useDocumentStore.getState().designName).toBe('user-choice');
    // The completed run counted against the name it was submitted under, so
    // the renamed design starts its own numbering at 1.
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'horn', runSequenceNext: 2 });
  });

  it('numbers the core before the date suffix and leaves the design name alone', async () => {
    mocks.submitDesign.mockResolvedValue('job');
    preferencesStore.update({ runNameDatePosition: 'suffix' });
    const design = designForFamily('OSSE');

    await act(async () => { await runCurrentDesign(design); });
    const changed = structuredClone(design);
    changed.simulation.f2 += 1_000;
    await act(async () => { await runCurrentDesign(changed); });

    expect(mocks.submitDesign.mock.calls.map((call) => call[3].label)).toEqual(['horn1_260812', 'horn2_260812']);
    expect(useDocumentStore.getState().designName).toBe('horn');
  });

  it('releases the guard after a rejected submission', async () => {
    mocks.submitDesign
      .mockRejectedValueOnce(new Error('submit failed'))
      .mockResolvedValueOnce('job-two');
    const design = designForFamily('OSSE');

    await act(async () => {
      await expect(runCurrentDesign(design)).resolves.toBe('blocked');
    });
    await act(async () => { await runCurrentDesign(design); });

    expect(mocks.submitDesign).toHaveBeenCalledTimes(2);
  });

  const solveCad = async (ingestId: string) => {
    await act(async () => { readyCad(ingestId); workspaceModeStore.setMode('cad'); });
    return solveControlStore.getSnapshot()!.solve();
  };
  const press = (index = 0) => mocks.solveModel.mock.calls[index][1] as ModelSolvePress;

  it('routes imported submissions through the same invocation mutex', async () => {
    const pending = deferred<string>();
    mocks.solveModel.mockReturnValue(pending.promise);
    let first!: ReturnType<NonNullable<ReturnType<typeof solveControlStore.getSnapshot>>['solve']>;
    act(() => { readyCad('wgi_example'); workspaceModeStore.setMode('cad'); });
    await act(async () => {
      first = solveControlStore.getSnapshot()!.solve();
      expect(await solveControlStore.getSnapshot()!.solve()).toBe('busy');
      await runCurrentDesign(designForFamily('OSSE'));
    });
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    await act(async () => { pending.resolve('job-imported'); await first; });
  });

  // A typed refusal reaches the browser as "<reason_code>: <message>", which
  // names the condition without naming the fix. Translating it where every
  // imported Solve is made gives every entry point the advice.
  it('turns a passive-cardioid topology refusal into the CAD change that fixes it', async () => {
    mocks.solveModel.mockRejectedValue(new Error(
      'passive_cardioid_topology: coupled passive cardioid requires all PORT_EXIT patches in one drive channel',
    ));
    await act(async () => { await expect(solveCad('wgi_refused')).resolves.toBe('blocked'); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toMatch(/same drive channel/);
    expect(jobsCoordinatorBridge.getSnapshot().actionError).not.toMatch(/passive_cardioid_topology/);
  });

  // Imported geometry takes the same engine choice as a parametric design: the
  // user's choice goes to the server as it is, and the server resolves AUTO or
  // refuses an engine that cannot, with the reason. The formulation is not a
  // choice for imported geometry, so it is always full 3-D.
  it("sends an imported solve with the user's engine choice, in full 3-D", async () => {
    act(() => useImportedSolveOptionsStore.getState().setEngine('metal'));
    await act(async () => { await expect(solveCad('wgi_metal')).resolves.toBe('submitted'); });
    act(() => useImportedSolveOptionsStore.getState().setEngine('beat-cpu'));
    await act(async () => { await expect(solveCad('wgi_no_metal')).resolves.toBe('submitted'); });
    expect(mocks.solveModel).toHaveBeenCalledTimes(2);
    expect(press(0).setup!.options).toMatchObject({ engine: 'metal', solver_mode: 'full_3d', symmetry: 'auto' });
    expect(press(1).setup!.options).toMatchObject({ engine: 'beat-cpu', solver_mode: 'full_3d', symmetry: 'auto' });
  });

  // "No engine here can take this geometry" is a capability this machine
  // lacks, which callers must handle differently from a refusal of the
  // request itself -- so it arrives as its own type.
  it('reports an engine_unavailable refusal without its machine code', async () => {
    const message = 'No solve engine on this host that can solve imported geometry is available.';
    mocks.solveModel.mockRejectedValue(new SolveSubmissionRefused(message, 503, 'engine_unavailable'));
    let caught: unknown;
    await act(async () => { caught = await solveCad('wgi_capability').catch((error: unknown) => error); });
    expect(caught).toBe('blocked');
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(message);
  });

  // An unsupported engine answers a request that named one -- a verdict on
  // that request, not a missing capability -- so it stays an ordinary error.
  it.each([
    ['fem_required', 'fem_required: this return includes FEM air volumes'],
    ['imported_engine_unsupported', "imported_engine_unsupported: engine 'bempp' does not declare imported geometry; engines that do: metal"],
  ])('reports a %s refusal without its machine code', async (code, message) => {
    mocks.solveModel.mockRejectedValue(new SolveSubmissionRefused(message, 422, code));
    let caught: unknown;
    await act(async () => { caught = await solveCad('wgi_refused').catch((error: unknown) => error); });
    expect(caught).toBe('blocked');
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(message.slice(`${code}: `.length));
  });

  it('leaves no claim and no armed operation behind a refused press', async () => {
    mocks.solveModel.mockRejectedValueOnce(new SolveSubmissionRefused('setup_required: choose settings', 409, 'setup_required'));
    await act(async () => { await expect(solveCad('wgi_refused_claim')).resolves.toBe('blocked'); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toMatch('choose settings');
    const key = `cad-solve:manual-solve:${press().client_request_id}`;
    expect(sessionStorage.getItem(`wg2.cad.solve.claim.v1:${key}`)).toBeNull();
    expect(Object.keys(sessionStorage).filter((item) => item.startsWith('wg2.cad.solve.claim.v1:'))).toEqual([]);
    // A job that later shows up under that key is not taken for this press.
    await act(async () => publishJobs([cadJobFixture(operation(key.slice('cad-solve:'.length), 'accepted', { jobId: 'stray' }))]));
    expect(compareSelection.getSnapshot().awaiting).toBeNull();
  });

  it('keeps the claim when the answer was lost, since the job may exist', async () => {
    mocks.solveModel.mockRejectedValueOnce(new TypeError('connection closed'));
    await act(async () => { await expect(solveCad('wgi_lost_claim')).resolves.toBe('blocked'); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toMatch('connection closed');
    const key = `cad-solve:manual-solve:${press().client_request_id}`;
    expect(sessionStorage.getItem(`wg2.cad.solve.claim.v1:${key}`)).not.toBeNull();
  });

  it('names CAD solves from the same design name and numbers them in the same sequence', async () => {
    mocks.solveModel.mockImplementation(async (_model: string, body: ModelSolvePress) => `job-${body.client_request_id}`);
    for (const ingestId of ['wgi_first', 'wgi_first', 'wgi_second']) {
      await act(async () => { await solveCad(ingestId); });
      const claimed = cadJobFixture(operation(`manual-solve:${press(mocks.solveModel.mock.calls.length - 1).client_request_id}`, 'accepted', {
        jobId: `job-${press(mocks.solveModel.mock.calls.length - 1).client_request_id}` }));
      await act(async () => publishJobs([claimed]));
    }
    expect(mocks.solveModel.mock.calls.map((_call, index) => press(index).label)).toEqual(['Speaker1', 'Speaker2', 'Speaker3']);
  });

  it('sends one model Solve with its own press id, never a CAD command key', async () => {
    // cad-solve:<id> is the backend's own submission-key namespace for
    // Fusion's commands; a press names only its own id, and the server keys it.
    await act(async () => { await solveCad('wgi_command'); });
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    const [modelId, body] = mocks.solveModel.mock.calls[0] as [string, ModelSolvePress];
    expect(modelId).toBe('unfiled');
    expect(body.client_request_id).toMatch(/^cad-[0-9a-f-]+$/);
    expect(body).toMatchObject({ prepared_id: 'wgi_command', label: 'Speaker1' });
    expect(body.setup).toBeDefined();
    expect(body.setup_revision_id).toBeUndefined();
    expect(Object.keys(sessionStorage).filter((key) => key.includes('pending') || key.includes('press'))).toEqual([]);
  });

  it('submits a full CAD solve from the main control without mounting CadLinkPanel', async () => {
    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    readyCad(ingestId);

    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.textContent).toBe('Solve');
    act(() => workspaceModeStore.setMode('cad'));
    expect(solve.textContent).toBe('Solve');
    expect(solve.title).toContain('displayed CAD Link model');
    await act(async () => { solve.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(mocks.solveModel).toHaveBeenCalledOnce();
    expect(mocks.solveModel).toHaveBeenCalledWith('unfiled', expect.objectContaining({ prepared_id: ingestId }));
    expect(press().setup).toBeDefined();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('commits an Advanced frequency on one Solve click and plans only the complete draft', async () => {
    // Remount with the production planning hook. The other mutex tests use a
    // fixed plan, but this click regression needs the query's invalidation.
    act(() => root.unmount());
    root = createRoot(host);
    mocks.useRealImportedPlan = true;
    const plan = mocks.importedPlan.plan;
    mocks.postImportedSolvePlan.mockResolvedValue(plan);
    act(() => { readyCad('wgi_frequency'); workspaceModeStore.setMode('cad'); });
    useCadReturnStore.setState({
      driveChannels: [
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
        { id: 'drive-lf', source_ids: ['source-lf'], motion: 'normal' },
      ],
      combineEnabled: true,
      combineSpec: expandLegacy(['drive-hf', 'drive-lf'], [1_000]),
    });
    act(() => workspaceModeStore.setMode('cad'));
    function Editor() {
      const cad = useCadReturnStore();
      return <CrossoverAdvanced
        spec={cad.combineSpec!}
        memberLabel={(member) => member}
        onChange={(spec) => cad.setCombineSpec(spec)}
      />;
    }
    const client = new QueryClient();
    await act(async () => {
      root.render(<QueryClientProvider client={client}><JobsCoordinator><Editor/><SolveActions/></JobsCoordinator></QueryClientProvider>);
      await Promise.resolve();
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
    const solve = host.querySelector<HTMLButtonElement>('.solve-button')!;
    expect(solve.disabled, solve.title).toBe(false);
    const input = host.querySelector<HTMLInputElement>('[aria-label="Low-pass frequency in hertz"]')!;
    const plannedFrequencies = () => mocks.postImportedSolvePlan.mock.calls.map(([body]) => {
      const submission = JSON.parse(body as string) as ImportedSolveSubmission;
      return submission.geometry.combine?.channels?.['drive-hf'].lp?.fc_hz;
    });
    act(() => input.focus());
    for (const draft of ['8', '80', '800']) {
      act(() => {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, draft);
        input.dispatchEvent(new Event('input', { bubbles: true }));
      });
      expect(useCadReturnStore.getState().combineSpec!.channels['drive-hf'].lp!.fcHz).toBe(1_000);
      if (draft !== '800') {
        await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
        expect(plannedFrequencies()).not.toContain(Number(draft));
      }
    }
    await act(async () => {
      const down = new MouseEvent('mousedown', { bubbles: true, cancelable: true });
      solve.dispatchEvent(down);
      if (!down.defaultPrevented) input.blur();
      solve.click();
      await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    });
    expect(useCadReturnStore.getState().combineSpec!.channels['drive-hf'].lp!.fcHz).toBe(800);
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    const setup = mocks.solveModel.mock.calls[0][1].setup as CadSolveSetup;
    expect(JSON.stringify(setup)).toContain('"fc_hz":800');
    expect(plannedFrequencies()).toContain(800);
    expect(plannedFrequencies()).not.toContain(8);
    expect(plannedFrequencies()).not.toContain(80);
    expect(mocks.postImportedSolvePlan.mock.invocationCallOrder.at(-1)!)
      .toBeLessThan(mocks.solveModel.mock.invocationCallOrder[0]);
    client.clear();
  });

  // Review-2 probes: a rejected draft must stay on screen and block the
  // request, including when the browser blurs the field before the click.
  it.each(['0', '-5', ''])('blocks an invalid Advanced frequency draft (%s)', async (draft) => {
    act(() => root.unmount());
    root = createRoot(host);
    mocks.useRealImportedPlan = true;
    mocks.postImportedSolvePlan.mockResolvedValue(mocks.importedPlan.plan);
    act(() => { readyCad('wgi_invalid_frequency'); workspaceModeStore.setMode('cad'); });
    useCadReturnStore.setState({
      driveChannels: [
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
        { id: 'drive-lf', source_ids: ['source-lf'], motion: 'normal' },
      ],
      combineEnabled: true,
      combineSpec: expandLegacy(['drive-hf', 'drive-lf'], [1_000]),
    });
    act(() => workspaceModeStore.setMode('cad'));
    function Editor() {
      const cad = useCadReturnStore();
      return <CrossoverAdvanced spec={cad.combineSpec!} memberLabel={(member) => member}
        onChange={(spec) => cad.setCombineSpec(spec)}/>;
    }
    const client = new QueryClient();
    await act(async () => {
      root.render(<QueryClientProvider client={client}><JobsCoordinator><Editor/><SolveActions/></JobsCoordinator></QueryClientProvider>);
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
    const input = host.querySelector<HTMLInputElement>('[aria-label="Low-pass frequency in hertz"]')!;
    const solve = host.querySelector<HTMLButtonElement>('.solve-button')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, draft);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(async () => { input.blur(); solve.click(); await Promise.resolve(); });
    expect(input.value).toBe(draft);
    expect(input.getAttribute('aria-invalid')).toBe('true');
    expect(host.querySelector('.crossover-frequency-error')?.textContent).toMatch(/greater than 0 Hz/);
    expect(host.querySelector('.solve-notice-blocked')?.textContent).toMatch(/greater than 0 Hz/);
    expect(solve.disabled).toBe(true);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    act(() => {
      input.focus();
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    });
    expect(input.value).toBe('1000');
    expect(host.querySelector('.crossover-frequency-error')).toBeNull();
    expect(host.querySelector('.solve-notice-blocked')).toBeNull();
    client.clear();
  });

  it('uses the clicked settings while the fresh Advanced plan is pending', async () => {
    act(() => root.unmount());
    root = createRoot(host);
    mocks.useRealImportedPlan = true;
    mocks.postImportedSolvePlan.mockResolvedValue(mocks.importedPlan.plan);
    act(() => { readyCad('wgi_plan_snapshot'); workspaceModeStore.setMode('cad'); });
    useCadReturnStore.setState({
      driveChannels: [
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
        { id: 'drive-lf', source_ids: ['source-lf'], motion: 'normal' },
      ],
      combineEnabled: true,
      combineSpec: expandLegacy(['drive-hf', 'drive-lf'], [1_000]),
      frequencyCount: 24,
    });
    act(() => workspaceModeStore.setMode('cad'));
    function Editor() {
      const cad = useCadReturnStore();
      return <CrossoverAdvanced spec={cad.combineSpec!} memberLabel={(member) => member}
        onChange={(spec) => cad.setCombineSpec(spec)}/>;
    }
    const client = new QueryClient();
    await act(async () => {
      root.render(<QueryClientProvider client={client}><JobsCoordinator><Editor/><SolveActions/></JobsCoordinator></QueryClientProvider>);
    });
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 200)); });
    const input = host.querySelector<HTMLInputElement>('[aria-label="Low-pass frequency in hertz"]')!;
    const solve = host.querySelector<HTMLButtonElement>('.solve-button')!;
    act(() => {
      input.focus();
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, '800');
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const pending = deferred<typeof mocks.importedPlan.plan>();
    mocks.postImportedSolvePlan.mockReturnValueOnce(pending.promise);
    await act(async () => { solve.click(); await Promise.resolve(); });
    act(() => useCadReturnStore.setState({ frequencyCount: 71 }));
    await act(async () => { pending.resolve(mocks.importedPlan.plan); await pending.promise; });
    const setup = mocks.solveModel.mock.calls[0][1].setup as CadSolveSetup;
    expect(setup.options.num_frequencies).toBe(24);
    expect(JSON.stringify(setup)).toContain('"fc_hz":800');
    expect(useCadReturnStore.getState().frequencyCount).toBe(71);
    client.clear();
  });

  it('labels the durable setup from the CAD document, not the open parametric design', async () => {
    act(() => { readyCad('wgi_label'); workspaceModeStore.setMode('cad'); });
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    // The run's name is the press's; the setup itself carries none.
    expect(press().label).toBe('Speaker1');
    expect(press().setup!.label).toBeUndefined();
    expect(useDocumentStore.getState().designName).toBe('horn');
  });

  it('selects an accepted durable job and advances its CAD label once across duplicate events and reload', async () => {
    act(() => { readyCad('wgi_once'); workspaceModeStore.setMode('cad'); });
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const request = press();
    const accepted = cadJobFixture(operation(`manual-solve:${request.client_request_id}`, 'accepted', { jobId: 'job-cad' }));
    await act(async () => { publishJobs([accepted]); });
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceNext: 2 });
    expect(compareSelection.getSnapshot().awaiting).toBe('job-cad');
    const awaitRun = vi.spyOn(compareSelection, 'awaitRun');
    await act(async () => { publishJobs([{ ...accepted }]); });
    act(() => root.unmount());
    root = createRoot(host);
    await act(async () => { root.render(<JobsCoordinator><span>reloaded</span></JobsCoordinator>); });
    expect(awaitRun).not.toHaveBeenCalled();
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceNext: 2 });
  });

  it('binds the same request fields as the old imported submission builder', async () => {
    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    await act(async () => { readyCad(ingestId); workspaceModeStore.setMode('cad'); });
    const old = (await import('../jobs/importedSubmission')).buildImportedSubmission(useCadReturnStore.getState());
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const setup = mocks.solveModel.mock.calls[0][1].setup as CadSolveSetup;
    const {
      type: _type, ingest_id: _ingest, manifest_sha256: _manifest,
      artifact_sha256: _artifact, acknowledged_findings: _findings, ...oldGeometry
    } = old.geometry;
    expect(setup.geometry).toEqual(oldGeometry);
    expect(setup.options).toEqual(old.options);
  });

  function filedCad(ingestId: string): CadReturnIngestRecord {
    const record = { ...readyCad(ingestId), project: { lineage_id: 'wgl_test' }, model_id: 'wgl_test' } as CadReturnIngestRecord;
    const bundle = useCadReturnStore.getState().selectedBundle!;
    useCadReturnStore.setState({ ingestRecord: record, ingestedBundleIdentity: bundle ? bundleIdentity(bundle) : null });
    return record;
  }

  it('sends the press to the model it is filed under and claims the job the server answers', async () => {
    const record = filedCad('wgi_waiting');
    // A refused request for this snapshot is the server's to continue; its
    // reviewed findings ride with the press.
    act(() => publishCadSummary(operation('op-fusion', 'needs_user_input', {
      reason: 'findings_need_review', preparationId: 'wgi_prepared',
      snapshot: { manifestSha256: record.manifest_sha256, projectLineageId: 'wgl_test' },
    })));
    const waiting = jobsSocket.getSnapshot().jobs[0];
    act(() => publishJobs([{ ...waiting, cad_state: { ...waiting.cad_state!,
      preparation: { preparation_id: 'wgi_prepared', ingest_id: 'wgi_prepared', blocking_finding_ids: ['f1'], report_sha256: null },
      approvals: [{ preparation_id: 'wgi_prepared', finding_id: 'f1' }] } }]));
    mocks.solveModel.mockResolvedValueOnce('job-child');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(mocks.solveModel).toHaveBeenCalledWith('wgl_test', expect.objectContaining({
      prepared_id: 'wgi_waiting', label: 'Speaker1',
      approvals: { preparation_id: 'wgi_prepared', finding_ids: ['f1'] },
      setup: expect.objectContaining({ options: expect.objectContaining({ solver_mode: 'full_3d', symmetry: 'auto' }) }),
    }));
    const child = cadJobFixture(operation('op-fusion', 'accepted', { jobId: 'job-child' }), { parent_job_id: 'op-fusion' });
    await act(async () => publishJobs([child]));
    expect(compareSelection.getSnapshot().awaiting).toBe('job-child');
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceNext: 2 });
  });

  it('blocks Solve during the initial frame read without queuing a hidden solve', async () => {
    act(() => { readyCad('wgi_captured'); workspaceModeStore.setMode('cad'); });
    const read = deferred<Response>();
    act(() => { void useCadSolverFrameStore.getState().load('wgi_captured', (() => read.promise) as typeof fetch); });
    await expect(solveControlStore.getSnapshot()!.solve()).resolves.toBe('blocked');
    expect(solveControlStore.getSnapshot()!.title).toContain('Reading which way');
    await act(async () => { read.resolve(new Response(JSON.stringify({ linked: true }))); });
    expect(mocks.solveModel).not.toHaveBeenCalled();
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(press().prepared_id).toBe('wgi_captured');
  });

  it('caches no press: after a lost response the next press is a new id the server answers', async () => {
    act(() => { readyCad('wgi_lost'); workspaceModeStore.setMode('cad'); });
    mocks.solveModel.mockRejectedValueOnce(new Error('connection closed'));
    await act(async () => { await expect(solveCad('wgi_lost')).resolves.toBe('blocked'); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toMatch('connection closed');
    act(() => { root.unmount(); });
    root = createRoot(host);
    await act(async () => root.render(<JobsCoordinator><span>reloaded</span></JobsCoordinator>));
    await act(async () => { await solveCad('wgi_lost'); });
    expect(press(1).client_request_id).not.toBe(press(0).client_request_id);
    expect(press(1).setup).toEqual(press(0).setup);
  });

  it('keeps Solve available while a job for the model on screen is being prepared', async () => {
    const record = readyCad('wgi_lost_preparing');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    await act(async () => publishCadSummary(operation('op-fusion', 'processing', {
      jobId: 'job-fusion', snapshot: { manifestSha256: record.manifest_sha256 },
    })));
    // The server answers the job in flight for the same settings: no frontend hold.
    expect(host.querySelector<HTMLButtonElement>('button')!.disabled).toBe(false);
    mocks.solveModel.mockResolvedValueOnce('job-fusion');
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    // The press claims the job it was answered with: its completion is shown.
    await act(async () => publishJobs([cadJobFixture(operation('op-fusion', 'accepted', { jobId: 'job-fusion' }))]));
    expect(compareSelection.getSnapshot().awaiting).toBe('job-fusion');
  });

  it('a refused press leaves nothing behind, so corrected settings make a new one', async () => {
    mocks.solveModel.mockRejectedValueOnce(new SolveSubmissionRefused('request refused', 409, 'setup_required'));
    await act(async () => { await expect(solveCad('wgi_known_refusal')).resolves.toBe('blocked'); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toMatch('request refused');
    act(() => useCadReturnStore.setState({ frequencyCount: 42 }));
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(press(1).client_request_id).not.toBe(press(0).client_request_id);
    expect(press(1).setup!.options.num_frequencies).toBe(42);
  });

  it('holds rapid double presses and claims the job the server returned', async () => {
    act(() => { readyCad('wgi_double'); workspaceModeStore.setMode('cad'); });
    const pending = deferred<string>();
    mocks.solveModel.mockReturnValueOnce(pending.promise);
    let first!: ReturnType<NonNullable<ReturnType<typeof solveControlStore.getSnapshot>>['solve']>;
    await act(async () => {
      first = solveControlStore.getSnapshot()!.solve();
      expect(await solveControlStore.getSnapshot()!.solve()).toBe('busy');
    });
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve('job-child'); await first; });
    await act(async () => publishJobs([cadJobFixture(operation('op-parent', 'accepted', { jobId: 'job-child' }))]));
    expect(compareSelection.getSnapshot().awaiting).toBe('job-child');
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(press(1).client_request_id).not.toBe(press(0).client_request_id);
  });

  it('advances a lost-response label once when the jobs channel finds the accepted job', async () => {
    mocks.solveModel.mockRejectedValueOnce(new Error('connection closed'));
    await act(async () => { await expect(solveCad('wgi_terminal')).resolves.toBe('blocked'); });
    const request = press();
    const accepted = cadJobFixture(operation(`manual-solve:${request.client_request_id}`, 'accepted', { jobId: 'job-cad' }));
    await act(async () => publishJobs([accepted]));
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceNext: 2 });
    // Pressed again, the server answers that same job: it is not counted twice.
    mocks.solveModel.mockResolvedValueOnce('job-cad');
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceNext: 2 });
  });

  it('reports a refused job once after reload and never reads its operation', async () => {
    act(() => { readyCad('wgi_refused'); workspaceModeStore.setMode('cad'); });
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    act(() => root.unmount());
    root = createRoot(host);
    await act(async () => root.render(<JobsCoordinator><span>reloaded</span></JobsCoordinator>));
    const refused = cadJobFixture(operation('op-refused', 'rejected', { jobId: 'job-cad', message: 'The return is damaged.' }));
    await act(async () => publishJobs([refused]));
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toContain('The return is damaged.');
    expect(mocks.getCadOperation).not.toHaveBeenCalled();
  });

  it('shows a refusal of the Solve', async () => {
    act(() => { readyCad('wgi_refusal'); workspaceModeStore.setMode('cad'); });
    mocks.solveModel.mockRejectedValueOnce(new Error('refused with 409'));
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { host.querySelector<HTMLButtonElement>('button')!.click(); });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('refused with 409');
  });

  it('solves a restored CAD run again through its model, with the setup revision it recorded', async () => {
    const request = { ...archivedRequest(), options: { ...archivedRequest().options, symmetry: 'half_xz',
      ground_plane: { enabled: true, axis: 'y', height_m: 1.2 } } };
    const recorded = archivedJob();
    const fetcher = vi.fn<typeof fetch>(async input => String(input).endsWith('/workspace')
      ? json(workspaceFor(archivedRecord.ingest_id, request as never, { revision: 'wgs_recorded', modelId: 'wgl_archived' }))
      : String(input).endsWith('/viewport-mesh')
        ? new Response(archivedMesh) : json({ ...archivedRecord, model_id: 'wgl_archived' }));
    await act(async () => { expect(await restoreCadJobModel(recorded, {}, fetcher)).toBe(true); });
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    const accepted = deferred<string>();
    mocks.solveModel.mockReturnValueOnce(accepted.promise);
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(false);
    await act(async () => { solve.click(); solve.click(); });
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    // The CAD lane, as any Solve of the model: never a direct /api/solve.
    expect(mocks.solveModel).toHaveBeenCalledWith('wgl_archived', expect.objectContaining({
      prepared_id: archivedRecord.ingest_id, setup_revision_id: 'wgs_recorded',
    }));
    expect(press().setup).toBeUndefined();
    await act(async () => { accepted.resolve('retained-cad-run'); await accepted.promise; });
    await act(async () => publishJobs([cadJobFixture(operation('manual-solve:x', 'accepted', { jobId: 'retained-cad-run' }))]));
    expect(compareSelection.getSnapshot().awaiting).toBe('retained-cad-run');
  });

  it.each([
    ['setup_required', 'This run’s saved setup is no longer available. Change a setting, or open the model again, then press Solve.'],
    ['prepared_unavailable', 'This run’s prepared mesh is no longer available. Import or prepare the model again, then press Solve.'],
    ['model_mismatch', 'This run belongs to another model. Open that model, or import this one again, then press Solve.'],
  ])('says how to recover when a restored run is refused with %s', async (code, hint) => {
    const recorded = archivedJob();
    await act(async () => { await restoreCadJobModel(recorded, {}, (async input => String(input).endsWith('/workspace')
      ? json(workspaceFor(archivedRecord.ingest_id, archivedRequest(), { revision: 'wgs_gone' })) : String(input).endsWith('/viewport-mesh')
        ? new Response(archivedMesh) : json(archivedRecord)) as typeof fetch); });
    mocks.solveModel.mockRejectedValueOnce(new SolveSubmissionRefused(`${code}: refused`, 409, code));
    let caught: unknown;
    await act(async () => { caught = await solveControlStore.getSnapshot()!.solve().catch((error: unknown) => error); });
    expect(caught).toBe('blocked');
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(hint);
  });

  it('solves a restored CAD run from before setup revisions with the revision the server derived, and an edited one with its setup', async () => {
    await act(async () => { await restoreCadJobModel(archivedJob(), {}, (async input => String(input).endsWith('/workspace')
      ? json(workspaceFor(archivedRecord.ingest_id, archivedRequest(), { revision: 'wgs_derived' })) : String(input).endsWith('/viewport-mesh')
        ? new Response(archivedMesh) : json(archivedRecord)) as typeof fetch); });
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(press()).toMatchObject({ prepared_id: archivedRecord.ingest_id, setup_revision_id: 'wgs_derived' });
    expect(press().setup).toBeUndefined();
    // One edit: the press sends the setup on screen, a new revision.
    act(() => useImportedSolveOptionsStore.getState().setFrequencySpacing('linear'));
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const edited = mocks.solveModel.mock.calls.at(-1)![1] as ModelSolvePress;
    expect(edited.setup_revision_id).toBeUndefined();
    expect(edited.setup!.options).toMatchObject({ frequency_spacing: 'linear', frequencies_hz: [100, 500, 1000] });
    expect(edited.setup!.geometry).toMatchObject({ drive_channels: [{ id: 'archive-hf', source_ids: ['source-hf'] }] });
  });

  it('requires explicit setup before solving a standalone msh import', async () => {
    workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
    act(() => importedMeshStore.setFile({ name: 'inspection.msh', source: 'file' } as ImportedMeshScene));
    await act(async () => { root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>); });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(true);
    expect(solve.title).toContain('Choose units and driven groups');
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });

  it('solves the prepared file from the main command, with one model Solve and no CAD job', async () => {
    act(() => { readyCad('wgi_retained_cad'); workspaceModeStore.setMode('cad'); });
    workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
    const accepted = deferred<string>();
    mocks.solveModel.mockReturnValueOnce(accepted.promise);
    act(() => {
      useMeshImportStore.setState({ busy: false, filename: 'standalone.msh' });
      void openImported({ kind: 'file', prepared: { ingest_id: 'wgi_file', model_id: 'mesh-file:sha256:original', manifest_sha256: 'sha256:mesh',
        artifact_sha256: 'sha256:original', source_tags: { group_2: 2 } } as unknown as RegisteredMesh });
      useImportedSolveOptionsStore.getState().setSweep({ startHz: 100, endHz: 200, count: 2 });
      importedMeshStore.setFile({ name: 'standalone.msh', source: 'file', ingestId: 'wgi_file' } as ImportedMeshScene);
    });
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(false);
    await act(async () => { solve.click(); solve.click(); });
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    const [modelId, body] = mocks.solveModel.mock.calls[0] as [string, ModelSolvePress];
    expect(modelId).toBe('mesh-file:sha256:original');
    expect(body.prepared_id).toBe('wgi_file');
    expect(body.setup!.geometry).toMatchObject({ drive_channels: [{ id: 'group_2', source_ids: ['group_2'], motion: 'normal' }] });
    expect(body.setup!.geometry).not.toHaveProperty('mesh');
    expect(body.setup!.geometry).not.toHaveProperty('ingest_id');
    expect(body.label).toContain('standalone');
    expect(body.client_request_id).toMatch(/^mesh-/);
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    await act(async () => { accepted.resolve('file-run'); await accepted.promise; });
    expect(compareSelection.getSnapshot().awaiting).toBe('file-run');
    act(() => resetImportedModelStore());
  });

  it.each([null, 'wgs_restored'])('submits an unedited opened file setup verbatim after automatic global settings restoration (revision %s)', async (revision) => {
    workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
    const replay = importedSubmission('wgi_replay');
    delete replay.geometry.mesh;
    replay.options = { ...replay.options, solver_mode: 'auto', symmetry: 'auto', accuracy: 'accurate',
      frequencies_hz: [137, 211, 3987], frequency_spacing: 'linear',
      polar_config: { ...replay.options.polar_config, enabled_axes: ['vertical'], distance: 3.7, norm_angle: 17 } };
    const accepted = deferred<string>();
    mocks.solveModel.mockReturnValueOnce(accepted.promise);
    act(() => {
      const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...opened } = replay.geometry;
      useMeshImportStore.setState({ busy: false, filename: 'replayed.msh' });
      // What `openImported` leaves for a replay (no revision) or a reopened run (its revision).
      useImportedModelStore.setState({ input: 'file', phase: 'ready', modelId: 'mesh-file:replay',
        setup: structuredClone({ geometry: opened, options: replay.options }), restoredRevisionId: revision,
        prepared: { ingest_id: 'wgi_replay', model_id: 'mesh-file:replay', manifest_sha256: replay.geometry.manifest_sha256,
          artifact_sha256: replay.geometry.artifact_sha256, source_tags: { source: 2 } } as unknown as RegisteredMesh });
      importedMeshStore.setFile({ name: 'replayed.msh', source: 'file', ingestId: 'wgi_replay' } as ImportedMeshScene);
      useImportedSolveOptionsStore.setState({ engine: 'auto', accuracy: 'fast' });
    });
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(false);
    await act(async () => { solve.click(); });
    if (revision) {
      // An unedited restored run names the revision it recorded.
      expect(press()).toMatchObject({ prepared_id: 'wgi_replay', setup_revision_id: revision });
      expect(press().setup).toBeUndefined();
    } else {
      const { type: _type, ingest_id: _ingest, manifest_sha256: _manifest, artifact_sha256: _artifact,
        acknowledged_findings: _findings, ...geometry } = replay.geometry;
      expect(press().setup).toEqual({ schema_version: 1, geometry, options: replay.options });
    }
    expect(press().client_request_id).toMatch(/^mesh-/);
    await act(async () => { accepted.resolve('replayed-run'); await accepted.promise; });
    act(() => resetImportedModelStore());
  });

  // V2: Solve has one meaning. It solves the model and settings on screen;
  // Fusion reporting newer geometry never changes what it does or which
  // action is primary, and the button and the shortcut are the same command.
  it.each([
    ['Fusion reports newer geometry', true],
    ['Fusion reports nothing new (positive control for the same measurement)', false],
  ])('keeps Solve as the one primary action that solves the displayed model when %s', async (_name, fusionChangesAvailable) => {
    const pullAndSolve = vi.fn(async () => 'solving' as const);
    const pullFromFusion = vi.fn(async () => { throw new Error('not in this test'); });
    vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue({
      ...cadWorkspace.getSnapshot(),
      pullAndSolve,
      pullFromFusion,
      fusionStatus: {
        running: true, state: 'stale', fusionChangesAvailable,
        observationFreshness: 'current', documentChangeDetectable: true,
      } as never,
    });
    act(() => { readyCad('wgi_displayed'); workspaceModeStore.setMode('cad'); });
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    const buttons = [...host.querySelectorAll<HTMLButtonElement>('.solve-button')];
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toContain('Solve');
    expect(buttons[0].className).toBe('solve-button');
    expect(host.textContent).not.toContain('Pull from Fusion & Solve');

    await act(async () => { buttons[0].click(); await Promise.resolve(); await Promise.resolve(); });
    expect(pullAndSolve).not.toHaveBeenCalled();
    expect(pullFromFusion).not.toHaveBeenCalled();
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    expect(mocks.solveModel).toHaveBeenCalledWith('unfiled', expect.objectContaining({ prepared_id: 'wgi_displayed' }));
  });

  it('gives the keyboard shortcut the same meaning as the button while Fusion reports newer geometry', async () => {
    const pullAndSolve = vi.fn(async () => 'solving' as const);
    vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue({
      ...cadWorkspace.getSnapshot(),
      pullAndSolve,
      fusionStatus: { running: true, state: 'stale', fusionChangesAvailable: true, observationFreshness: 'current', documentChangeDetectable: true } as never,
    });
    act(() => { readyCad('wgi_displayed'); workspaceModeStore.setMode('cad'); });
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(pullAndSolve).not.toHaveBeenCalled();
    expect(mocks.solveModel).toHaveBeenCalledOnce();
    expect(mocks.solveModel).toHaveBeenCalledWith('unfiled', expect.objectContaining({ prepared_id: 'wgi_displayed' }));
  });

  it('offers the Fusion refresh as its own action on the source line, which never solves', async () => {
    const pullFromFusion = vi.fn(async () => ({}) as never);
    vi.spyOn(cadWorkspace, 'getSnapshot').mockReturnValue({
      ...cadWorkspace.getSnapshot(),
      pullFromFusion,
      fusionStatus: { running: true, state: 'stale', fusionChangesAvailable: true, observationFreshness: 'current', documentChangeDetectable: true } as never,
    });
    act(() => { readyCad('wgi_displayed'); workspaceModeStore.setMode('cad'); });
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    const line = host.querySelector('.cad-source-line')!;
    expect(line.textContent).toContain('Model loaded from Fusion · Newer CAD changes available');
    const refresh = [...line.querySelectorAll('button')].find((button) => button.textContent === 'Refresh')!;
    await act(async () => { refresh.click(); await Promise.resolve(); });
    expect(pullFromFusion).toHaveBeenCalledOnce();
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });

  it('enters CAD mode without an ingest and exposes the submission blocker', async () => {
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
      workspaceModeStore.setMode('cad');
    });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.textContent).toBe('Solve');
    expect(solve.disabled).toBe(true);
    expect(solve.title).toBe('Ingest a CAD return before solving.');
  });

  it.each([
    ['equal sweep endpoints', { angleStart: 0, angleEnd: 0 }],
    ['zero angular step', { angleStep: 0 }],
    ['short measurement distance', { distance: 0.05 }],
    ['no display planes', { enabledAxes: [] }],
    ['more than 721 samples', { angleStart: 0, angleEnd: 180, angleStep: 0.1 }],
  ])('disables the global Solve action in parametric and CAD modes for %s', async (_name, invalid) => {
    await act(async () => { root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>); });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    act(() => activeOptions().updatePolar(invalid));
    expect(solve.disabled).toBe(true);
    expect(solve.title).toMatch(/Directivity|directivity/);

    // Imported mode judges its own directivity settings, not the parametric ones.
    act(() => workspaceModeStore.setMode('cad'));
    act(() => useImportedSolveOptionsStore.getState().updatePolar(invalid));
    expect(solve.disabled).toBe(true);
    expect(solve.title).toMatch(/Directivity|directivity/);
  });

  it.each(['error', 'complete'] as const)('follows the retry child of a %s parent without reselecting the parent', async (status) => {
    const parent = { ...failedJob(), status, has_results: status === 'complete' };
    const pinned = { ...failedJob(), id: 'pinned', status: 'complete' as const, has_results: true };
    const child = { ...failedJob(), id: 'retry-child', parent_job_id: parent.id, status: 'queued' as const };
    const bindRun = vi.spyOn(solveAttention, 'bindRun');
    vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue(child.id);
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
    publishJobs([child, parent, pinned]);
    compareSelection.setPrimary(pinned.id);
    await act(async () => { root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>); });

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().continueRun(parent.id); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: pinned.id, awaiting: child.id });
    expect(bindRun).toHaveBeenCalledWith(child.id);

    await act(async () => { publishJobs([{ ...child, status: 'complete', has_results: true }, parent, pinned]); });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: child.id, awaiting: null });
  });

  it('selects a retry child that completes during the acceptance refresh', async () => {
    const parent = failedJob();
    const child = { ...parent, id: 'fast-child', parent_job_id: parent.id, status: 'complete' as const, has_results: true };
    vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue(child.id);
    vi.mocked(jobsSocket.refresh).mockImplementationOnce(async () => {
      expect(compareSelection.getSnapshot().awaiting).toBe(child.id);
      publishJobs([child, parent]);
    });
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}')));
    publishJobs([parent]);
    compareSelection.setPrimary('pinned');
    await act(async () => root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>));
    await act(async () => jobsCoordinatorBridge.getSnapshot().continueRun(parent.id));
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: child.id, awaiting: null });
  });

  it('reports a refresh error without losing the accepted child and unlocks the next press', async () => {
    vi.spyOn(jobsSocket, 'retryJob').mockResolvedValueOnce('accepted-child').mockResolvedValue('next-child');
    vi.mocked(jobsSocket.refresh).mockRejectedValueOnce(new Error('refresh failed'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().continueRun('parent')).rejects.toThrow('refresh failed');
    });
    expect(compareSelection.getSnapshot().awaiting).toBe('accepted-child');
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('refresh failed');
    expect(solveAttention.resultsReady('accepted-child')).toBe('revealed');
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().continueRun('next-parent')).resolves.toBe('next-child');
    });
  });

  it('leaves no Solve arm behind when a retry is refused', async () => {
    const parent = failedJob();
    vi.spyOn(jobsSocket, 'retryJob').mockRejectedValue(new Error('retry refused'));
    const bindRun = vi.spyOn(solveAttention, 'bindRun');
    publishJobs([parent]);
    await act(async () => { root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>); });

    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().continueRun(parent.id)).rejects.toThrow('retry refused');
    });
    solveAttention.bindRun('unrelated-run');
    expect(bindRun).toHaveBeenCalledWith('unrelated-run');
    expect(solveAttention.getReadyRun()).toBeNull();
  });

  it('guards two fast retries routed through the coordinator bridge', async () => {
    // A retry replays the stored run on the server rather than resubmitting the
    // design from the browser, so the guarded call here is retryJob.
    const pending = deferred<string>();
    const retryJob = vi.spyOn(jobsSocket, 'retryJob').mockReturnValue(pending.promise);
    publishJobs([failedJob()]);
    await act(async () => { root.render(<JobsCoordinator><JobsPanel/></JobsCoordinator>); });
    const retry = [...host.querySelectorAll('button')]
      .find((button) => button.textContent === 'Retry');

    expect(retry).toBeDefined();
    await act(async () => {
      retry!.click();
      retry!.click();
      await Promise.resolve();
    });

    expect(retryJob).toHaveBeenCalledTimes(1);
    expect(retryJob).toHaveBeenCalledWith('failed-job');
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    await act(async () => {
      pending.resolve('retry-child');
      await pending.promise;
      await Promise.resolve();
    });
  });
});

describe('archive completion refresh', () => {
  it('uses the canonical job snapshot after late artifact metadata arrives', async () => {
    const stale = failedJob();
    stale.status = 'complete';
    stale.has_results = true;
    stale.has_pressure_basis_artifact = false;
    stale.has_radiation_impedance_artifact = false;
    const complete = {
      ...stale,
      completed_at: '2026-08-20T16:53:37Z',
      has_pressure_basis_artifact: true,
      pressure_basis_artifact_bytes: 56_998,
      has_radiation_impedance_artifact: true,
      radiation_impedance_artifact_bytes: 3_584,
    };
    const refresh = vi.spyOn(jobsSocket, 'refresh').mockImplementation(async () => {
      publishJobs([complete]);
    });

    await expect(refreshedArchiveJob(stale)).resolves.toBe(complete);
    expect(refresh).toHaveBeenCalledOnce();
  });
});
