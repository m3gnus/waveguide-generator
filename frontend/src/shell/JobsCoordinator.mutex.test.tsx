import './cadlink/restores';
import { archivedRequest, job as archivedJob, json, mesh as archivedMesh, record as archivedRecord, workspaceFor } from './cadlink/selection.fixtures';
import { captureSolveInputs } from '../jobs/useRequestedSolve';
import { openImported, resetImportedModelStore, useImportedModelStore } from '../stores/importedModel';

/** Reopen a CAD run as history does: `openImported` of the run, shown by the CAD rail. */
const restoreCadJobModel = (run: JobItem, display = {}, fetcher: typeof fetch = fetch) =>
  openImported({ kind: 'run', job: run, display }, fetcher).catch(() => false);
import { currentModel, currentModelStatus, runVerdict, shownResult, spatialResult } from '../model/currentModel';
import { previewSocket } from '../api/previewSocket';
import { currentDocumentLoad, serializeDesign } from '../stores/design';
import { act, StrictMode } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cadJobFixture, publishCadSummary } from '../jobs/cadSolve.fixtures';
import { JobModelProtectionError, jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary, CadSolveSetup } from '../api/cadOperations';
import { compareSelection } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import type { SolveBlocker, EngineCapability } from '../jobs/actions';
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
import { saveMeshSetup, useMeshImportStore } from '../stores/meshImport';
import { readLastModel, rememberLastModel } from '../stores/lastModel';
import type { RegisteredMesh } from '../api/meshImport';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { cadWorkspace } from '../stores/cadWorkspace';
import { resetCadSolverFrameStore, useCadSolverFrameStore } from '../stores/cadSolverFrame';
import {
  JobsCoordinator,
  jobsCoordinatorBridge,
  solveControlStore,
  refreshedArchiveJob,
  useSolveControl,
  useOptionalSolveControl,
} from './JobsCoordinator';
import { CurrentModelStatus as CurrentReadiness } from './CurrentModelStatus';
import { SolveActions as ToolbarSolveActions, TopBar } from './TopBar';
import { CadSolveCard } from './CadSolveCard';
import { SolveOptionsControls } from '../design/SolveOptionsSections';
import { CadCrossover } from '../design/CrossoverSection';
import { latestCombine } from '../results/latestCombine';
import { toWire } from '../results/crossoverSpec';
import { CrossoverAdvanced } from '../design/CrossoverAdvanced';
import { expandLegacy } from '../results/crossoverSpec';
import { JobsPanel } from './JobsPanel';
import { workspaceNavigation } from './workspaceNavigation';
import { ResultsPanel } from './ResultsPanel';
import { resetSolveAttentionForTests, solveAttention } from './solveAttention';

// Exercise the toolbar alongside the same solve status that Results owns.
function SolveActions() {
  return <><div data-toolbar-solve><ToolbarSolveActions/></div><CurrentReadiness solveStatus/></>;
}

