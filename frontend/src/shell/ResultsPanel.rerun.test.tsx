import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection, provisionalResults, resultsCache } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { seedDesign, serializeDesign } from '../stores/design';
import { provideExportDestinationPrompt } from './exportDestinationPrompt';
import { JobsCoordinator, jobsCoordinatorBridge } from './JobsCoordinator';
import { ResultsPanel } from './ResultsPanel';
import { resetSolveAttentionForTests, solveAttention } from './solveAttention';
import { resetSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';

vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({ error: null, engines: [], refreshCapabilities: async () => undefined }),
  useCapabilityRefreshOnReconnect: () => undefined,
  useLegacyBeatEngineMigration: () => undefined,
}));
vi.mock('../jobs/useSolvePlan', () => ({ useSolvePlan: () => ({ plan: null, error: null, isPending: false }) }));
vi.mock('../jobs/useImportedSolvePlan', () => ({ useImportedSolvePlan: () => ({ plan: null, error: null, isPending: false }) }));

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

const rerun = vi.hoisted(() => ({ rerunWithPatch: vi.fn() }));
vi.mock('../jobs/rerun', () => ({ rerunWithPatch: rerun.rerunWithPatch }));

function job(id: string, imported = false): JobItem {
  return {
    id, run_number: 1, parent_job_id: null,
    label: id, status: 'complete', progress: 1, stage: null,
    stage_message: null, created_at: '2026-08-08T00:00:00Z',
    queued_at: '2026-08-08T00:00:00Z', started_at: null,
    completed_at: '2026-08-08T00:00:01Z', config_summary: {}, solve_options: {} as JobItem['solve_options'],
    has_results: true, has_mesh_artifact: false, error_message: null,
    cancellation_requested: false, mesh_stats: null,
    script_snapshot: { version: 1, design: serializeDesign(seedDesign) },
    design_revision: 1, polar_grid: {}, rating: null, exported_files: [],
    auto_export_completed_at: null, auto_export_formats: {}, raw_results_file: null,
    mesh_artifact_file: null, log_tail: [],
    cad_source: imported ? {
      ingest_id: 'i', design_id: 'd', lineage_id: 'l', archive_stem: id,
      manifest_sha256: 'sha256:x', document_name: 'doc', return_state_hash: null,
    } : null,
  };
}

function publishJobs(jobs: JobItem[]): void {
  const manager = jobsSocket as unknown as { snapshot: JobsSnapshot; listeners: Set<() => void> };
  manager.snapshot = { connection: 'connected', epoch: 1, cursor: 1, jobs, error: null };
  manager.listeners.forEach((listener) => listener());
}

