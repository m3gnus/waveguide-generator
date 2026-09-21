/**
 * Milestone M1's acceptance chain, end to end inside the application shell:
 *
 *   Solve -> capture the intended input -> create or recover the operation ->
 *   submit the job -> select the finished result -> reveal the existing
 *   Results panel -> CAD Link mode is still selected.
 *
 * and the awkward cases the plan names, each its own test. The operation
 * store, the submission path and the Results panel are the real ones; only the
 * HTTP edge and the dock are stood in for. The dock is a recorder bound to the
 * real navigation seam, so "revealed" means `activate('results')` was asked
 * for, and "hidden" means the dock reported Results as not on screen.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary, CadSolveSetup } from '../api/cadOperations';
import { recoverMissedSnapshots, resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resetDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { JobsCoordinator, jobsCoordinatorBridge } from './JobsCoordinator';
import { activateWorkspaceMode } from './TopBar';
import { takeDesignOpenTicket } from '../design/openCadProject';
import { showJobModel } from '../jobs/showJobModel';
import { ResultsPanel } from './ResultsPanel';
import { SolveActions } from './TopBar';
import { resetSolveAttentionForTests, solveAttention } from './solveAttention';
import {
  bindWorkspaceNavigation,
  navigationGeneration,
  publishVisiblePanels,
  resetWorkspaceNavigationForTests,
  workspaceNavigation,
  type WorkspacePanel,
} from './workspaceNavigation';

const mocks = vi.hoisted(() => ({
  submitImported: vi.fn(),
  submitDesign: vi.fn(),
  planSolveDesign: vi.fn(),
  createSetupRevision: vi.fn(),
  createCadOperation: vi.fn(),
  prepareCadOperation: vi.fn(),
  getCadOperation: vi.fn(),
}));

vi.mock('../api/cadOperations', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/cadOperations')>();
  return {
    ...actual,
    createSetupRevision: mocks.createSetupRevision,
    createCadOperation: mocks.createCadOperation,
    prepareCadOperation: mocks.prepareCadOperation,
    getCadOperation: mocks.getCadOperation,
  };
});
vi.mock('../jobs/actions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/actions')>();
  return { ...actual, planSolveDesign: mocks.planSolveDesign, submitDesign: mocks.submitDesign, submitImported: mocks.submitImported };
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
  useSolvePlan: () => ({
    plan: { engine: 'metal', formulation: 'full-3d', reason: 'test', eligibility_reasons: [] },
    error: null,
    isPending: false,
  }),
}));
vi.mock('../jobs/useImportedSolvePlan', () => ({
  useImportedSolvePlan: (enabled: boolean) => (enabled ? {
    plan: {
      ingest_id: 'wgi_test', requested: 'auto', engine: 'metal', code: null,
      reason: 'AUTO selected the first available engine', domain: 'full',
      engines: [{ name: 'metal', label: 'Metal', solves: true }],
    },
    error: null,
    isPending: false,
  } : { plan: null, error: null, isPending: false }),
}));

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function cadJob(id: string, ingestId: string, status: JobItem['status'] = 'complete'): JobItem {
  return {
    id, run_number: 1, parent_job_id: null, label: id, status, progress: status === 'complete' ? 1 : 0.4,
    stage: null, stage_message: null, created_at: '2026-09-21T00:00:00Z',
    queued_at: '2026-09-21T00:00:00Z', started_at: null, completed_at: status === 'complete' ? '2026-09-21T00:00:01Z' : null,
    config_summary: { geometry_type: 'imported' }, solve_options: {} as JobItem['solve_options'],
    has_results: status === 'complete', has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null, script_snapshot: null,
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
    cad_source: {
      ingest_id: ingestId, design_id: null, lineage_id: 'wgl_test', archive_stem: null,
      manifest_sha256: null, document_name: 'Speaker', return_state_hash: null,
    },
  } as JobItem;
}

function operation(operationId: string, state = 'received', overrides: Partial<CadOperationSummary> = {}): CadOperationSummary {
  return {
    operationId, kind: 'prepare_and_solve', state, stage: 'received', reason: null, message: null,
    jobId: null, attemptGeneration: 0, setupRevisionId: null, preparationId: null,
    snapshot: { manifestSha256: `sha256:${'1'.repeat(64)}` }, legacy: false,
    createdAt: '2026-09-21T10:00:00Z', updatedAt: '2026-09-21T10:00:00Z',
    ...overrides,
  };
}

function readyCad(ingestId: string): void {
  const record = {
    ingest_id: ingestId,
    manifest_sha256: `sha256:${'1'.repeat(64)}`,
    artifact_sha256: `sha256:${'2'.repeat(64)}`,
    report_sha256: `sha256:${'3'.repeat(64)}`,
    findings: [], evidence: { fem_air_volumes: [] }, polar_grid_derivation: {},
  } as unknown as CadReturnIngestRecord;
  useCadReturnStore.setState({
    selectedBundle: {
      name: 'speaker.wgreturn', bundlePath: 'returns/speaker.wgreturn', modifiedAt: '2026-09-21T10:00:00Z',
      readable: true, documentName: 'Speaker', requestId: null, sourceCount: 1, instanceCount: 1,
      designIds: [], sources: [{ id: 'source-hf', role: 'source', required: true, suggestedResolutionMm: 4, defaultDriveChannelId: 'drive-hf' }],
    },
    projectLineageId: 'wgl_test', ingestRecord: record, needsIngest: false,
    driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
    sourceSizesMm: { 'source-hf': 4 }, rigidSizeMm: 8, transitionMm: 12, skippedSourceIds: [],
  });
  importedMeshStore.setCad({ name: 'Fusion speaker', source: 'cad', ingestId } as ImportedMeshScene);
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

const flush = async (times = 4) => {
  for (let index = 0; index < times; index += 1) await Promise.resolve();
};

describe('M1 acceptance: Solve to the revealed result, in CAD Link mode', () => {
  let host: HTMLDivElement;
  let root: Root;
  /** Every panel the application asked the dock to front, in order. */
  let activations: WorkspacePanel[];
  /** What the dock shows; Results starts behind another tab. */
  let visible: Set<WorkspacePanel>;

  const dockActivate = (panel: WorkspacePanel) => {
    activations.push(panel);
    // A tab fronted in the shared group hides the one it covered.
    if (panel === 'results') visible.delete('cadlink');
    if (panel === 'cadlink') visible.delete('results');
    visible.add(panel);
    publishVisiblePanels(visible);
    return true;
  };

  async function mount(): Promise<void> {
    await act(async () => {
      root.render(<JobsCoordinator now={() => new Date(2026, 8, 21, 12)}>
        <div className="topbar"><SolveActions/></div>
        <ResultsPanel/>
      </JobsCoordinator>);
      await flush();
    });
  }

  async function pressSolve(): Promise<string> {
    const button = host.querySelector<HTMLButtonElement>('.solve-button')!;
    expect(button.disabled).toBe(false);
    await act(async () => { button.click(); await flush(8); });
    const calls = mocks.createCadOperation.mock.calls;
    return calls[calls.length - 1][0].operationId as string;
  }

  async function deliver(summary: CadOperationSummary): Promise<void> {
    await act(async () => { useCadOperationsStore.getState().apply(summary); await flush(); });
  }

  async function jobs(list: JobItem[]): Promise<void> {
    await act(async () => { publishJobs(list); await flush(8); });
  }

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['summary'] });
    resetDocumentStore();
    useDocumentStore.getState().setDesignName('horn');
    resetDesignStore();
    resetCadReturnStore();
    resetCadOperationsStore();
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
    vi.stubGlobal('fetch', vi.fn(async () => resultResponse()));
    mocks.createSetupRevision.mockImplementation(async (setup: CadSolveSetup) => ({
      revisionId: `wgs_${mocks.createSetupRevision.mock.calls.length}`, contentSha256: 'sha256:setup', createdAt: 'now', setup,
    }));
    mocks.createCadOperation.mockImplementation(async ({ operationId }: { operationId: string }) => operation(operationId));
    mocks.prepareCadOperation.mockImplementation(async (operationId: string) => operation(operationId, 'processing', { stage: 'validating' }));
    mocks.getCadOperation.mockRejectedValue(new CadLinkApiError('Unknown CAD operation', [], 404));
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    readyCad('wgi_first');
    workspaceModeStore.setMode('cad');
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
    await mount();
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

  it('runs the whole chain: Solve, operation, job, selected result, revealed Results, CAD mode kept', async () => {
    const operationId = await pressSolve();

    // Capture the intended input: the setup on screen, bound once.
    expect(mocks.createSetupRevision).toHaveBeenCalledOnce();
    // Create or recover the operation, for the ingestion on screen.
    expect(mocks.createCadOperation).toHaveBeenCalledWith(expect.objectContaining({ operationId, ingestId: 'wgi_first' }));
    // Submit the job.
    expect(mocks.prepareCadOperation).toHaveBeenCalledWith(operationId, { setupRevisionId: 'wgs_1', submit: true });

    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', stage: 'submitted', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-1', 'wgi_first', 'running')]);
    expect(activations).not.toContain('results');
    await jobs([cadJob('job-1', 'wgi_first')]);

    // Select the finished result, reveal the existing Results panel.
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'job-1', awaiting: null });
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
    expect(workspaceNavigation.isVisible('results')).toBe(true);
    // CAD Link mode is still selected.
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  it('takes the primary slot from an older pinned result once the requested run has results', async () => {
    await jobs([cadJob('old-pinned', 'wgi_first')]);
    act(() => compareSelection.setPrimary('old-pinned'));
    const operationId = await pressSolve();
    await deliver(operation(operationId, 'accepted', { jobId: 'job-new', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-new', 'wgi_first', 'running'), cadJob('old-pinned', 'wgi_first')]);
    // The pin holds for the length of the solve.
    expect(compareSelection.getSnapshot().primary).toBe('old-pinned');
    await jobs([cadJob('job-new', 'wgi_first'), cadJob('old-pinned', 'wgi_first')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-new');
    expect(activations).toContain('results');
  });

  it('reveals Results when it is hidden behind another tab', async () => {
    expect(workspaceNavigation.isVisible('results')).toBe(false);
    const operationId = await pressSolve();
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-1', 'wgi_first')]);
    expect(workspaceNavigation.isVisible('results')).toBe(true);
    expect(workspaceNavigation.isVisible('cadlink')).toBe(false);
  });

  it('makes no second job and no second result when the completion is delivered twice', async () => {
    const operationId = await pressSolve();
    const accepted = operation(operationId, 'accepted', { jobId: 'job-1', updatedAt: '2026-09-21T10:00:05Z' });
    await deliver(accepted);
    await jobs([cadJob('job-1', 'wgi_first')]);
    // The same completion again, as a later copy and as a reconnect's replay.
    await deliver({ ...accepted, updatedAt: '2026-09-21T10:00:06Z' });
    await deliver({ ...accepted, updatedAt: '2026-09-21T10:00:07Z' });
    await jobs([cadJob('job-1', 'wgi_first')]);

    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    expect(mocks.submitImported).not.toHaveBeenCalled();
    expect(activations.filter((panel) => panel === 'results')).toEqual(['results']);
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'job-1', overlays: [] });
    expect(solveAttention.resultsReady('job-1')).toBe('repeated');
    // The run counter moved once for one run.
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'Speaker', runSequenceNext: 2 });
  });

  it('keeps the result owned by its original input when a new CAD snapshot arrives while it runs', async () => {
    // A session already showing a result: the opening-selection fallback, which
    // would pin any drawable run, is spent, as it is in real use.
    await jobs([cadJob('earlier', 'wgi_first')]);
    expect(compareSelection.getSnapshot().primary).toBe('earlier');
    const operationId = await pressSolve();
    // A newer return is prepared and put on screen while the first solve runs.
    act(() => readyCad('wgi_second'));
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-1', 'wgi_first'), cadJob('earlier', 'wgi_first')]);

    // The run the user asked for is shown, for the model it was submitted with,
    // and a newer model on screen does not hand the slot on.
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'job-1', following: false });
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.createCadOperation).toHaveBeenCalledWith(expect.objectContaining({ ingestId: 'wgi_first' }));
    await jobs([cadJob('job-1', 'wgi_first'), cadJob('earlier', 'wgi_first')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
    expect(activations).toContain('results');
  });

  it('selects and reveals the result of a solve Fusion sent, which WG watched finish', async () => {
    // Fusion's Solve command, delivered to the backend: WG sees it arrive...
    await deliver(operation('cmd-fusion-1', 'processing', { stage: 'validating' }));
    await deliver(operation('cmd-fusion-1', 'accepted', { jobId: 'job-fusion', stage: 'submitted', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-fusion', 'wgi_first')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-fusion');
    expect(activations).toContain('results');
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    // No WG-side request was made for it: the backend owns that solve.
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
  });

  it('leaves a Fusion solve that had already finished before this page saw it as history', async () => {
    await jobs([cadJob('earlier', 'wgi_first')]);
    await deliver(operation('cmd-old', 'accepted', { jobId: 'job-old', updatedAt: '2026-09-21T09:00:00Z' }));
    await jobs([cadJob('job-old', 'wgi_first'), cadJob('earlier', 'wgi_first')]);
    expect(compareSelection.getSnapshot().awaiting).toBeNull();
    expect(activations).not.toContain('results');
    // The result on screen stays where it was.
    expect(compareSelection.getSnapshot().primary).toBe('earlier');
  });

  // -- Reconnect: the outcome the page was waiting for (whole-stack review F1) --

  /** Exactly a reconnect's two reads, against a server that answers `finished`. */
  async function reconnect(finished: CadOperationSummary[]): Promise<void> {
    const api = (async (input: RequestInfo | URL) => new Response(JSON.stringify({
      operations: String(input).includes('pending=false') ? finished : [],
    }), { status: 200, headers: { 'Content-Type': 'application/json' } })) as typeof fetch;
    await act(async () => {
      await Promise.all([useCadOperationsStore.getState().load(api), recoverMissedSnapshots(Date.now() - 60_000, api)]);
      await flush();
    });
  }

  it.each(['manual', 'fusion'])('lets a %s solve that finished while disconnected still replace the pinned result', async (origin) => {
    await jobs([cadJob('old-pinned', 'wgi_first')]);
    act(() => compareSelection.setPrimary('old-pinned'));
    const operationId = origin === 'manual' ? await pressSolve() : 'fusion-new';
    if (origin === 'fusion') await deliver(operation(operationId, 'processing'));
    await reconnect([operation(operationId, 'accepted', { jobId: 'job-new', updatedAt: new Date().toISOString() })]);
    await jobs([cadJob('job-new', 'wgi_first'), cadJob('old-pinned', 'wgi_first')]);
    expect(compareSelection.getSnapshot().primary).toBe('job-new');
    expect(activations).toContain('results');
  });

  it('never lets an unrelated finished solve in the recovery take the result slot', async () => {
    await jobs([cadJob('old-pinned', 'wgi_first')]);
    act(() => compareSelection.setPrimary('old-pinned'));
    await reconnect([operation('someone-elses', 'accepted', { jobId: 'job-other', updatedAt: new Date().toISOString() })]);
    await jobs([cadJob('job-other', 'wgi_first'), cadJob('old-pinned', 'wgi_first')]);
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'old-pinned', awaiting: null });
    expect(activations).not.toContain('results');
  });

  it('says so when a solve the page was waiting for was refused while disconnected', async () => {
    const operationId = await pressSolve();
    await reconnect([operation(operationId, 'rejected', {
      reason: 'snapshot_invalid', message: 'The return in the WGLink folder is not the one Fusion named.',
      updatedAt: new Date().toISOString(),
    })]);
    const { jobsCoordinatorBridge: bridge } = await import('./JobsCoordinator');
    expect(bridge.getSnapshot().actionError).toContain('not the one Fusion named');
  });

  // -- V4: the viewport is the contract -------------------------------------

  it('refuses to solve when the displayed CAD mesh is not the selected ingestion', async () => {
    act(() => { importedMeshStore.setCad({ name: 'Other', source: 'cad', ingestId: 'wgi_other' } as ImportedMeshScene); });
    await act(async () => { await flush(); });
    // The button, at render time.
    const button = host.querySelector<HTMLButtonElement>('.solve-button')!;
    expect(button.disabled).toBe(true);
    expect(button.title).toContain('does not match the selected ingestion');
    // The shortcut.
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }));
      await flush();
    });
    // Every caller, at call time.
    await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).rejects.toThrow('does not match the selected ingestion');
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.createSetupRevision).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).not.toHaveBeenCalled();
  });

  it('solves once the displayed mesh is the selected ingestion again (the control)', async () => {
    act(() => { importedMeshStore.setCad({ name: 'Other', source: 'cad', ingestId: 'wgi_other' } as ImportedMeshScene); });
    act(() => { importedMeshStore.setCad({ name: 'Fusion speaker', source: 'cad', ingestId: 'wgi_first' } as ImportedMeshScene); });
    await act(async () => { await flush(); });
    await pressSolve();
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
  });

  // -- V1: explicit navigation disarms the reveal ---------------------------

  it.each([
    ['a workspace mode switch', () => { activateWorkspaceMode('parametric'); activateWorkspaceMode('cad'); }],
    ['opening a design or project', () => { takeDesignOpenTicket(); }],
    ['showing a run\'s own model', () => { void showJobModel({ ...cadJob('x', 'wgi_first'), config_summary: {}, script_snapshot: null } as JobItem); }],
  ])('does not reveal Results after %s during the solve, and says the results are ready', async (_name, navigate) => {
    const operationId = await pressSolve();
    const before = activations.length;
    act(() => { navigate(); });
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-1', 'wgi_first')]);
    expect(activations.slice(before)).not.toContain('results');
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
    expect(host.querySelectorAll('.attention-ready')).toHaveLength(1);
  });

  it('reveals Results after a parametric Solve too', async () => {
    act(() => { workspaceModeStore.setMode('parametric'); });
    mocks.planSolveDesign.mockResolvedValue({ engine: 'metal', formulation: 'full-3d', reason: 'test', eligibility_reasons: [] });
    mocks.submitDesign.mockResolvedValue('job-p');
    await act(async () => { await flush(); });
    const button = host.querySelector<HTMLButtonElement>('.solve-button')!;
    expect(button.disabled).toBe(false);
    await act(async () => { button.click(); await flush(8); });
    expect(mocks.submitDesign).toHaveBeenCalledOnce();
    const parametric = { ...cadJob('job-p', 'wgi_first'), config_summary: {}, cad_source: null } as JobItem;
    await jobs([parametric]);
    expect(compareSelection.getSnapshot().primary).toBe('job-p');
    expect(activations).toContain('results');
  });

  it('keeps the result bound to the settings submitted when settings change during the run', async () => {
    act(() => useSolveOptionsStore.getState().setEngine('metal'));
    const operationId = await pressSolve();
    const submitted = mocks.createSetupRevision.mock.calls[0][0] as CadSolveSetup;
    act(() => useSolveOptionsStore.getState().setEngine('bempp'));
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', setupRevisionId: 'wgs_1', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-1', 'wgi_first')]);

    // Nothing was bound or prepared again for the edited settings.
    expect(mocks.createSetupRevision).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation).toHaveBeenCalledWith(operationId, { setupRevisionId: 'wgs_1', submit: true });
    expect(submitted.options.engine).toBe('metal');
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
  });

  it('does not pull the user back when the solve finishes after they deliberately navigated elsewhere', async () => {
    const operationId = await pressSolve();
    // The user chooses the Jobs tab while the solve runs.
    act(() => { workspaceNavigation.navigate('jobs'); });
    const before = activations.length;
    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', updatedAt: '2026-09-21T10:00:05Z' }));
    await jobs([cadJob('job-1', 'wgi_first')]);

    expect(activations.slice(before)).not.toContain('results');
    // The claim still resolves: the run is the primary result...
    expect(compareSelection.getSnapshot().primary).toBe('job-1');
    // ...and an unobtrusive indication says so, with the way to it.
    const ready = [...host.querySelectorAll<HTMLButtonElement>('.attention-ready')];
    expect(ready).toHaveLength(1);
    expect(ready[0].textContent).toContain('Results ready');
    await act(async () => { ready[0].click(); await flush(); });
    expect(workspaceNavigation.isVisible('results')).toBe(true);
    expect(host.querySelector('.attention-ready')).toBeNull();
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
  });

  it('fronts the CAD Link panel when the solve stops at a gate, and still reveals Results once it is solved', async () => {
    const operationId = await pressSolve();
    act(() => { dockActivate('results'); });
    activations = [];
    await deliver(operation(operationId, 'needs_user_input', {
      reason: 'frame_confirmation_required', stage: 'ready', preparationId: 'wgp_1',
      attemptGeneration: 1, updatedAt: '2026-09-21T10:00:02Z',
    }));
    expect(activations).toEqual(['cadlink']);
    expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    // The same gate reported again is not a new event.
    await deliver(operation(operationId, 'needs_user_input', {
      reason: 'frame_confirmation_required', stage: 'ready', preparationId: 'wgp_1',
      attemptGeneration: 1, updatedAt: '2026-09-21T10:00:03Z',
    }));
    expect(activations).toEqual(['cadlink']);

    await deliver(operation(operationId, 'accepted', { jobId: 'job-1', attemptGeneration: 2, updatedAt: '2026-09-21T10:00:09Z' }));
    await jobs([cadJob('job-1', 'wgi_first')]);
    expect(activations).toEqual(['cadlink', 'results']);
    expect(navigationGeneration()).toBe(0);
  });
});