const mocks = vi.hoisted(() => ({
  capabilityGeneration: 1,
  lastCapabilityEngines: null as EngineCapability[] | null,
  refreshCapabilities: vi.fn(),
  prewarmSelectedEngine: vi.fn(),
  planSolveDesign: vi.fn(),
  submitDesign: vi.fn(),
  solveModel: vi.fn(),
  postImportedSolvePlan: vi.fn(),
  useRealImportedPlan: false,
  useRealSolvePlan: false,
  useRealCapabilities: false,
  getCadOperation: vi.fn(),
  solvePlan: {
    engine: 'metal', formulation: 'full-3d' as const,
    reason: "explicit solver_mode='full_3d'", eligibility_reasons: [] as string[],
  } as { engine: string; formulation: 'axisymmetric' | 'full-3d'; reason: string; eligibility_reasons: string[] } | null,
  solvePlanError: null as string | null,
  solveReadiness: null as SolveBlocker | null,
  solvePlanPending: false,
  // The server's verdict on the CAD return (POST /api/solve/imported-plan).
  importedPlan: {
    plan: {
      ingest_id: 'wgi_test', requested: 'auto', engine: 'metal' as string | null, code: null,
      reason: 'AUTO selected the first available engine', domain: 'full',
      readiness: null as SolveBlocker | null,
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
    prewarmSelectedEngine: mocks.prewarmSelectedEngine,
    planSolveDesign: mocks.planSolveDesign,
    submitDesign: mocks.submitDesign,
    solveModel: mocks.solveModel,
    postImportedSolvePlan: mocks.postImportedSolvePlan,
  };
});
vi.mock('../jobs/useCapabilities', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/useCapabilities')>();
  return {
    useCapabilities: () => {
      if (mocks.useRealCapabilities) return actual.useCapabilities();
      if (mocks.lastCapabilityEngines !== mocks.capabilities.engines) {
        mocks.lastCapabilityEngines = mocks.capabilities.engines;
        mocks.capabilityGeneration++;
      }
      return {
        capabilityGeneration: mocks.capabilityGeneration,
        engines: mocks.capabilities.engines,
        engineSelection: mocks.capabilities.engineSelection,
        error: null,
        refreshCapabilities: mocks.refreshCapabilities,
        isLoading: false,
      };
    },
    useActiveBackendCapability: () => null,
    usePlannedBackendCapabilities: () => [],
    useCapabilityRefreshOnReconnect: () => undefined,
    useLegacyBeatEngineMigration: () => undefined,
  };
});
vi.mock('../jobs/useSolvePlan', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/useSolvePlan')>();
  return {
    useSolvePlan: (...args: Parameters<typeof actual.useSolvePlan>) => mocks.useRealSolvePlan
      ? actual.useSolvePlan(...args) : ({
        plan: mocks.solvePlan,
        error: mocks.solvePlanError,
        readiness: mocks.solveReadiness,
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
    meshSizes: { ...useCadReturnStore.getState().meshSizes, source_size_mm: { 'source-hf': 4 }, rigid_size_mm: 8, transition_mm: 12 },   skippedSourceIds: [],
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
    mocks.refreshCapabilities.mockReset().mockResolvedValue(undefined);
    mocks.prewarmSelectedEngine.mockReset().mockResolvedValue(undefined);
    mocks.importedPlan.isPending = false;
    mocks.importedPlan.error = null;
    mocks.importedPlan.plan.readiness = null;
    mocks.importedPlan.plan.engine = 'metal';
    mocks.importedPlan.plan.reason = 'AUTO selected the first available engine';
    mocks.useRealImportedPlan = false;
    mocks.useRealSolvePlan = false;
    mocks.useRealCapabilities = false;
    preferencesStore.resetForTests();
    resetDocumentStore();
    useDocumentStore.getState().setDesignName('horn');
    resetDesignStore();
    resetCadReturnStore();
    resetCadSolverFrameStore();

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
    mocks.solveReadiness = null;
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
        ], meshSizes: { ...useCadReturnStore.getState().meshSizes, source_size_mm: { 'source-mf': 4, 'source-hf': 4 } },
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
      if (surface === 'CAD card') publishJobs([cadJobFixture(operation('previous-card-request', 'cancelled'), { status: 'cancelled' })]);
      if (surface === 'Results popover') {
        publishJobs([{ ...failedJob(), id: 'older-run', status: 'complete', has_results: true }]);
        compareSelection.setPrimary('older-run');
      }
    });
    await act(async () => { root.render(<QueryClientProvider client={tableClient}><JobsCoordinator>
      {surface === 'top bar' ? <ToolbarSolveActions/> : surface === 'palette' ? <TopBar onResetLayout={() => undefined}/>
        : surface === 'CAD card' ? <CadSolveCard record={useCadReturnStore.getState().ingestRecord!} label="Speaker"/>
          : surface.startsWith('Results') ? <ResultsPanel/>
            : surface === 'crossover' ? <CadCrossover/> : <span/>}
    </JobsCoordinator></QueryClientProvider>); });
    if (surface === 'crossover') await flushPlan(tableClient);
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
      await flushPlan(tableClient);
    }
    expect(posts).toHaveLength(1);
    expect(posts[0].url).toBe(mode === 'parametric' ? '/api/solve' : mode === 'file' ? '/api/models/mesh-file%3Asurface/solve' : '/api/models/wgl_test/solve');
    if (mode !== 'parametric') expect(posts[0].body.prepared_id).toBe(mode === 'file' ? 'wgi_file' : 'wgi_surface');
    else expect(posts[0].body.design).toMatchObject({ formula: useDesignStore.getState().design.formula });
    if (surface === 'crossover') expect((posts[0].body.setup as { options: { frequency_range: number[] } }).options.frequency_range[0]).toBe(100);
    latestCombine.publish(null);
    tableClient.clear();
  });

  it('prepares only the active selection, including model and mode changes', async () => {
    mocks.prewarmSelectedEngine.mockClear();
    act(() => useImportedSolveOptionsStore.getState().setEngine('beat-metal'));
    expect(mocks.prewarmSelectedEngine).not.toHaveBeenCalled();
    act(() => useSolveOptionsStore.getState().setEngine('beat-cpu'));
    expect(mocks.prewarmSelectedEngine).toHaveBeenCalledExactlyOnceWith('beat-cpu');
    act(() => { readyCad('wgi_prewarm_selection'); workspaceModeStore.setMode('cad'); });
    expect(mocks.prewarmSelectedEngine.mock.calls.at(-1)).toEqual(['beat-metal']);
    act(() => useImportedSolveOptionsStore.setState({ engine: 'bempp' })); // restored setup
    expect(mocks.prewarmSelectedEngine.mock.calls.at(-1)).toEqual(['bempp']);
  });

  it.each(['parametric', 'cad'] as const)('starts warm-up immediately from the %s engine picker', async mode => {
    act(() => {
      if (mode === 'cad') { readyCad('wgi_picker_prewarm'); workspaceModeStore.setMode('cad'); }
      mocks.capabilities.engines = [{ name: 'beat-cpu', available: false, runtime_state: 'unprepared',
        runtime_preparable: true, geometry_sources: ['parametric', 'imported'] } as EngineCapability];
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><JobsCoordinator>
      <SolveOptionsControls mode={mode}/>
    </JobsCoordinator></QueryClientProvider>));
    const picker = host.querySelector<HTMLSelectElement>(mode === 'cad' ? '#cad-solve-engine' : '#solve-engine')!;
    expect(picker.querySelector<HTMLOptionElement>('option[value="beat-cpu"]')!.disabled).toBe(false);
    act(() => { picker.value = 'beat-cpu'; picker.dispatchEvent(new Event('change', { bubbles: true })); });
    expect(mocks.prewarmSelectedEngine.mock.calls.at(-1)).toEqual(['beat-cpu']);
    expect(host.querySelector('.solve-readiness')).toBeNull();
    expect(host.querySelector('.beat-runtime-preparation')).toBeNull();
    client.clear();
  });

  const blocker: SolveBlocker = { code: 'engine_unavailable', engine: 'beat-metal',
    message: 'BEAT Metal needs preparing.', remedies: [
      { kind: 'prepare', engine: 'beat-metal', disabled_reason: null },
      { kind: 'use_engine', engine: 'metal', label: 'Use Metal-BEM (different solver — results will differ)', same_solver: false },
      { kind: 'use_engine', engine: 'beat-cpu', label: 'Use BEAT CPU', same_solver: true },
      { kind: 'settings', section: 'simulation' },
    ] };
  function blockedCad(held = false) {
    const record = readyCad('wgi_readiness');
    record.model_id = 'wgl_readiness';
    record.symmetry = { cut_planes: [] } as CadReturnIngestRecord['symmetry'];
    workspaceModeStore.setMode('cad');
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: null, reason: blocker.message, readiness: blocker };
    if (held) publishJobs([cadJobFixture(operation('held-fusion', 'needs_user_input', {
      reason: 'engine_unavailable', message: blocker.message, setupRevisionId: 'held-setup',
    }), { status: 'held', solve_options: { engine: 'beat-metal' } as JobItem['solve_options'] })]);
    return record;
  }
  it.each(['ordinary', 'Fusion'])('observes %s readiness after picker prewarm even when the cached capability has no preparation in flight', async source => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn(async () => json({ job_id: 'continued-fusion' })));
    act(() => {
      useSolveOptionsStore.setState({ engine: 'beat-metal' });
      mocks.solvePlan = null;
      mocks.solveReadiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      if (source === 'Fusion') {
        blockedCad(true);
        useImportedSolveOptionsStore.setState({ engine: 'beat-metal' });
        mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      }
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'unprepared' } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    expect(mocks.refreshCapabilities).toHaveBeenCalled();
    mocks.refreshCapabilities.mockClear();
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(mocks.refreshCapabilities.mock.calls.length).toBeGreaterThanOrEqual(2);
    mocks.solvePlan = { engine: 'beat-metal', formulation: 'full-3d', reason: 'ready', eligibility_reasons: [] };
    mocks.solveReadiness = null;
    if (source === 'Fusion') {
      mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
      mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    }
    await act(async () => render());
    if (source === 'ordinary') expect(mocks.submitDesign).toHaveBeenCalledOnce();
    else expect(vi.mocked(fetch).mock.calls.filter(([url]) => String(url).endsWith('/solve-again'))).toHaveLength(1);
    mocks.refreshCapabilities.mockClear();
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(mocks.refreshCapabilities).not.toHaveBeenCalled();
  });

  it.each(['parametric', 'cad'] as const)('regression: %s Prepare and solve resumes when the plan becomes ready before preparation finishes', async mode => {
    vi.useFakeTimers();
    const preparation = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(async () => preparation.promise));
    act(() => {
      if (mode === 'cad') blockedCad();
      else { mocks.solvePlan = null; mocks.solveReadiness = blocker; }
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'failed' } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    let preparing!: Promise<void>;
    await act(async () => { preparing = solveControlStore.getSnapshot()!.prepareAndSolve('beat-metal'); });
    expect(solveControlStore.getSnapshot()!.waitingForEngine).toBe(true); // retry tolerates the cached failure
    mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.solvePlan = { engine: 'beat-metal', formulation: 'full-3d', reason: 'ready', eligibility_reasons: [] };
    mocks.solveReadiness = null;
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => render());
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    expect(mocks.solveModel).not.toHaveBeenCalled();
    await act(async () => { preparation.resolve(json({}, 202)); await preparing; });
    expect(mode === 'cad' ? mocks.solveModel : mocks.submitDesign).toHaveBeenCalledOnce();
    expect(solveControlStore.getSnapshot()!.waitingForEngine).toBe(false);
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    expect(mode === 'cad' ? mocks.solveModel : mocks.submitDesign).toHaveBeenCalledOnce();
  });

  it.each([false, true])('regression: a warming Fusion request supersedes a blocked ordinary Solve with its saved setup (ready on arrival: %s)', async readyOnArrival => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>(async () => json({ job_id: 'fusion-child' }));
    vi.stubGlobal('fetch', fetcher);
    act(() => {
      blockedCad();
      if (readyOnArrival) mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      useImportedSolveOptionsStore.setState({ engine: 'beat-metal' });
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'preparing' } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    await act(async () => { expect(await solveControlStore.getSnapshot()!.solve()).toBe(readyOnArrival ? 'pending' : 'blocked'); });
    expect(solveControlStore.getSnapshot()!.readinessRequested).toBe(true);
    act(() => {
      blockedCad(true);
      const parent = jobsSocket.getSnapshot().jobs[0];
      publishJobs([{ ...parent, cad_state: { ...parent.cad_state!, blocker: { ...blocker, remedies: [{ kind: 'wait' }] } } }]);
      mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      if (readyOnArrival) {
        mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
        mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
      }
    });
    await act(async () => render());
    expect(solveControlStore.getSnapshot()!.waitingForEngine).toBe(!readyOnArrival);
    mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => render());
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
    const posts = fetcher.mock.calls.filter(([, init]) => init?.method === 'POST');
    // No browser setup or engine overrides: solve-again replays the Fusion job.
    expect(posts).toEqual([['/api/jobs/job%3Aheld-fusion/solve-again', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }]]);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    expect(solveControlStore.getSnapshot()!.waitingForEngine).toBe(false);
  });

  it.each(['pre-POST frame wait', 'POST in flight', 'POST done'] as const)(
    'Fusion survives an ordinary continuation in %s', async arrival => {
      vi.useFakeTimers();
      const frame = deferred<Response>();
      const ordinaryPost = deferred<string>();
      const posts: string[] = [];
      vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        if (init?.method === 'POST') posts.push(String(url));
        return json({ job_id: 'fusion-child' });
      }));
      mocks.solveModel.mockReturnValue(ordinaryPost.promise);
      act(() => {
        blockedCad();
        useCadSolverFrameStore.getState().apply('wgi_readiness', { linked: true });
        useImportedSolveOptionsStore.setState({ engine: 'beat-metal' });
        mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
        mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'preparing' } as EngineCapability];
      });
      const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
      await act(async () => render());
      await act(async () => { expect(await solveControlStore.getSnapshot()!.solve()).toBe('pending'); });
      let frameRead!: Promise<void>;
      act(() => { frameRead = useCadSolverFrameStore.getState().load('wgi_readiness', () => frame.promise); });
      mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
      mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
      await act(async () => render());
      expect(solveControlStore.getSnapshot()!.submitting).toBe(true);
      expect(mocks.solveModel).not.toHaveBeenCalled();
      if (arrival !== 'pre-POST frame wait') await act(async () => { frame.resolve(json({ linked: true })); await frameRead; });
      if (arrival === 'POST done') await act(async () => ordinaryPost.resolve('ordinary-job'));
      await act(async () => {
        const parent = cadJobFixture(operation('frame-fusion', 'needs_user_input', {
          reason: 'engine_unavailable', setupRevisionId: 'fusion-saved-setup',
        }), { solve_options: { engine: 'beat-metal' } as JobItem['solve_options'] });
        publishJobs([{ ...parent, cad_state: { ...parent.cad_state!, blocker: { ...blocker, remedies: [{ kind: 'wait' }] } } }]);
        render();
      });
      // Supersession aborts the ordinary frame wait and releases its mutex now,
      // while Fusion joins the shared frame read under its own request ID.
      expect(mocks.solveModel).toHaveBeenCalledTimes(arrival === 'pre-POST frame wait' ? 0 : 1);
      if (arrival === 'pre-POST frame wait') await act(async () => { frame.resolve(json({ linked: true })); await frameRead; });
      if (arrival === 'POST in flight') await act(async () => {
        const held = jobsSocket.getSnapshot().jobs[0];
        // The ordinary acceptance may become the newest displayed job before
        // its HTTP response. Fusion still owns the saved waiting parent by ID.
        publishJobs([{ ...held, id: 'ordinary-job', created_at: '2026-10-24T12:00:00Z', status: 'preparing',
          cad_state: { ...held.cad_state!, state: 'accepted' } }, held]);
        ordinaryPost.resolve('ordinary-job');
      });
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); render(); });
      expect(posts.filter(url => url.endsWith('/solve-again'))).toEqual(['/api/jobs/job%3Aframe-fusion/solve-again']);
      expect(mocks.solveModel).toHaveBeenCalledTimes(arrival === 'pre-POST frame wait' ? 0 : 1);
      expect(solveControlStore.getSnapshot()!.waitingForEngine).toBe(false);
      expect(solveControlStore.getSnapshot()!.readinessRequested).toBe(false);
    });

  // Exercise the real owner/adapter as a small event machine. Fusion arrives
  // before the ordinary request can submit; Stop/engine edits cancel that intent.
  const eventOrders = [
    ['Fusion', 'plan ready', 'preparation done'],
    ['Fusion', 'preparation done', 'plan ready'],
    ['plan ready', 'Fusion', 'preparation done'],
    ['preparation done', 'Fusion', 'plan ready'],
    ['plan ready', 'preparation done'],
    ['preparation done', 'plan ready'],
  ];
  const eventCases = eventOrders.flatMap(events => ['submit', 'engine change', 'Stop'].map(outcome => ({
    events: outcome === 'submit' ? events : [...events.slice(0, -1), outcome, events.at(-1)!],
    outcome,
  })));
  it.each(eventCases)('requested-solve event machine: press → $events ($outcome)', async ({ events, outcome }) => {
    vi.useFakeTimers();
    const preparation = deferred<Response>();
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).endsWith('/prepare')) return preparation.promise;
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'fusion-child' });
    }));
    act(() => {
      blockedCad();
      useImportedSolveOptionsStore.setState({ engine: 'beat-metal' });
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'preparing' } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    let preparing!: Promise<void>;
    await act(async () => { preparing = solveControlStore.getSnapshot()!.prepareAndSolve('beat-metal'); });
    let planReady = false;
    let preparationDone = false;
    let fusion = false;
    let cancelled = false;
    for (const event of events) {
      await act(async () => {
        if (event === 'Fusion') {
          fusion = true;
          const parent = cadJobFixture(operation('machine-fusion', 'needs_user_input', {
            reason: 'engine_unavailable', setupRevisionId: 'fusion-saved-setup',
          }), { solve_options: { engine: 'beat-metal' } as JobItem['solve_options'] });
          publishJobs([{ ...parent, cad_state: { ...parent.cad_state!, blocker: { ...blocker, remedies: [{ kind: 'wait' }] } } }]);
        }
        if (event === 'plan ready') {
          planReady = true;
          mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
        }
        if (event === 'preparation done') {
          preparationDone = true;
          preparation.resolve(json({}, 202));
          await preparing;
        }
        // Engine readiness follows both gates, so every order exercises a wait.
        if (planReady && preparationDone) mocks.capabilities.engines = [
          { name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
        if (event === 'engine change') {
          cancelled = true;
          useImportedSolveOptionsStore.getState().setEngine('metal');
          useImportedSolveOptionsStore.getState().setEngine('beat-metal'); // undo cannot resurrect the intent
        }
        if (event === 'Stop') { cancelled = true; solveControlStore.getSnapshot()!.stopWaiting(); }
        render();
      });
      const submissions = posts.filter(url => url.endsWith('/solve-again')).length + mocks.solveModel.mock.calls.length;
      expect(submissions).toBe(!cancelled && planReady && preparationDone ? 1 : 0);
    }
    await act(async () => { await vi.advanceTimersByTimeAsync(3100); render(); });
    expect(posts.filter(url => url.endsWith('/solve-again'))).toEqual(outcome === 'submit' && fusion
      ? ['/api/jobs/job%3Amachine-fusion/solve-again'] : []);
    expect(mocks.solveModel).toHaveBeenCalledTimes(outcome === 'submit' && !fusion ? 1 : 0);
    if (outcome === 'submit' && !fusion) expect(mocks.solveModel).toHaveBeenCalledWith('wgl_readiness',
      expect.objectContaining({ prepared_id: 'wgi_readiness', setup: expect.objectContaining({ options: expect.objectContaining({ engine: 'beat-metal' }) }) }));
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    expect(solveControlStore.getSnapshot()!.waitingForEngine).toBe(false);
    expect(solveControlStore.getSnapshot()!.readinessRequested).toBe(false);
  });

  it.each(['model', 'engine', 'setup', 'mode', 'stop', 'unmount'] as const)(
    'a Solve pressed before the plan arrives cannot migrate after %s changes', async change => {
      mocks.solvePlan = null; mocks.solvePlanPending = true;
      const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
      await act(async () => render());
      await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
      act(() => {
        if (change === 'model') useDesignStore.getState().replaceDocument({ ...designForFamily('OSSE'), L: 321 });
        if (change === 'engine') useSolveOptionsStore.getState().setEngine('bempp');
        if (change === 'setup') useSolveOptionsStore.getState().setSweep({ startHz: 123 });
        if (change === 'mode') { workspaceModeStore.setMode('cad'); workspaceModeStore.setMode('parametric'); }
        if (change === 'stop') solveControlStore.getSnapshot()!.stopWaiting();
        if (change === 'unmount') root.render(null);
      });
      mocks.solvePlanPending = false;
      mocks.solvePlan = { engine: 'metal', formulation: 'full-3d', reason: 'ready', eligibility_reasons: [] };
      await act(async () => { if (change !== 'unmount') render(); });
      expect(mocks.submitDesign).not.toHaveBeenCalled();
    });

  it.each(['Stop', 'Retry', 'delete', 'Clear failed'] as const)('reveals the single Results error home after a refused Runs %s action', async action => {
    const run = failedJob();
    if (action === 'Stop') run.status = 'running';
    const reason = `Server refused ${action}.`;
    const method = action === 'Stop' ? 'stopJob' : action === 'Retry' ? 'retryJob' : action === 'delete' ? 'deleteJob' : 'clearFailed';
    const request = vi.spyOn(jobsSocket, method).mockRejectedValue(new Error(reason));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const activate = vi.spyOn(workspaceNavigation, 'activate').mockImplementation(panel => {
      if (panel === 'results') host.querySelector<HTMLElement>('[data-results-tab]')!.hidden = false;
      return true;
    });
    publishJobs([run]);
    await act(async () => { root.render(<JobsCoordinator>
      <div data-results-tab hidden><ResultsPanel/></div><JobsPanel/>
    </JobsCoordinator>); });
    expect(host.querySelector<HTMLElement>('[data-results-tab]')!.hidden).toBe(true);
    const button = action === 'delete' ? host.querySelector<HTMLButtonElement>('.jobs-panel .job-remove')!
      : [...host.querySelectorAll<HTMLButtonElement>('.jobs-panel button')].find(button => button.textContent === action)!;
    expect(button).toBeDefined();
    await act(async () => { button.click(); });
    expect(request).toHaveBeenCalledTimes(1);
    expect(activate).toHaveBeenCalledWith('results');
    expect(host.querySelector<HTMLElement>('[data-results-tab]')!.hidden).toBe(false);
    expect(host.querySelector('.results-panel')?.textContent).toContain(reason);
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(reason);
    expect([...host.querySelectorAll('[role="alert"]')].filter(node => node.textContent?.includes(reason))).toHaveLength(1);
  });

  it.each(['Stop', 'Retry', 'delete', 'Clear failed'] as const)(
    'does not reveal a delayed Runs %s failure after the user navigates', async action => {
      const run = failedJob();
      if (action === 'Stop') run.status = 'running';
      const pending = deferred<never>();
      const method = action === 'Stop' ? 'stopJob' : action === 'Retry' ? 'retryJob' : action === 'delete' ? 'deleteJob' : 'clearFailed';
      vi.spyOn(jobsSocket, method).mockReturnValue(pending.promise);
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
      publishJobs([run]);
      await act(async () => { root.render(<JobsCoordinator><ResultsPanel/><JobsPanel/></JobsCoordinator>); });
      const button = action === 'delete' ? host.querySelector<HTMLButtonElement>('.job-remove')!
        : [...host.querySelectorAll<HTMLButtonElement>('.jobs-panel button')].find(button => button.textContent === action)!;
      act(() => { button.click(); workspaceNavigation.navigate('geometry'); });
      activate.mockClear();
      await act(async () => { pending.reject(new Error('Delayed refusal.')); });
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toContain('Delayed refusal.');
      expect(host.querySelector('.results-panel')?.textContent).toContain('Delayed refusal.');
      await act(async () => {
        window.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1 }));
        document.dispatchEvent(new FocusEvent('focusout', { bubbles: true }));
        await new Promise(resolve => setTimeout(resolve, 10));
      });
      expect(activate).not.toHaveBeenCalled();
    });

  it.each(['input', 'textarea', 'select', 'contenteditable', 'textbox', 'drag', 'pointercancel', 'two pointers', 'leave app while editing'] as const)(
    'never reveals an action error received during %s, even after the interaction ends', async interaction => {
      const run = { ...failedJob(), status: 'running' as const };
      const pending = deferred<never>();
      vi.spyOn(jobsSocket, 'stopJob').mockReturnValue(pending.promise);
      const activate = vi.spyOn(workspaceNavigation, 'activate').mockReturnValue(true);
      publishJobs([run]);
      await act(async () => { root.render(<JobsCoordinator>
        <ResultsPanel/><JobsPanel/><input/><textarea/><select/><div contentEditable tabIndex={0}/><div role="textbox" tabIndex={0}/><canvas/>
      </JobsCoordinator>); });
      const button = [...host.querySelectorAll<HTMLButtonElement>('.jobs-panel button')].find(button => button.textContent === 'Stop')!;
      act(() => button.click());
      const pointer = ['drag', 'pointercancel', 'two pointers'].includes(interaction);
      const editing = host.querySelector<HTMLElement>(interaction === 'textarea' ? 'textarea'
        : interaction === 'select' ? 'select' : interaction === 'textbox' ? '[role="textbox"]'
        : interaction === 'contenteditable' ? '[contenteditable]' : 'input')!;
      const canvas = host.querySelector('canvas')!;
      act(() => {
        if (pointer) {
          canvas.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1 }));
          if (interaction === 'two pointers') canvas.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 2 }));
        } else editing.focus();
      });
      await act(async () => { pending.reject(new Error('Stop refused.')); });
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toContain('Stop refused.');
      expect(host.querySelector('.results-panel')?.textContent).toContain('Stop refused.');
      expect(activate).not.toHaveBeenCalled();
      vi.useFakeTimers();
      if (interaction === 'leave app while editing') {
        act(() => window.dispatchEvent(new Event('blur')));
        await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60 * 1000); });
        act(() => window.dispatchEvent(new Event('focus')));
      }
      await act(async () => {
        if (pointer) {
          canvas.dispatchEvent(new PointerEvent(interaction === 'pointercancel' ? 'pointercancel' : 'pointerup', { bubbles: true, pointerId: 1 }));
        } else editing.blur();
        await vi.advanceTimersByTimeAsync(10);
      });
      if (interaction === 'two pointers') {
        expect(activate).not.toHaveBeenCalled();
        await act(async () => {
          canvas.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 2 }));
          await vi.advanceTimersByTimeAsync(10);
        });
      }
      expect(activate).not.toHaveBeenCalled();
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('Stop refused.');
    });

  it('shows a failed Delete anyway inside the still-open confirmation dialog', async () => {
    const run = failedJob();
    const remove = vi.spyOn(jobsSocket, 'deleteJob')
      .mockRejectedValueOnce(new JobModelProtectionError('The model could not be protected.'))
      .mockRejectedValueOnce(new Error('Server refused deletion.'));
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    publishJobs([run]);
    await act(async () => { root.render(<JobsCoordinator><ResultsPanel/><JobsPanel/></JobsCoordinator>); });
    await act(async () => { host.querySelector<HTMLButtonElement>('.job-remove')!.click(); });
    await act(async () => {
      [...document.querySelectorAll<HTMLButtonElement>('[role="dialog"] button')]
        .find(button => button.textContent === 'Delete anyway')!.click();
    });
    expect(remove).toHaveBeenLastCalledWith(run.id, { skipModelProtection: true });
    expect(document.querySelector('[role="dialog"] [role="alert"]')?.textContent).toContain('Server refused deletion.');
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toContain('Server refused deletion.');
  });

  it('keeps readiness and action errors in Results when the other solve surfaces are mounted', async () => {
    const record = blockedCad();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><JobsCoordinator>
      <div className="topbar"><ToolbarSolveActions/></div>
      <ResultsPanel/><CadSolveCard record={record} label="Speaker"/>
      <div data-settings><SolveOptionsControls mode="cad" ingestRecord={record}/></div>
      <CadCrossover/><JobsPanel/>
    </JobsCoordinator></QueryClientProvider>));
    expect(host.querySelectorAll('.solve-readiness')).toHaveLength(0);
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    act(() => jobsCoordinatorBridge.getSnapshot().reportError('The requested solve could not be submitted.'));
    expect(host.querySelectorAll('.solve-readiness')).toHaveLength(1);
    const progress = host.querySelector('.results-panel .solve-readiness')!;
    expect(progress.textContent).toContain(blocker.message);
    expect([...progress.querySelectorAll('button')].every(button => button.classList.contains('btn'))).toBe(true);
    expect([...host.querySelectorAll('[role="alert"]')].filter(node => node.textContent === 'The requested solve could not be submitted.')).toHaveLength(1);
    expect(host.querySelector('.topbar .current-model-status')).toBeNull();
    expect(host.querySelector('[data-settings] .current-model-status')).toBeNull();
    expect(host.querySelector('.section-note .current-model-status')).toBeNull();
    expect(host.querySelector('.cad-solve-card .solve-readiness')).toBeNull();
    client.clear();
  });

  it.each(['never-solved', 'failed'] as const)('lays out the %s Results title, detail and action separately without toolbar status', async kind => {
    if (kind === 'failed') {
      const run = failedJob();
      run.script_snapshot = { version: 1, design: serializeDesign(useDesignStore.getState().design) };
      run.error_message = 'Mesh build could not finish.';
      act(() => publishJobs([run]));
    }
    await act(async () => root.render(<JobsCoordinator>
      <div className="topbar"><ToolbarSolveActions/></div><ResultsPanel/>
    </JobsCoordinator>));
    const status = host.querySelector('.results-panel .current-model-status')!;
    const title = kind === 'failed' ? 'Solve failed' : 'Not solved yet';
    const detail = kind === 'failed' ? 'Mesh build could not finish.' : 'This version of the model has not been solved.';
    expect(status.querySelector('.notice-title')?.textContent).toBe(title);
    expect(status.querySelector('.notice-detail')?.textContent).toBe(detail);
    expect(status.textContent).toBe(`${title}\n${detail}\n${kind === 'failed' ? 'Retry' : 'Solve'}`);
    expect(status.querySelector('.notice-actions button')?.className).toBe(kind === 'failed' ? 'btn' : 'btn-primary');
    expect(host.querySelector('.topbar')?.textContent).not.toMatch(/failed|Not solved/i);
  });

  it('shows a rejected CAD diagnostic once in Results, including its contextual action error', async () => {
    act(() => {
      readyCad('wgi_rejected');
      workspaceModeStore.setMode('cad');
      publishJobs([cadJobFixture(operation('fusion-rejected', 'rejected', {
        reason: 'preparation_failed', message: 'Could not build the surface mesh.',
      }))]);
    });
    await act(async () => root.render(<JobsCoordinator>
      <div className="topbar"><ToolbarSolveActions/></div><ResultsPanel/><JobsPanel/>
    </JobsCoordinator>));
    const results = host.querySelector('.results-panel')!;
    expect(results.textContent?.match(/Could not build the surface mesh\./g)).toHaveLength(1);
    expect(results.textContent?.match(/Solve refused/g)).toHaveLength(1);
    expect(host.querySelector('.topbar')?.textContent).not.toMatch(/refused|mesh|failed/i);
    expect(host.querySelectorAll('.solve-readiness')).toHaveLength(0);
  });

  it.each(['top bar', 'Results empty', 'CAD card', 'crossover', 'Simulation'])('readiness parity: %s stays quiet until Solve is requested', async surface => {
    let record!: CadReturnIngestRecord;
    act(() => {
      record = blockedCad();
      useCadReturnStore.setState({ driveChannels: [
        { id: 'drive-mf', source_ids: ['source-mf'], motion: 'normal' },
        { id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' },
      ], meshSizes: { ...useCadReturnStore.getState().meshSizes, source_size_mm: { 'source-mf': 4, 'source-hf': 4 } },
        combineSpec: expandLegacy(['drive-mf', 'drive-hf'], [100]), combineEnabled: true });
      latestCombine.publish({ jobId: 'shown', channelId: 'combined', canApply: true, blockedReason: null, recall: null,
        solvedBandHz: [200, 20000], onApplied: () => undefined,
        combine: { ...toWire(expandLegacy(['drive-mf', 'drive-hf'], [100])), crossovers_hz: [100] } } as never);
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(<QueryClientProvider client={client}><JobsCoordinator>
      {surface === 'top bar' ? <ToolbarSolveActions/> : surface === 'Results empty' ? <ResultsPanel/>
        : surface === 'CAD card' ? <CadSolveCard record={record} label="Speaker"/>
          : surface === 'crossover' ? <CadCrossover/> : <SolveOptionsControls mode="cad" ingestRecord={record}/>}
    </JobsCoordinator></QueryClientProvider>));
    expect(host.querySelector('.solve-readiness')).toBeNull();
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const readiness = host.querySelector('.solve-readiness')!;
    if (surface !== 'Results empty') {
      expect(readiness).toBeNull();
      return;
    }
    expect(readiness.closest('.results-panel .solve-progress')).not.toBeNull();
    expect(readiness.querySelector('span')?.textContent).toBe(blocker.message);
    expect([...readiness.querySelectorAll('button')].map(button => button.textContent)).toEqual([
      'Prepare BEAT Metal and solve', 'Use BEAT CPU', 'Use Metal-BEM', 'Open settings',
    ]);
    const solve = host.querySelector<HTMLButtonElement>('.solve-button, [data-action="solve"], .results-panel .btn-primary');
    if (solve) expect(solve.disabled).toBe(false);
    expect(readiness.textContent).toContain(blocker.message); // visible text, outside the disabled button
    latestCombine.publish(null);
    client.clear();
  });
  it.each(['parametric', 'Fusion'] as const)('renders %s warm-up once in Results with no toolbar or picker status', async source => {
    act(() => {
      if (source === 'Fusion') {
        blockedCad(true);
        mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      } else {
        useSolveOptionsStore.setState({ engine: 'beat-metal' });
        mocks.solvePlan = null;
        mocks.solvePlanError = 'BEAT Metal is warming up.';
        mocks.solveReadiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      }
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'preparing',
        reason: 'Preparing runtime', runtime_preparable: true } as EngineCapability];
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = () => root.render(<QueryClientProvider client={client}><JobsCoordinator>
      <div data-toolbar-solve><ToolbarSolveActions/></div><ResultsPanel/>
      <SolveOptionsControls mode={source === 'Fusion' ? 'cad' : 'parametric'}/>
    </JobsCoordinator></QueryClientProvider>);
    await act(async () => render());
    if (source === 'parametric') {
      expect(host.querySelector('.solve-readiness')).toBeNull();
      await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    }
    const progress = host.querySelector('.results-panel .solve-progress.solve-readiness')!;
    expect(progress.textContent).toBe('BEAT Metal is warming up. The solve starts when it is ready.Stop');
    expect(host.querySelectorAll('.solve-readiness')).toHaveLength(1);
    expect(host.querySelector('[data-toolbar-solve] .solve-button')?.textContent).not.toMatch(/warm|ready|waiting|refused/i);
    expect(host.querySelector('.beat-runtime-preparation')).toBeNull();
    expect(host.querySelectorAll('.solve-notice')).toHaveLength(0);
    // Readiness temporarily disappears while the plan cache refreshes. The
    // waiting intention still has exactly one owner, even across dock panels.
    if (source === 'parametric') { mocks.solvePlanPending = true; mocks.solveReadiness = null; }
    else { mocks.importedPlan.isPending = true; mocks.importedPlan.plan.readiness = null; }
    await act(async () => render());
    expect(host.querySelectorAll('.solve-readiness')).toHaveLength(1);
    expect(host.querySelector('.results-panel .solve-readiness')?.textContent).toBe(progress.textContent);
    client.clear();
  });

  it.each([
    { name: 'failed preparation', readiness: { ...blocker, message: 'BEAT Metal could not warm up. Download failed.' }, expected: 'BEAT Metal could not warm up. Download failed.', buttons: ['Prepare BEAT Metal and solve', 'Use BEAT CPU', 'Use Metal-BEM', 'Open settings'] },
    { name: 'unavailable hardware', readiness: { ...blocker, message: 'BEAT Metal is unavailable. No supported device.', remedies: [{ kind: 'use_engine' as const, engine: 'beat-cpu', same_solver: true }, { kind: 'settings' as const, section: 'simulation' }] }, expected: 'No supported device.', buttons: ['Use BEAT CPU', 'Open settings'] },
    { name: 'disabled preparation', readiness: { ...blocker, remedies: [{ kind: 'prepare' as const, engine: 'beat-metal', disabled_reason: 'Preparation is disabled by this installation.' }] }, expected: 'Preparation is disabled by this installation.', buttons: [] },
    { name: 'waiting', readiness: { ...blocker, message: 'BEAT Metal is still preparing.', remedies: [{ kind: 'wait' as const }, { kind: 'settings' as const, section: 'simulation' }] }, expected: 'BEAT Metal is warming up. The solve starts when it is ready.', buttons: ['Stop'] },
    { name: 'mesh too large', readiness: { code: 'mesh_too_large', message: 'This mesh is too large to solve.' }, expected: 'This mesh is too large to solve.', buttons: [] },
  ])('$name renders its message and only the declared remedies', async ({ readiness, expected, buttons }) => {
    act(() => { blockedCad(); mocks.importedPlan.plan.readiness = readiness as SolveBlocker; mocks.importedPlan.plan.reason = readiness.message; });
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const view = host.querySelector('.solve-readiness')!;
    expect(view.textContent).toContain(expected);
    expect([...view.querySelectorAll('button')].map(button => button.textContent)).toEqual(buttons);
    expect(host.querySelector<HTMLButtonElement>('.solve-button')!.disabled).toBe(readiness.code !== 'engine_unavailable');
    expect(host.querySelector('[data-toolbar-solve] .solve-readiness')).toBeNull();
  });
  it.each(['ready', 'stop', 'model change', 'engine change', 'failure', 'unmount'])('a requested warming solve continues exactly once, or cancels: %s', async outcome => {
    act(() => {
      blockedCad();
      useImportedSolveOptionsStore.setState({ engine: 'beat-metal' });
      mocks.importedPlan.plan.readiness = { ...blocker, message: 'BEAT Metal is getting ready.',
        remedies: [{ kind: 'wait' }, ...(blocker.remedies ?? []).filter(remedy => remedy.kind === 'use_engine')] };
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/><CurrentReadiness/></JobsCoordinator>);
    await act(async () => render());
    expect(host.textContent).toContain('Not solved yet');
    expect(host.textContent).not.toContain('getting ready');
    expect(host.querySelector('.solve-readiness')).toBeNull();
    await act(async () => { host.querySelector<HTMLButtonElement>('.solve-button')!.click(); });
    const notice = host.querySelector('.solve-readiness')!;
    expect(notice.textContent).toContain('BEAT Metal is warming up. The solve starts when it is ready.');
    expect(notice.classList.contains('solve-notice-blocked')).toBe(false);
    expect(notice.textContent).toBe('BEAT Metal is warming up. The solve starts when it is ready.Stop');
    expect(notice.closest('.solve-progress')).not.toBeNull();
    expect(host.querySelector('[data-toolbar-solve] .solve-button')?.textContent).not.toMatch(/warm|ready|waiting/i);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    if (outcome === 'stop') await act(async () => notice.querySelector<HTMLButtonElement>('button')!.click());
    if (outcome === 'model change') act(() => readyCad('wgi_changed_while_waiting'));
    if (outcome === 'engine change') act(() => useImportedSolveOptionsStore.getState().setEngine('beat-cpu'));
    if (outcome === 'unmount') await act(async () => root.render(null));
    if (outcome === 'failure') {
      mocks.importedPlan.plan.readiness = blocker;
      await act(async () => render());
    }
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => { if (outcome !== 'unmount') render(); });
    expect(mocks.solveModel).toHaveBeenCalledTimes(outcome === 'ready' ? 1 : 0);
    await act(async () => { if (outcome !== 'unmount') render(); });
    expect(mocks.solveModel).toHaveBeenCalledTimes(outcome === 'ready' ? 1 : 0);
  });

  it.each(['ready', 'model change', 'engine change', 'stop', 'unmount'])('keeps a warming intention guarded through asynchronous parametric preflight: %s', async outcome => {
    const preflight = deferred<Awaited<ReturnType<typeof mocks.planSolveDesign>>>();
    mocks.planSolveDesign.mockReturnValue(preflight.promise);
    act(() => {
      useSolveOptionsStore.setState({ engine: 'beat-metal' });
      mocks.solvePlan = null;
      mocks.solvePlanError = 'BEAT Metal is getting ready.';
      mocks.solveReadiness = { ...blocker, remedies: [{ kind: 'wait' }] };
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    expect(host.querySelector('.solve-readiness')).toBeNull();
    await act(async () => { host.querySelector<HTMLButtonElement>('.solve-button')!.click(); });
    expect(host.querySelector('.solve-progress.solve-readiness')?.textContent).toBe(
      'BEAT Metal is warming up. The solve starts when it is ready.Stop');
    mocks.solvePlan = { engine: 'beat-metal', formulation: 'full-3d', reason: 'ready', eligibility_reasons: [] };
    mocks.solvePlanError = null; mocks.solveReadiness = null;
    await act(async () => render());
    expect(mocks.planSolveDesign).toHaveBeenCalled();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    if (outcome === 'model change') act(() => useDesignStore.getState().replaceDocument({ ...designForFamily('OSSE'), L: 321 }));
    if (outcome === 'engine change') act(() => useSolveOptionsStore.getState().setEngine('beat-cpu'));
    if (outcome === 'stop') act(() => solveControlStore.getSnapshot()!.stopWaiting());
    if (outcome === 'unmount') await act(async () => root.render(null));
    await act(async () => preflight.resolve(mocks.solvePlan));
    expect(mocks.submitDesign).toHaveBeenCalledTimes(outcome === 'ready' ? 1 : 0);
  });

  it('does not downgrade a warming intention on repeated Solve during plan refresh', async () => {
    act(() => {
      blockedCad(); useImportedSolveOptionsStore.setState({ engine: 'beat-metal' });
      mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    mocks.importedPlan.isPending = true; mocks.importedPlan.plan.readiness = null;
    await act(async () => render());
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    act(() => { readyCad('wgi_another_model'); });
    mocks.importedPlan.isPending = false;
    mocks.importedPlan.plan.engine = 'beat-metal';
    await act(async () => render());
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });

  it.each(['ready', 'stop', 'model change', 'engine change', 'setup change', 'failure', 'unmount'])('a warming Fusion request waits in solve progress and continues automatically: %s', async outcome => {
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'held-fusion' });
    }));
    const waiting = { ...blocker, remedies: [{ kind: 'wait' as const }, ...(blocker.remedies ?? [])] };
    act(() => {
      blockedCad(true);
      mocks.importedPlan.plan.readiness = waiting;
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'preparing',
        reason: null, version: null, fast_paths: [] } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    const progress = host.querySelector('.solve-progress.solve-readiness')!;
    expect(progress.textContent).toBe('BEAT Metal is warming up. The solve starts when it is ready.Stop');
    expect([...progress.querySelectorAll('button')].map(button => button.textContent)).toEqual(['Stop']);
    expect(host.querySelector('[data-toolbar-solve] .solve-button')?.textContent).not.toMatch(/warm|ready|waiting|refused/i);
    expect(posts).toEqual([]); // Fusion already requested Solve; no extra click or prepare POST.
    if (outcome === 'stop') await act(async () => progress.querySelector<HTMLButtonElement>('button')!.click());
    if (outcome === 'model change') act(() => readyCad('wgi_changed_while_waiting'));
    if (outcome === 'engine change') act(() => useImportedSolveOptionsStore.getState().setEngine('bempp'));
    if (outcome === 'setup change') act(() => useImportedSolveOptionsStore.getState().setSweep({ startHz: 123 }));
    if (outcome === 'unmount') await act(async () => root.render(null));
    if (outcome === 'failure') {
      mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], runtime_state: 'failed' } as EngineCapability];
      mocks.importedPlan.plan.readiness = blocker;
      await act(async () => render());
      expect(host.querySelector('.solve-progress.solve-readiness')?.textContent).toContain(blocker.message);
      expect(host.querySelector('.solve-readiness')?.textContent).toContain('Prepare BEAT Metal and solve');
    }
    mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => { if (outcome !== 'unmount') render(); });
    expect(posts).toEqual(outcome === 'ready' ? ['/api/jobs/job%3Aheld-fusion/solve-again']
      : outcome === 'stop' ? ['/api/jobs/job%3Aheld-fusion/dismiss'] : []);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    await act(async () => { if (outcome !== 'unmount') render(); });
    expect(posts).toHaveLength(outcome === 'ready' || outcome === 'stop' ? 1 : 0);
  });

  it.each(['failed', 'unprepared'] as const)('regression: cached %s before a warming Fusion arrival cannot cancel its request', async runtimeState => {
    vi.useFakeTimers();
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'continued-fusion' });
    }));
    act(() => {
      blockedCad();
      mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: runtimeState } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render()); // Cache the failure before the request exists.
    act(() => { blockedCad(true); mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] }; });
    await act(async () => render()); // Same capability snapshot, new intention.
    expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(true);
    expect(posts).toEqual([]);
    mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => render());
    for (let poll = 0; poll < 3; poll++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); render(); });
    expect(posts).toEqual(['/api/jobs/job%3Aheld-fusion/solve-again']);
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });

  it.each(['failed', 'unprepared'] as const)('regression: an initial %s response requested before Fusion acceptance cannot cancel it', async runtimeState => {
    vi.useFakeTimers();
    const initial = deferred<Response>();
    let capabilityFetches = 0;
    const posts: string[] = [];
    const snapshot = (state: string) => json({
      engines: [{ name: 'beat-metal', available: state === 'ready', runtime_state: state }],
      engineSelection: { default: 'auto', resolvedDefault: 'beat-metal', full3dOrder: ['beat-metal'] },
    });
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url) === '/api/capabilities') {
        capabilityFetches++;
        return capabilityFetches === 1 ? initial.promise : snapshot('ready');
      }
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'continued-fusion' });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => root.render(null)); // Choose the real hook before mounting its owner.
    mocks.useRealCapabilities = true;
    act(() => {
      blockedCad();
      mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
    });
    const render = () => root.render(<QueryClientProvider client={client}><JobsCoordinator><SolveActions/></JobsCoordinator></QueryClientProvider>);
    try {
      await act(async () => render());
      expect(capabilityFetches).toBe(1); // Fetch started before the Fusion request exists.
      act(() => {
        blockedCad(true);
        mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      });
      await act(async () => render());
      expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(true);
      await act(async () => { initial.resolve(snapshot(runtimeState)); });
      await act(async () => { await vi.advanceTimersByTimeAsync(0); });
      expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(true);
      expect(posts).toEqual([]);
      mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
      for (let poll = 0; poll < 3; poll++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); render(); });
      expect(capabilityFetches).toBeGreaterThan(1);
      expect(posts).toEqual(['/api/jobs/job%3Aheld-fusion/solve-again']);
      expect(mocks.solveModel).not.toHaveBeenCalled();
    } finally {
      await act(async () => root.render(null));
      client.clear();
    }
  });

  it('allows a new Fusion Solve for the same job after a fresh preparation failure', async () => {
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'continued-fusion' });
    }));
    act(() => {
      blockedCad(true);
      mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'preparing' } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'failed' } as EngineCapability];
    await act(async () => render());
    expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(false);
    expect(posts).toEqual([]);
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(true);
    mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => render());
    await act(async () => render());
    expect(posts).toEqual(['/api/jobs/job%3Aheld-fusion/solve-again']);
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });

  it.each(['waiting', 'frame wait', 'ready'] as const)('regression: StrictMode mount replay preserves a held intention exactly once during %s', async phase => {
    vi.useFakeTimers();
    await act(async () => root.render(null));
    const frame = deferred<Response>();
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).includes('/solver-frame')) return frame.promise;
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'continued-fusion' });
    }));
    act(() => {
      blockedCad(true);
      const held = jobsSocket.getSnapshot().jobs[0];
      publishJobs([{ ...held, cad_state: { ...held.cad_state!, blocker: { ...blocker, remedies: [{ kind: 'wait' }] } } }]);
      mocks.importedPlan.plan.readiness = { ...blocker, remedies: [{ kind: 'wait' }] };
      mocks.capabilities.engines = [{ name: 'beat-metal', available: phase !== 'waiting',
        runtime_state: phase === 'waiting' ? 'preparing' : 'ready' } as EngineCapability];
      if (phase !== 'frame wait') useCadSolverFrameStore.getState().apply('wgi_readiness', { linked: true });
    });
    let frameRead: Promise<void> | undefined;
    if (phase === 'frame wait') act(() => { frameRead = useCadSolverFrameStore.getState().load('wgi_readiness'); });
    const render = () => root.render(<StrictMode><JobsCoordinator><SolveActions/></JobsCoordinator></StrictMode>);
    await act(async () => render());
    if (phase === 'waiting') expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(true);
    if (phase !== 'ready') expect(posts).toEqual([]);
    mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-metal', readiness: null };
    await act(async () => {
      render();
      frame.resolve(json({ linked: true }));
      await frameRead;
    });
    for (let poll = 0; poll < 3; poll++) await act(async () => { await vi.advanceTimersByTimeAsync(1000); render(); });
    expect(posts).toEqual(['/api/jobs/job%3Aheld-fusion/solve-again']);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    const refreshed = mocks.refreshCapabilities.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(mocks.refreshCapabilities).toHaveBeenCalledTimes(refreshed);
  });

  it('does not resume a legacy held request through a different selected engine’s warm-up', async () => {
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'unexpected-continuation' });
    }));
    act(() => {
      blockedCad(true); // Historical payload has no structured blocker; its saved engine is BEAT Metal.
      useImportedSolveOptionsStore.setState({ engine: 'beat-cpu' });
      mocks.importedPlan.plan.readiness = { ...blocker, engine: 'beat-cpu', remedies: [{ kind: 'wait' }] };
      mocks.capabilities.engines = [{ name: 'beat-cpu', available: false, runtime_state: 'preparing' } as EngineCapability];
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    expect(solveControlStore.getSnapshot()?.waitingForEngine).toBe(false);
    mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], available: true, runtime_state: 'ready' } as EngineCapability];
    mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: 'beat-cpu', readiness: null };
    await act(async () => render());
    expect(posts).toEqual([]);
  });

  it('continues the held warming intent even when readiness refresh finishes before the job arrives', async () => {
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') posts.push(String(url));
      return json({ job_id: 'continued-fusion' });
    }));
    act(() => {
      blockedCad(true);
      const parent = jobsSocket.getSnapshot().jobs[0];
      publishJobs([{ ...parent, cad_state: { ...parent.cad_state!,
        blocker: { ...blocker, remedies: [{ kind: 'wait' }] } } }]);
      mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability];
      mocks.importedPlan.plan = { ...mocks.importedPlan.plan, engine: null, readiness: null };
      mocks.importedPlan.isPending = true;
    });
    const render = () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
    await act(async () => render());
    expect(posts).toEqual(['/api/jobs/job%3Aheld-fusion/solve-again']);
    mocks.importedPlan.isPending = false;
    mocks.importedPlan.plan.engine = 'beat-metal';
    await act(async () => render());
    expect(posts).toHaveLength(1);
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });

  it('prepares once from solve progress and refreshes, without solving', async () => {
    act(() => blockedCad());
    const response = deferred<Response>();
    const fetcher = vi.fn((url: RequestInfo | URL) => String(url).endsWith('/prepare') ? response.promise : Promise.resolve(json({})));
    vi.stubGlobal('fetch', fetcher);
    await act(async () => root.render(<JobsCoordinator><SolveActions/><CurrentReadiness/></JobsCoordinator>));
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const buttons = [...host.querySelectorAll<HTMLButtonElement>('button')].filter(button => button.textContent === 'Prepare BEAT Metal and solve');
    expect(buttons).toHaveLength(1);
    await act(async () => { buttons[0].click(); buttons[0].click(); });
    await act(async () => response.resolve(json({}, 202)));
    expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/prepare'))).toEqual([['/api/runtimes/beat/metal/prepare', { method: 'POST' }]]);
    expect(mocks.refreshCapabilities).toHaveBeenCalled();
    expect(mocks.solveModel).not.toHaveBeenCalled();
  });
  it.each(['unchanged', 'model changed', 'settings changed'])('held Fusion continues its saved request once: %s', async changed => {
    const actions = await vi.importActual<typeof import('../jobs/actions')>('../jobs/actions');
    mocks.solveModel.mockImplementation(actions.solveModel);
    const posts: string[] = [];
    const preparation = deferred<Response>();
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).endsWith('/prepare')) return preparation.promise;
      if (init?.method === 'POST' && !String(url).endsWith('/prewarm')) posts.push(String(url));
      return json({ job_id: 'held-fusion' });
    }));
    act(() => {
      blockedCad(true);
      mocks.capabilities.engines = [{ name: 'beat-metal', label: 'BEAT Metal', available: false,
        runtime_state: 'unprepared', runtime_preparable: true, reason: null, version: null, fast_paths: [] } as EngineCapability];
    });
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    const readiness = host.querySelector('.solve-readiness')!;
    expect([...readiness.querySelectorAll('button')].map(button => button.textContent)).toEqual([
      'Prepare BEAT Metal and solve', 'Use BEAT CPU', 'Use Metal-BEM', 'Open settings',
    ]);
    await act(async () => readiness.querySelector<HTMLButtonElement>('button')!.click());
    if (changed === 'model changed') act(() => readyCad('wgi_different'));
    if (changed === 'settings changed') act(() => {
      useImportedSolveOptionsStore.getState().setSweep({ startHz: 123, endHz: 456, count: 5 });
      useImportedSolveOptionsStore.getState().setEngine('bempp');
      useCadReturnStore.setState({ meshSizes: { ...useCadReturnStore.getState().meshSizes, rigid_size_mm: 17 } });
    });
    act(() => { mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], runtime_state: 'preparing' } as EngineCapability]; });
    await act(async () => preparation.resolve(json({}, 202)));
    expect(posts).toEqual([]);
    act(() => { mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], available: true, runtime_state: 'ready' } as EngineCapability]; });
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    expect(posts).toEqual(changed !== 'unchanged' ? [] : ['/api/jobs/job%3Aheld-fusion/solve-again']);
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(changed === 'model changed'
      ? 'Prepare and solve was cancelled because the model on screen changed.'
      : changed === 'settings changed' ? 'Prepare and solve was cancelled because the solve setup changed.' : null);
    if (changed === 'unchanged') {
      const continuation = vi.mocked(fetch).mock.calls.find(([url]) => String(url).endsWith('/solve-again'))!;
      expect(JSON.parse(String(continuation[1]?.body))).toEqual({}); // inherit the held job’s setup, frame and identity
    }
    expect(mocks.solveModel).not.toHaveBeenCalled();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    expect(posts).toHaveLength(changed !== 'unchanged' ? 0 : 1);
  });
  it.each(['before response', 'during refresh'])('a successful continuation stays silent when its child arrives %s', async arrival => {
    const preparation = deferred<Response>();
    const response = deferred<Response>();
    const posts: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
      if (String(url).endsWith('/prepare')) return preparation.promise;
      if (init?.method === 'POST' && !String(url).endsWith('/prewarm')) posts.push(String(url));
      return response.promise;
    }));
    act(() => {
      blockedCad(true);
      mocks.capabilities.engines = [{ name: 'beat-metal', available: false, runtime_state: 'unprepared' } as EngineCapability];
    });
    const parent = jobsSocket.getSnapshot().jobs[0];
    const child = cadJobFixture(operation('continued-held', 'accepted', { jobId: 'continued-held' }), {
      parent_job_id: parent.id,
      cad_source: { ingest_id: 'wgi_readiness', model_id: 'wgl_readiness' } as JobItem['cad_source'],
    });
    const publishChild = () => publishJobs([child, parent]);
    if (arrival === 'during refresh') vi.mocked(jobsSocket.refresh).mockImplementationOnce(async () => publishChild());
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    await act(async () => host.querySelector<HTMLButtonElement>('.solve-readiness button')!.click());
    act(() => { mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability]; });
    await act(async () => preparation.resolve(json({}, 202)));
    expect(posts).toEqual(['/api/jobs/job%3Aheld-fusion/solve-again']);
    expect(solveControlStore.getSnapshot()!.submitting).toBe(true);
    if (arrival === 'before response') act(() => publishChild());
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBeNull();
    await act(async () => response.resolve(json({ job_id: child.id })));
    expect(currentModelStatus(currentModel(), jobsSocket.getSnapshot().jobs).job?.id).toBe(child.id);
    expect(compareSelection.getSnapshot().awaiting).toBe(child.id);
    expect(solveControlStore.getSnapshot()!.submitting).toBe(false);
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBeNull();
    expect(host.textContent).not.toContain('Prepare and solve was cancelled');
  });

  it.each(['engine change and return', 'setup change', 'frame pick', 'unmount', 'leave and return', 'model change and return', 'held job moves on', 'held job cancelled', 'preparation fails', 'unchanged'])(
    'held continuation checks its lifetime after the frame read: %s', async change => {
      const preparation = deferred<Response>();
      const frame = deferred<Response>();
      const posts: string[] = [];
      vi.stubGlobal('fetch', vi.fn(async (url: RequestInfo | URL, init?: RequestInit) => {
        if (String(url).endsWith('/prepare')) return preparation.promise;
        if (init?.method === 'POST' && !String(url).endsWith('/prewarm')) posts.push(String(url));
        return json({ job_id: 'continued-held' });
      }));
      act(() => {
        blockedCad(true);
        mocks.capabilities.engines = [{ name: 'beat-metal', label: 'BEAT Metal', available: false,
          runtime_state: 'unprepared', runtime_preparable: true, reason: null, version: null, fast_paths: [] } as EngineCapability];
      });
      await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
      await act(async () => host.querySelector<HTMLButtonElement>('.solve-readiness button')!.click());
      let frameRead!: Promise<void>;
      act(() => { frameRead = useCadSolverFrameStore.getState().load('wgi_readiness', () => frame.promise); });
      act(() => { mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], available: true, runtime_state: 'ready' } as EngineCapability]; });
      await act(async () => preparation.resolve(json({}, 202)));
      await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
      expect(solveControlStore.getSnapshot()!.submitting).toBe(true); // continuation is waiting inside the mutex
      expect(posts).toEqual([]);
      const notices: string[] = [];
      const stopNotices = jobsCoordinatorBridge.subscribe(() => {
        const notice = jobsCoordinatorBridge.getSnapshot().actionError;
        if (notice) notices.push(notice);
      });
      await act(async () => {
        if (change === 'engine change and return') {
          const engine = useImportedSolveOptionsStore.getState().engine;
          useImportedSolveOptionsStore.getState().setEngine('bempp');
          useImportedSolveOptionsStore.getState().setEngine(engine);
        }
        if (change === 'setup change') useImportedSolveOptionsStore.getState().setSweep({ startHz: 123 });
        if (change === 'frame pick') {
          useCadSolverFrameStore.getState().apply('wgi_readiness', { linked: false, axes: [
            { axis: '+z', allowed: true }, { axis: '+x', allowed: true },
          ], preselected: { axis: '+z' } } as never);
          useCadSolverFrameStore.getState().pick('wgi_readiness', '+x');
        }
        if (change === 'unmount') root.render(<span>gone</span>);
        if (change === 'leave and return') {
          const scene = importedMeshStore.getSnapshot().cad!;
          importedMeshStore.clear();
          importedMeshStore.setCad(scene); // same batch: cancellation must remain latched
        }
        if (change === 'model change and return') {
          const record = useCadReturnStore.getState().ingestRecord;
          useCadReturnStore.setState({ ingestRecord: { ...record!, ingest_id: 'different' } });
          useCadReturnStore.setState({ ingestRecord: record });
        }
        if (change === 'held job moves on') {
          const held = jobsSocket.getSnapshot().jobs[0];
          publishJobs([{ ...held, status: 'preparing', cad_state: { ...held.cad_state!, state: 'processing' } }]);
          publishJobs([held]); // restoring the read model cannot revive a cancelled token
        }
        if (change === 'held job cancelled') {
          const held = jobsSocket.getSnapshot().jobs[0];
          publishJobs([{ ...held, status: 'cancelled' }]); // cad_state may lag behind a status delta
        }
        if (change === 'preparation fails') {
          mocks.capabilities.engines = [{ ...mocks.capabilities.engines[0], available: false, runtime_state: 'failed' } as EngineCapability];
          root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>);
        }
      });
      await act(async () => { frame.resolve(json({ linked: true })); await frameRead; });
      expect(posts).toEqual(change === 'unchanged' ? ['/api/jobs/job%3Aheld-fusion/solve-again'] : []);
      const reason = change === 'engine change and return' ? 'the selected engine changed'
        : change === 'setup change' || change === 'frame pick' ? 'the solve setup changed'
        : change === 'leave and return' ? 'the model left the viewport'
        : change === 'model change and return' ? 'the model on screen changed'
          : change.startsWith('held job') ? 'the waiting job changed'
            : change === 'preparation fails' ? 'engine preparation failed' : null;
      const notice = reason ? `Prepare and solve was cancelled because ${reason}.` : null;
      expect(notices).toEqual(notice ? [notice] : []);
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(notice);
      if (notice) {
        const status = host.querySelector('.solve-readiness[role="status"]')!;
        expect(status.textContent?.split(notice)).toHaveLength(2);
      }
      stopNotices();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      expect(mocks.submitDesign).not.toHaveBeenCalled();
    });

  it('preparation POST failure cancels the held intention even if the engine later becomes ready', async () => {
    const preparation = deferred<Response>();
    const fetcher = vi.fn(async (url: RequestInfo | URL) => String(url).endsWith('/prepare') ? preparation.promise : json({}));
    vi.stubGlobal('fetch', fetcher);
    act(() => blockedCad(true));
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    await act(async () => host.querySelector<HTMLButtonElement>('.solve-readiness button')!.click());
    await act(async () => preparation.resolve(json({ detail: 'Preparation failed' }, 500)));
    expect(host.querySelector('.solve-readiness')?.textContent).toContain(
      'Prepare and solve was cancelled because engine preparation failed.');
    act(() => { mocks.capabilities.engines = [{ name: 'beat-metal', available: true, runtime_state: 'ready' } as EngineCapability]; });
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    expect(fetcher.mock.calls.map(([url]) => String(url)).filter(url => url.endsWith('/solve-again'))).toEqual([]);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(
      'Prepare and solve was cancelled because engine preparation failed.');
  });

  it.each([false, true])('the final held guard reports cancellation unless already aborted: %s', async aborted => {
    act(() => blockedCad(true));
    const controller = new AbortController();
    const held = { ...captureSolveInputs(), jobId: 'job:held-fusion', signal: controller.signal };
    if (aborted) controller.abort(); // Includes coordinator unmount cleanup.
    act(() => readyCad('wgi_changed_before_post'));
    vi.stubGlobal('fetch', vi.fn());
    await act(async () => {
      expect(await jobsCoordinatorBridge.getSnapshot().continueRun(held.jobId, { held })).toBeNull();
    });
    expect(fetch).not.toHaveBeenCalled();
    const notice = 'Prepare and solve was cancelled because the model on screen changed.';
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(aborted ? null : notice);
    // The status surface must also render a notice without a readiness blocker.
    mocks.importedPlan.plan.readiness = null;
    mocks.importedPlan.plan.engine = 'metal';
    act(() => publishJobs([]));
    await act(async () => root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>));
    expect(host.querySelector('.solve-readiness')?.textContent ?? '').toBe(aborted ? '' : notice);
  });

  it.each(['running', 'error', 'complete'] as const)('keeps the CAD run’s %s status beside blocked readiness for the next solve', async status => {
    const run = cadJobFixture(operation('run-status', 'accepted'), {
      cad_source: { ingest_id: 'wgi_readiness', model_id: 'wgl_readiness' } as JobItem['cad_source'],
      status, progress: 0.4, stage: 'solve', stage_message: 'Solving frequency 2/10 with Metal BEM',
      error_message: status === 'error' ? 'The solver failed on this run.' : null,
      has_results: status === 'complete',
    });
    act(() => {
      blockedCad();
      publishJobs([run]);
      if (status === 'error') useImportedSolveOptionsStore.getState().updatePolar({ angleStep: 0 });
    });
    await act(async () => root.render(<JobsCoordinator><CurrentReadiness/></JobsCoordinator>));
    const progress = host.querySelector('.cad-solve-run')!;
    expect(progress).not.toBeNull();
    expect(progress.textContent).toContain(status === 'running' ? 'frequency 2 of 10' : status === 'error' ? 'The solver failed on this run.' : 'Done');
    if (status === 'running') expect(host.querySelector('.solve-readiness')).toBeNull();
    expect(host.querySelector('.solve-readiness')?.textContent ?? '').not.toContain(blocker.message);
    expect(host.querySelector('.current-model-status')?.getAttribute('data-model-status')).toBe(status === 'running' ? 'running' : status === 'error' ? 'failed' : 'solved');
  });

  it('Use engine changes the visible option and re-plans the imported request', async () => {
    act(() => blockedCad());
    mocks.useRealImportedPlan = true;
    mocks.postImportedSolvePlan.mockReset().mockResolvedValue(mocks.importedPlan.plan);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const render = () => root.render(<QueryClientProvider client={client}><JobsCoordinator><SolveActions/></JobsCoordinator></QueryClientProvider>);
    await act(async () => render());
    await act(async () => { await new Promise(resolve => setTimeout(resolve, 20)); });
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const button = [...host.querySelectorAll<HTMLButtonElement>('button')].find(button => button.textContent === 'Use BEAT CPU')!;
    expect(button).toBeTruthy();
    await act(async () => button.click());
    expect(useImportedSolveOptionsStore.getState().engine).toBe('beat-cpu');
    await act(async () => { await new Promise(resolve => setTimeout(resolve, SOLVE_PLAN_DEBOUNCE_MS + 30)); });
    expect(JSON.parse(mocks.postImportedSolvePlan.mock.calls.at(-1)![0]).options.engine).toBe('beat-cpu');
    expect(mocks.postImportedSolvePlan).toHaveBeenCalledTimes(2);
    client.clear();
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
    expect(host.querySelector('.solve-progress')?.textContent).toContain('BEAT Metal needs preparing.');
    const solve = host.querySelector<HTMLButtonElement>('.results-panel .btn-primary')!;
    expect(solve.disabled).toBe(false);
    await act(async () => solve.click());
    expect(mocks.solveModel).toHaveBeenCalledExactlyOnceWith('wgl_test', expect.objectContaining({ prepared_id: 'wgi_refused_results' }));
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST').map(([url]) => url)).toEqual(['/api/models/wgl_test/solve']);
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('records Solve anyway through the existing continuation lock and API', async () => {
    const response = deferred<Response>();
    const fetcher = vi.fn<typeof fetch>(() => response.promise);
    vi.stubGlobal('fetch', fetcher);
    let continuation!: Promise<unknown>;
    await act(async () => {
      continuation = jobsCoordinatorBridge.getSnapshot().continueRun('held-mesh', { mesh_limit_override: true });
      expect(await jobsCoordinatorBridge.getSnapshot().continueRun('held-mesh', { mesh_limit_override: true })).toBeNull();
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/jobs/held-mesh/solve-again');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ mesh_limit_override: true });
    await act(async () => { response.resolve(json({ job_id: 'mesh-child' })); await continuation; });
    expect(compareSelection.getSnapshot().awaiting).toBe('mesh-child');
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
    const jobRequests = () => fetcher.mock.calls.filter(([url]) => String(url).startsWith('/api/jobs/'));
    expect(jobRequests()).toHaveLength(1);
    expect(solveAttention.operationArmed('refused-parent')).toBe(true);
    await act(async () => {
      approval.resolve(outcome === 'approval refused' ? json({ detail: 'wrong preparation' }, 422) : json({}));
      await continuation;
    });
    expect(jobRequests().map(([url]) => url)).toEqual(outcome === 'approval refused'
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

  const flushPlan = async (client: QueryClient) => {
    // A real Response body can finish outside the fake timer queue. Wait for
    // the actual queries before draining React Query's zero-delay notifications;
    // advancing two milliseconds alone races body consumption under load.
    await act(async () => {
      await Promise.allSettled(client.getQueryCache().findAll({ fetchStatus: 'fetching' })
        .map(query => query.promise));
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
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
    vi.stubGlobal('fetch', (url: string, init?: RequestInit) => url.endsWith('/prewarm') ? Promise.resolve(json({})) : fetcher(url, init));
    mocks.submitDesign.mockResolvedValue('job-fresh');
    if (mode === 'cad') readyCad('wgi_select');
    workspaceModeStore.setMode(mode);
    const client = new QueryClient();
    await act(async () => {
      root.render(<QueryClientProvider client={client}><JobsCoordinator><EngineSelect/><MainSolveButton/></JobsCoordinator></QueryClientProvider>);
    });
    await flushPlan(client);
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

  it('disables Solve and blocks clicks, shortcuts and direct invocation for a refused enclosure', async () => {
    const { client, fetcher } = await mountPlanning('parametric');
    const message = 'The enclosure leaves 0.00 mm of baffle beside the mouth on the right; set the right margin to at least 0.1 mm.';
    fetcher.mockImplementation(async () => new Response(JSON.stringify({ detail: message }), { status: 422 }));
    act(() => {
      const design = designForFamily('ICW');
      design.morph = { ...design.morph, target_shape: 1, target_width: 300, target_height: 400, fixed_part: 0, allow_shrinkage: 1 };
      design.enclosure = { ...design.enclosure, depth: 280, space_l: 0, space_r: 0, space_t: 0, space_b: 0 };
      design.mesh.wall_thickness = 0;
      useDesignStore.getState().replaceDocument(design);
    });
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    await flushPlan(client);
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(true);
    expect(solve.title).toBe(message);
    for (const press of ['click', 'Cmd+Enter', 'Ctrl+Enter']) pressSolve(press);
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    client.clear();
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
    await flushPlan(client);
    expect(mocks.solveModel).not.toHaveBeenCalled();
    client.clear();
  });

  describe.each(['parametric', 'cad'] as const)('%s planning gap', (mode) => {
    it.each(['click', 'Cmd+Enter', 'Ctrl+Enter'])('queues one immediate Solve %s after a select change', async (press) => {
      const { client, fetcher } = await mountPlanning(mode);
      const parametric = deferred<Response>();
      const body = deferred<typeof mocks.solvePlan>();
      const response = new Response();
      const readBody = vi.spyOn(response, 'json').mockReturnValue(body.promise);
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
        parametric.resolve(response);
        imported.resolve({ ...mocks.importedPlan.plan, requested: 'bempp', engine: 'bempp' });
      });
      if (mode === 'parametric') {
        expect(readBody).toHaveBeenCalledOnce();
        expect(mocks.submitDesign).not.toHaveBeenCalled();
        await act(async () => body.resolve(mocks.solvePlan));
      }
      await flushPlan(client);
      if (mode === 'parametric') {
        expect(mocks.submitDesign).toHaveBeenCalledOnce();
        expect(mocks.submitDesign.mock.calls[0][1].engine).toBe('bempp');
        const planning = fetcher.mock.calls.filter(([url]) => String(url) === '/api/solve/plan');
        expect(JSON.parse(planning.at(-1)![1]!.body as string).options.engine).toBe('bempp');
      } else {
        expect(mocks.solveModel).toHaveBeenCalledOnce();
        expect((mocks.solveModel.mock.calls[0][1].setup as CadSolveSetup).options.engine).toBe('bempp');
        expect(JSON.parse(mocks.postImportedSolvePlan.mock.calls[1][0]).options.engine).toBe('bempp');
      }
      await flushPlan(client);
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
      await flushPlan(client);
      expect(host.querySelector('button')!.disabled).toBe(true);
      expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('settings refused');
      changeEngine('metal');
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan(client);
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
      await flushPlan(client);
      act(() => workspaceModeStore.setMode(mode));
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan(client);
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      client.clear();
    });

    it('requires a new press when another select change races the pending response', async () => {
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
      await flushPlan(client);
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
      await flushPlan(client);
      expect(mocks.submitDesign).not.toHaveBeenCalled();
      expect(mocks.solveModel).not.toHaveBeenCalled();
      pressSolve('click');
      await flushPlan(client);
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
      await flushPlan(client);
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
    await flushPlan(client);
    expect(host.querySelector('button')!.disabled).toBe(true);
    changeEngine('metal');
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    await flushPlan(client);
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
      useDesignStore.getState().replaceDocument(design);
      const run = () => solveControlStore.getSnapshot()!.solve();
      first = run();
      second = run();
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
    expect(solve.title).toBe('Solve current design with Auto (BEMPP)');
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
    expect(host.querySelector('.solve-progress.solve-readiness')?.textContent).toMatch(/greater than 0 Hz/);
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

  it('cancels when settings change while the fresh Advanced plan is pending', async () => {
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
    expect(mocks.solveModel).not.toHaveBeenCalled();
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

  it('submits the same complete file setup before and after Save setup', async () => {
    const replay = importedSubmission('wgi_saved_replay');
    replay.geometry.drive_voltage_v = 4.2;
    replay.geometry.drive_channels[0].driver = { re_ohm: 6.2, sd_cm2: 26, bl_t_m: 12.4, mms_g: 2.4, fs_hz: 620 };
    replay.geometry.drive_channels.push({ id: 'second', source_ids: ['source-2'], motion: 'normal' });
    replay.geometry.combine = { members: replay.geometry.drive_channels.map(channel => channel.id), crossovers_hz: [500] };
    const { type: _t, ingest_id: _i, manifest_sha256: _m, artifact_sha256: _a, acknowledged_findings: _f, ...geometry } = replay.geometry;
    const setup = { geometry, options: replay.options };
    act(() => {
      workspaceModeStore.setImportedInput('file'); workspaceModeStore.setMode('cad');
      useMeshImportStore.setState({ busy: false, filename: 'replay.msh' });
      void openImported({ kind: 'file', setup, prepared: { ingest_id: 'wgi_saved_replay', model_id: 'mesh-file:saved-replay',
        manifest_sha256: replay.geometry.manifest_sha256, artifact_sha256: replay.geometry.artifact_sha256,
        source_tags: Object.fromEntries(geometry.drive_channels.flatMap(channel => channel.source_ids).map((id, i) => [id, i + 1])),
      } as unknown as RegisteredMesh });
      importedMeshStore.setFile({ name: 'replay.msh', source: 'file', ingestId: 'wgi_saved_replay' } as ImportedMeshScene);
    });
    mocks.solveModel.mockResolvedValue('replayed-run');
    await act(async () => root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>));
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    const before = structuredClone(press().setup);
    const save = vi.fn<typeof fetch>(async () => json({ revision_id: 'wgs_saved' }));
    await act(async () => { await saveMeshSetup(save); });
    await act(async () => { await solveControlStore.getSnapshot()!.solve(); });
    expect(before).toEqual({ schema_version: 1, ...setup });
    expect(press(1).setup).toEqual(before);
    expect(JSON.parse(save.mock.calls[0][1]?.body as string).setup).toEqual(before);
    expect(mocks.submitDesign).not.toHaveBeenCalled();
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
    expect(solve.title).toBe('Prepare this model before solving.');
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