function response(frequencies: number[], ok = true): Response {
  const digest = 'a'.repeat(64);
  const result = {
    result_kind: 'parametric',
    result_contract_version: 1,
    client_request_id: null,
    client_metadata: {},
    provenance: {
      schema_version: 1,
      wg_version: 'test',
      dependency_shas: {},
      request_sha256: digest,
      geometry_sha256: digest,
      solve_options_sha256: digest,
      request_identity: 'execution',
      execution_request_sha256: digest,
      execution_geometry_sha256: digest,
      execution_solve_options_sha256: digest,
      effective_request_sha256: digest,
      effective_geometry_sha256: digest,
      effective_solve_options_sha256: digest,
      resolved_engine: 'test',
    },
    frequencies,
    metadata: {},
  };
  return new Response(JSON.stringify(ok ? result : { detail: 'result vanished' }), {
    status: ok ? 200 : 500,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('Enable & rerun wiring', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    resultsCache.clear();
    provisionalResults.clear();
    compareSelection.clear();
    preferencesStore.resetForTests();
    preferencesStore.update({ chartTypes: ['beam_shape'] });
    provideExportDestinationPrompt(async () => ({ token: 't', directory: '/exports' }));
    rerun.rerunWithPatch.mockReset().mockResolvedValue('job-new');
    resetSolveAttentionForTests();
    resetSolveOptionsStore();
    workspaceModeStore.setMode('parametric');
    vi.spyOn(jobsSocket, 'start').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'stop').mockImplementation(() => undefined);
    vi.spyOn(jobsSocket, 'refresh').mockResolvedValue(undefined);
    vi.stubGlobal('fetch', vi.fn(async () => response([100])));
    publishJobs([job('parametric-run'), job('cad-run', true)]);
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    provideExportDestinationPrompt(null);
    act(() => root.unmount());
    host.remove();
    publishJobs([]);
    compareSelection.clear();
    resultsCache.clear();
    provisionalResults.clear();
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  const button = () => [...host.querySelectorAll('button')].find((item) => item.textContent === 'Enable & rerun');

  it('reruns the selected run with spherical sampling switched on', async () => {
    compareSelection.setPrimary('parametric-run');
    await act(async () => { root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>); });
    await act(async () => { await Promise.resolve(); });
    expect(button()).toBeTruthy();
    const pending = deferred<string>();
    rerun.rerunWithPatch.mockReturnValueOnce(pending.promise);
    const arm = vi.spyOn(solveAttention, 'armSolve');
    const bind = vi.spyOn(solveAttention, 'bindRun');
    const blocked = vi.spyOn(jobsSocket, 'retryJob').mockResolvedValue('wrong-job');
    await act(async () => {
      button()!.click(); button()!.click();
      expect(await jobsCoordinatorBridge.getSnapshot().continueRun('blocked')).toBeNull();
    });
    expect(arm).toHaveBeenCalledOnce();
    expect(arm.mock.invocationCallOrder[0]).toBeLessThan(rerun.rerunWithPatch.mock.invocationCallOrder[0]);
    expect(rerun.rerunWithPatch).toHaveBeenCalledOnce();
    expect(blocked).not.toHaveBeenCalled();
    await act(async () => { pending.resolve('job-new'); await pending.promise; });
    expect(compareSelection.getSnapshot()).toMatchObject({ primary: 'parametric-run', following: true, awaiting: 'job-new' });
    expect(bind.mock.calls.map(([id]) => id)).toContain('job-new');
    expect(jobsSocket.refresh).toHaveBeenCalledOnce();
    expect(solveAttention.resultsReady('job-new')).toBe('revealed');
    expect(rerun.rerunWithPatch).toHaveBeenCalledWith(
      'parametric-run', { options: { polar_config: { spherical_sampling: true } } },
    );
  });

  it('releases attention, reports a refusal and permits another press', async () => {
    rerun.rerunWithPatch.mockRejectedValueOnce(new Error('rerun refused'));
    const release = vi.spyOn(solveAttention, 'releaseSolve');
    compareSelection.setPrimary('parametric-run');
    await act(async () => root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>));
    await act(async () => { button()!.click(); });
    expect(release).toHaveBeenCalledOnce();
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('rerun refused');
    expect(compareSelection.getSnapshot().awaiting).toBeNull();
    expect(jobsSocket.refresh).not.toHaveBeenCalled();
    solveAttention.bindRun('unrelated');
    expect(solveAttention.resultsReady('unrelated')).toBe('indicated');
    await act(async () => { button()!.click(); });
    expect(rerun.rerunWithPatch).toHaveBeenCalledTimes(2);
    expect(compareSelection.getSnapshot().awaiting).toBe('job-new');
  });

  it('reports a busy coordinator without submitting or arming the rerun', async () => {
    compareSelection.setPrimary('parametric-run');
    await act(async () => root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>));
    const pending = deferred<string>();
    vi.spyOn(jobsSocket, 'retryJob').mockReturnValue(pending.promise);
    let other!: Promise<string | null>;
    await act(async () => { other = jobsCoordinatorBridge.getSnapshot().continueRun('other'); });
    const arm = vi.spyOn(solveAttention, 'armSolve');
    await act(async () => { button()!.click(); });
    expect(rerun.rerunWithPatch).not.toHaveBeenCalled();
    expect(arm).not.toHaveBeenCalled();
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe('Another solve is being submitted. Try again in a moment.');
    await act(async () => { pending.resolve('other-child'); await other; });
  });

  it('does not offer the rerun on an imported run', async () => {
    compareSelection.setPrimary('cad-run');
    await act(async () => { root.render(<JobsCoordinator><ResultsPanel/></JobsCoordinator>); });
    await act(async () => { await Promise.resolve(); });
    expect(button()).toBeUndefined();
  });
});
