import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { preferencesStore } from '../prefs/preferences';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { ImportedSolveSubmission } from '../jobs/actions';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import { JobsCoordinator, jobsCoordinatorBridge, useSolveControl } from './JobsCoordinator';
import { SolveActions } from './TopBar';
import { JobsPanel } from './JobsPanel';

const mocks = vi.hoisted(() => ({
  submitDesign: vi.fn(),
  submitImported: vi.fn(),
}));

vi.mock('../jobs/actions', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../jobs/actions')>();
  return { ...actual, submitDesign: mocks.submitDesign, submitImported: mocks.submitImported };
});
vi.mock('../jobs/useCapabilities', () => ({
  useCapabilities: () => ({
    engines: [{ name: 'metal', available: true, reason: null, version: null, fast_paths: [] }, { name: 'bempp', available: true, reason: null, version: null, fast_paths: [] }, { name: 'dryrun', available: true, reason: null, version: null, fast_paths: [] }],
    error: null,
    isLoading: false,
  }),
  useCapabilityRefreshOnReconnect: () => undefined,
}));

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

function MainSolveButton() {
  const solve = useSolveControl();
  return <button disabled={solve.disabled} title={solve.title} onClick={solve.solve}>{solve.label}</button>;
}

describe('solve invocation mutex', () => {
  let host: HTMLDivElement;
  let root: Root;

  beforeEach(async () => {
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    preferencesStore.resetForTests();
    resetDocumentStore();
    useDocumentStore.getState().setDesignName('horn');
    resetDesignStore();
    resetCadReturnStore();
    resetSolveOptionsStore();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
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
    vi.restoreAllMocks();
    vi.clearAllMocks();
    workspaceModeStore.setMode('parametric');
  });

  it('submits only once when run is invoked twice in the same tick', async () => {
    const pending = deferred<string>();
    mocks.submitDesign.mockReturnValue(pending.promise);
    const design = designForFamily('OSSE');
    let first!: Promise<void>;
    let second!: Promise<void>;

    await act(async () => {
      const run = jobsCoordinatorBridge.getSnapshot().run;
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

  it('submits again after the first invocation resolves', async () => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(design); });
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(design); });

    expect(mocks.submitDesign).toHaveBeenCalledTimes(2);
    expect(mocks.submitDesign.mock.calls.map((call) => call[3].label)).toEqual(['horn1', 'horn2']);
  });

  it('materializes the 5 mm ATH wall default before submitting to BEMPP', async () => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');
    design.mesh.wall_thickness = 0;
    design.enclosure.depth = 0;
    design.simulation.sim_type = 'freestanding';
    act(() => {
      useDesignStore.getState().loadDesign(design);
      useSolveOptionsStore.getState().setEngine('bempp');
    });

    await act(async () => {
      const state = useDesignStore.getState();
      await jobsCoordinatorBridge.getSnapshot().run(state.design, state.designRevision);
    });

    expect(useDesignStore.getState().design.mesh.wall_thickness).toBe(5);
    expect(mocks.submitDesign).toHaveBeenCalledOnce();
    expect(mocks.submitDesign.mock.calls[0][0].mesh.wall_thickness).toBe(5);
    expect(mocks.submitDesign.mock.calls[0][3].designRevision).toBe(
      useDesignStore.getState().designRevision,
    );
  });

  it('numbers each run of the design in sequence and never renames the design', async () => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(design); });
    const changed = structuredClone(design);
    changed.simulation.f2 += 1_000;
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(changed); });
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(changed); });

    expect(mocks.submitDesign.mock.calls.map((call) => call[3].label)).toEqual(['horn1', 'horn2', 'horn3']);
    expect(useDocumentStore.getState().designName).toBe('horn');
  });

  it('does not overwrite a rename made while a submission was in flight', async () => {
    const pending = deferred<string>();
    mocks.submitDesign.mockReturnValue(pending.promise);
    let run!: Promise<void>;
    await act(async () => {
      run = jobsCoordinatorBridge.getSnapshot().run(designForFamily('OSSE'));
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

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(design); });
    const changed = structuredClone(design);
    changed.simulation.f2 += 1_000;
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(changed); });

    expect(mocks.submitDesign.mock.calls.map((call) => call[3].label)).toEqual(['horn1_260812', 'horn2_260812']);
    expect(useDocumentStore.getState().designName).toBe('horn');
  });

  it('releases the guard after a rejected submission', async () => {
    mocks.submitDesign
      .mockRejectedValueOnce(new Error('submit failed'))
      .mockResolvedValueOnce('job-two');
    const design = designForFamily('OSSE');

    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().run(design)).rejects.toThrow('submit failed');
    });
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(design); });

    expect(mocks.submitDesign).toHaveBeenCalledTimes(2);
  });

  it('routes imported submissions through the same invocation mutex', async () => {
    const pending = deferred<string>();
    mocks.submitImported.mockReturnValue(pending.promise);
    const submission = importedSubmission('wgi_example');
    let first!: Promise<string | null>;
    await act(async () => {
      const run = jobsCoordinatorBridge.getSnapshot().runImported;
      first = run(submission);
      await run(submission);
      await jobsCoordinatorBridge.getSnapshot().run(designForFamily('OSSE'));
    });
    expect(mocks.submitImported).toHaveBeenCalledOnce();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
    await act(async () => { pending.resolve('job-imported'); await first; });
  });

  // A typed refusal reaches the browser as "<reason_code>: <message>", which
  // names the condition without naming the fix. Translating inside runImported
  // means every imported entry point gets the advice, not just the one that
  // happened to be wired up.
  it('turns a passive-cardioid topology refusal into the CAD change that fixes it', async () => {
    mocks.submitImported.mockRejectedValue(new Error(
      'passive_cardioid_topology: coupled passive cardioid requires all PORT_EXIT patches in one drive channel',
    ));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_refused')))
        .rejects.toThrow(/same drive channel/);
    });
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_refused')))
        .rejects.not.toThrow(/passive_cardioid_topology/);
    });
  });

  it('names CAD solves from the same design name and numbers them in the same sequence', async () => {
    mocks.submitImported.mockResolvedValue('job-cad');
    const first = importedSubmission('wgi_first');

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().runImported(first); });
    act(() => useDesignStore.getState().updateField('R', 141));
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().runImported(structuredClone(first)); });
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_second')); });

    expect(mocks.submitImported.mock.calls.map((call) => call[2])).toEqual(['horn1', 'horn2', 'horn3']);
  });

  it('submits a full CAD solve from the main control without mounting CadLinkPanel', async () => {
    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    const record = {
      ingest_id: ingestId,
      manifest_sha256: `sha256:${'1'.repeat(64)}`,
      artifact_sha256: `sha256:${'2'.repeat(64)}`,
      report_sha256: `sha256:${'3'.repeat(64)}`,
      findings: [],
      evidence: { fem_air_volumes: [] },
      polar_grid_derivation: {},
    } as unknown as CadReturnIngestRecord;
    useCadReturnStore.setState({
      ingestRecord: record,
      needsIngest: false,
      driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      sourceSizesMm: { 'source-hf': 4 },
      rigidSizeMm: 8,
      transitionMm: 12,
      skippedSourceIds: [],
      acknowledgedFindingIds: [],
    });
    importedMeshStore.setCad({ name: 'Fusion speaker', source: 'cad', ingestId } as ImportedMeshScene);
    mocks.submitImported.mockResolvedValue('job-cad');

    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.textContent).toBe('Solve');
    act(() => workspaceModeStore.setMode('cad'));
    expect(solve.textContent).toBe('Solve CAD Link');
    expect(solve.title).toContain('displayed CAD Link model');
    await act(async () => { solve.click(); await Promise.resolve(); await Promise.resolve(); });

    expect(mocks.submitImported).toHaveBeenCalledOnce();
    expect(mocks.submitImported.mock.calls[0][0].geometry.ingest_id).toBe(ingestId);
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('gates solveCurrentCadImport on readiness and reports a busy solve instead of dropping it', async () => {
    // Automatic callers (Pull & Solve, a Fusion solve command) use this action,
    // so its refusal has to be a thrown reason and never a silent no-op.
    await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport())
      .rejects.toThrow('Ingest a CAD return before solving.');
    expect(mocks.submitImported).not.toHaveBeenCalled();

    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    useCadReturnStore.setState({
      ingestRecord: {
        ingest_id: ingestId,
        manifest_sha256: `sha256:${'1'.repeat(64)}`,
        artifact_sha256: `sha256:${'2'.repeat(64)}`,
        report_sha256: `sha256:${'3'.repeat(64)}`,
        findings: [],
        evidence: { fem_air_volumes: [] },
        polar_grid_derivation: {},
      } as unknown as CadReturnIngestRecord,
      needsIngest: false,
      driveChannels: [{ id: 'drive-hf', source_ids: ['source-hf'], motion: 'normal' }],
      sourceSizesMm: { 'source-hf': 4 },
      skippedSourceIds: [],
      acknowledgedFindingIds: [],
    });
    const pending = deferred<string>();
    mocks.submitImported.mockReturnValue(pending.promise);

    let first!: Promise<'submitted' | 'busy'>;
    let second!: 'submitted' | 'busy';
    await act(async () => {
      first = jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
      second = await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
    });
    expect(second).toBe('busy');
    expect(mocks.submitImported).toHaveBeenCalledOnce();
    await act(async () => { pending.resolve('job-cad'); await expect(first).resolves.toBe('submitted'); });
  });

  it('makes the Fusion pull the primary action when Fusion moved past the prepared geometry', async () => {
    const pullAndSolve = vi.fn(async () => 'solving' as const);
    vi.spyOn(cadLinkCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...cadLinkCoordinatorBridge.getSnapshot(),
      pullAndSolve,
      fusionStatus: { running: true, fusionChangesAvailable: true } as never,
    });
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    const buttons = [...host.querySelectorAll<HTMLButtonElement>('.solve-button')];
    expect(buttons[0].textContent).toContain('Pull from Fusion & Solve');
    // The geometry WG already prepared stays solvable beside it.
    expect(buttons[1].textContent).toContain('Solve prepared');

    await act(async () => { buttons[0].click(); await Promise.resolve(); });
    expect(pullAndSolve).toHaveBeenCalledOnce();
    expect(mocks.submitImported).not.toHaveBeenCalled();
  });

  it('enters CAD mode without an ingest and exposes the submission blocker', async () => {
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
      workspaceModeStore.setMode('cad');
    });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.textContent).toBe('Solve CAD Link');
    expect(solve.disabled).toBe(true);
    expect(solve.title).toBe('Ingest a CAD return before solving.');
  });

  it('guards two fast retries routed through the coordinator bridge', async () => {
    // A retry replays the stored run on the server rather than resubmitting the
    // design from the browser, so the guarded call here is retryJob.
    const pending = deferred<void>();
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
      pending.resolve();
      await pending.promise;
      await Promise.resolve();
    });
  });
});
