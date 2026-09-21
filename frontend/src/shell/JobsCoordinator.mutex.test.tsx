import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import type { CadOperationSummary, CadSolveSetup } from '../api/cadOperations';
import { resetCadOperationsStore, useCadOperationsStore } from '../stores/cadOperations';
import { compareSelection } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import { CadLinkApiError, type CadReturnIngestRecord } from '../api/cadlink';
import { SolveSubmissionRefused, type ImportedSolveSubmission } from '../jobs/actions';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resolveOuterBodyMode } from '../design/ParamPanel';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import {
  JobsCoordinator,
  jobsCoordinatorBridge,
  refreshedArchiveJob,
  SolveEngineUnavailableError,
  useSolveControl,
} from './JobsCoordinator';
import { SolveActions } from './TopBar';
import { JobsPanel } from './JobsPanel';

const mocks = vi.hoisted(() => ({
  planSolveDesign: vi.fn(),
  submitDesign: vi.fn(),
  submitImported: vi.fn(),
  createSetupRevision: vi.fn(),
  createCadOperation: vi.fn(),
  prepareCadOperation: vi.fn(),
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
      full3dOrder: ['metal', 'bempp', 'dryrun'], axisymmetricRunner: 'axisym',
    },
  },
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
  return {
    ...actual,
    planSolveDesign: mocks.planSolveDesign,
    submitDesign: mocks.submitDesign,
    submitImported: mocks.submitImported,
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
vi.mock('../jobs/useSolvePlan', () => ({
  useSolvePlan: () => ({
    plan: mocks.solvePlan,
    error: mocks.solvePlanError,
    isPending: mocks.solvePlanPending,
  }),
}));
vi.mock('../jobs/useImportedSolvePlan', () => ({
  useImportedSolvePlan: (enabled: boolean) => (
    enabled ? mocks.importedPlan : { plan: null, error: null, isPending: false }
  ),
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
    resetCadOperationsStore();
    resetSolveOptionsStore();
    sessionStorage.clear();
    importedMeshStore.clear();
    workspaceModeStore.setMode('parametric');
    mocks.capabilities.engines = [
      { name: 'metal', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'bempp', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'dryrun', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
    ];
    mocks.capabilities.engineSelection = {
      default: 'auto', resolvedDefault: 'metal',
      full3dOrder: ['metal', 'bempp', 'dryrun'], axisymmetricRunner: 'axisym',
    };
    mocks.solvePlan = {
      engine: 'metal', formulation: 'full-3d',
      reason: "explicit solver_mode='full_3d'", eligibility_reasons: [],
    };
    mocks.solvePlanError = null;
    mocks.solvePlanPending = false;
    mocks.planSolveDesign.mockResolvedValue(mocks.solvePlan);
    mocks.createSetupRevision.mockResolvedValue({ revisionId: 'wgs_manual', contentSha256: 'sha256:setup', createdAt: 'now' });
    mocks.createCadOperation.mockImplementation(async ({ operationId }: { operationId: string }) => operation(operationId));
    mocks.prepareCadOperation.mockImplementation(async (operationId: string) => operation(operationId, 'processing'));
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

  it('blocks forced Axisymmetric mode when the advertised runner is unavailable', async () => {
    mocks.solvePlan = null;
    mocks.solvePlanError = 'Forced Axisymmetric mode requires the advertised axisym runner, but it is unavailable.';
    useSolveOptionsStore.setState({ engine: 'auto', solverMode: 'circsym' });
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });

    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(true);
    expect(solve.title).toContain('requires the advertised axisym runner');
    expect(solve.title).not.toContain('AUTO (metal)');
  });

  it('blocks invalid dry-run and stale engine selections in forced Axisymmetric mode', async () => {
    mocks.solvePlan = null;
    mocks.solvePlanError = 'Dry-run cannot run forced Axisymmetric solver mode.';
    useSolveOptionsStore.setState({ engine: 'dryrun', solverMode: 'circsym' });
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(true);
    expect(solve.title).toContain('Dry-run cannot run forced Axisymmetric');

    mocks.solvePlanError = 'Unknown solve engine: stale-engine';
    useSolveOptionsStore.setState({ engine: 'stale-engine' });
    await act(async () => { await Promise.resolve(); });
    expect(solve.disabled).toBe(true);
    expect(solve.title).toContain('Unknown solve engine');
  });

  it('allows explicit Axisymmetric planning when the saved Full 3D backend is offline', async () => {
    mocks.capabilities.engines = [
      { name: 'beat', available: false, reason: 'GPU backend is offline', version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'axisym', available: true, reason: null, version: '1', fast_paths: [], formulations: ['axisymmetric'] },
    ];
    mocks.capabilities.engineSelection = {
      default: 'auto', resolvedDefault: null,
      full3dOrder: ['beat'], axisymmetricRunner: 'axisym',
    };
    mocks.solvePlan = {
      engine: 'axisym', formulation: 'axisymmetric',
      reason: "forced by solver_mode='circsym'",
      eligibility_reasons: [],
    };
    mocks.planSolveDesign.mockResolvedValue(mocks.solvePlan);
    useSolveOptionsStore.setState({ engine: 'beat', solverMode: 'circsym' });
    mocks.submitDesign.mockResolvedValue('axisym-job');
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });

    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(false);
    expect(solve.title).toBe('Solve current design with AXISYM (requested BEAT full-3D fallback)');
    await act(async () => {
      solve.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(mocks.submitDesign).toHaveBeenCalledOnce();

    await act(async () => {
      mocks.solvePlan = null;
      mocks.solvePlanError = 'GPU backend is offline';
      useSolveOptionsStore.setState({ solverMode: 'full_3d' });
      await Promise.resolve();
    });
    expect(solve.disabled).toBe(true);
    expect(solve.title).toBe('GPU backend is offline');
  });

  it('blocks an ineligible design when its explicit full-3D fallback is offline', async () => {
    mocks.capabilities.engines = [
      { name: 'beat', available: false, reason: 'GPU backend is offline', version: null, fast_paths: [], formulations: ['full-3d'] },
      { name: 'axisym', available: true, reason: null, version: '1', fast_paths: [], formulations: ['axisymmetric'] },
    ];
    mocks.solvePlan = null;
    mocks.solvePlanError = "Solve engine 'beat' is unavailable. GPU backend is offline";
    useSolveOptionsStore.setState({ engine: 'beat', solverMode: 'auto' });
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
      await expect(jobsCoordinatorBridge.getSnapshot().run(designForFamily('FREEFORM')))
        .rejects.toThrow("Solve engine 'beat' is unavailable. GPU backend is offline");
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

  it('names Axisym when the formulation is selected explicitly', async () => {
    mocks.solvePlan = {
      engine: 'axisym', formulation: 'axisymmetric',
      reason: "forced by solver_mode='circsym'",
      eligibility_reasons: [],
    };
    useSolveOptionsStore.setState({ engine: 'auto', solverMode: 'circsym' });
    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });

    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(false);
    expect(solve.title).toBe('Solve current design with AUTO (AXISYM)');
  });

  // A result picked by hand pins the primary slot, and pinning outlived the
  // solve that came after it: every later run finished into a rail that still
  // showed the old one. Pressing Solve is a request to see that solve, so the
  // submission claims the slot for its own run; shell/ResultsPanel hands it
  // over once that run has results.
  it('claims the primary slot for the run it submits', async () => {
    mocks.submitDesign.mockResolvedValue('fresh-run');
    compareSelection.setPrimary('pinned-run');

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().run(designForFamily('OSSE')); });

    expect(compareSelection.getSnapshot()).toMatchObject({
      primary: 'pinned-run', following: false, awaiting: 'fresh-run',
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

  // Choosing "Bare shell" and pressing Solve used to flip the Outer body
  // control straight back to "Thickened waveguide (freestanding)": the browser
  // wrote BEMPP's 5 mm closed-wall default into the live document before
  // submitting. The server applies that default to the run's own copy
  // (server/jobs/runtime.py `_apply_bempp_wall_default`), so pressing Solve
  // must leave the design exactly as the user left it -- on every engine.
  it.each(['bempp', 'metal', 'auto'])('does not edit the design when solving a bare shell on %s', async (engine) => {
    mocks.submitDesign.mockResolvedValue('job');
    const design = designForFamily('OSSE');
    design.mesh.wall_thickness = 0;
    design.enclosure.depth = 0;
    design.simulation.sim_type = 'freestanding';
    act(() => {
      useDesignStore.getState().loadDesign(design);
      useSolveOptionsStore.getState().setEngine(engine);
    });
    const revision = useDesignStore.getState().designRevision;

    await act(async () => {
      const state = useDesignStore.getState();
      await jobsCoordinatorBridge.getSnapshot().run(state.design, state.designRevision);
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

  // Imported geometry takes the same engine choice as a parametric design: the
  // user's choice goes to the server as it is, and the server resolves AUTO or
  // refuses an engine that cannot, with the reason. Neither a Metal check nor a
  // forced engine may stand in for that choice here. The formulation is not a
  // choice for imported geometry, so it is always full 3-D.
  it("sends an imported solve with the user's engine choice, in full 3-D", async () => {
    mocks.submitImported.mockResolvedValue('job-cad');
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_metal'))).resolves.toBe('job-cad');
    });

    // No Metal at all on this machine: the request still goes to the server,
    // with the engine the user chose.
    mocks.capabilities.engines = [
      { name: 'bempp', available: true, reason: null, version: null, fast_paths: [], formulations: ['full-3d'] },
    ];
    await act(async () => { root.render(<JobsCoordinator now={() => new Date(2026, 7, 12, 12)}><span>ready</span></JobsCoordinator>); });
    const cpu = importedSubmission('wgi_no_metal');
    cpu.options = { ...cpu.options, engine: 'beat-cpu', solver_mode: 'circsym' };
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().runImported(cpu)).resolves.toBe('job-cad');
    });

    expect(mocks.submitImported).toHaveBeenCalledTimes(2);
    const sent = mocks.submitImported.mock.calls.map(([submission]) => (submission as ImportedSolveSubmission).options);
    expect(sent[0]).toMatchObject({ engine: 'metal', solver_mode: 'full_3d', symmetry: 'auto' });
    expect(sent[1]).toMatchObject({ engine: 'beat-cpu', solver_mode: 'full_3d', symmetry: 'auto' });
  });

  // "No engine here can take this geometry" is a capability this machine
  // lacks, which callers must handle differently from a refusal of the
  // request itself -- so it arrives as its own type.
  it('turns an engine_unavailable refusal into a typed capability error', async () => {
    const message = 'No solve engine on this host that can solve imported geometry is available.';
    mocks.submitImported.mockRejectedValue(new SolveSubmissionRefused(message, 503, 'engine_unavailable'));
    let caught: unknown;
    await act(async () => {
      caught = await jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_capability'))
        .catch((error: unknown) => error);
    });
    expect(caught).toBeInstanceOf(SolveEngineUnavailableError);
    expect((caught as Error).message).toBe(message);
  });

  // An unsupported engine answers a request that named one -- a verdict on
  // that request, not a missing capability -- so it stays an ordinary error.
  it.each([
    ['fem_required', 'fem_required: this return includes FEM air volumes'],
    ['imported_engine_unsupported', "imported_engine_unsupported: engine 'bempp' does not declare imported geometry; engines that do: metal"],
  ])('keeps a %s refusal an ordinary error', async (code, message) => {
    mocks.submitImported.mockRejectedValue(new SolveSubmissionRefused(message, 422, code));
    let caught: unknown;
    await act(async () => {
      caught = await jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_refused'))
        .catch((error: unknown) => error);
    });
    expect(caught).toBeInstanceOf(Error);
    expect(caught).not.toBeInstanceOf(SolveEngineUnavailableError);
    expect((caught as Error).message).toBe(message);
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

  it('sends an imported solve with no CAD command key', async () => {
    // cad-solve:<id> is the backend's own submission-key namespace for
    // Fusion's commands; a solve started here must never claim one.
    useCadReturnStore.setState({
      selectedBundle: {
        name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn',
        modifiedAt: '2026-08-22T00:00:00Z', readable: true, documentName: 'Speaker',
        requestId: null, sourceCount: 0, instanceCount: 0, designIds: [], sources: [],
      },
    });
    mocks.submitImported.mockResolvedValue('job-cad');

    await act(async () => {
      await jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_command'));
    });

    expect(mocks.submitImported).toHaveBeenCalledOnce();
    expect(mocks.submitImported.mock.calls[0]).toHaveLength(3);
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

    expect(mocks.createSetupRevision).toHaveBeenCalledOnce();
    expect(mocks.createCadOperation).toHaveBeenCalledWith(expect.objectContaining({ ingestId }));
    expect(mocks.prepareCadOperation).toHaveBeenCalledWith(
      expect.any(String), { setupRevisionId: 'wgs_manual', submit: true },
    );
    expect(mocks.submitImported).not.toHaveBeenCalled();
    expect(mocks.submitDesign).not.toHaveBeenCalled();
  });

  it('labels the durable setup from the CAD document, not the open parametric design', async () => {
    readyCad('wgi_label');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); });
    expect((mocks.createSetupRevision.mock.calls[0][0] as CadSolveSetup).label).toBe('Speaker1');
    expect(useDocumentStore.getState().designName).toBe('horn');
  });

  it('selects an accepted durable job, refreshes jobs, and advances its CAD label once', async () => {
    readyCad('wgi_completed');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); });
    const operationId = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    await act(async () => {
      useCadOperationsStore.getState().apply(operation(operationId, 'accepted', { jobId: 'job-cad' }));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(compareSelection.getSnapshot().awaiting).toBe('job-cad');
    expect(jobsSocket.refresh).toHaveBeenCalled();
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'Speaker', runSequenceNext: 2 });
    await act(async () => {
      useCadOperationsStore.getState().apply(operation(operationId, 'accepted', {
        jobId: 'job-cad', updatedAt: '2026-09-15T10:00:01Z',
      }));
      await Promise.resolve();
    });
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'Speaker', runSequenceNext: 2 });
  });

  // What the selector holds is what a CAD solve sends: the user's pick as they
  // left it -- AUTO stays AUTO for the server to resolve -- and never an engine
  // the browser chose on their behalf.
  it.each(['beat-cpu', 'auto'])('submits the engine selected in the solver selector (%s) for a CAD solve', async (engine) => {
    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    readyCad(ingestId);
    act(() => useSolveOptionsStore.getState().setEngine(engine));
    act(() => useSolveOptionsStore.getState().setSolverMode('circsym'));

    await act(async () => {
      root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>);
    });
    act(() => workspaceModeStore.setMode('cad'));
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.textContent).toBe('Solve');
    await act(async () => { solve.click(); await Promise.resolve(); await Promise.resolve(); });

    const setup = mocks.createSetupRevision.mock.calls[0][0] as CadSolveSetup;
    expect(setup.options.engine).toBe(engine);
    expect(setup.options.solver_mode).toBe('full_3d');
    expect(mocks.submitImported).not.toHaveBeenCalled();
    // Submitting leaves the selection alone.
    expect(useSolveOptionsStore.getState().engine).toBe(engine);
  });

  it('binds the same request fields as the old imported submission builder', async () => {
    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    readyCad(ingestId);
    act(() => workspaceModeStore.setMode('cad'));
    const old = (await import('../jobs/importedSubmission')).buildImportedSubmission(useCadReturnStore.getState());
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); });
    const setup = mocks.createSetupRevision.mock.calls[0][0] as CadSolveSetup;
    const {
      type: _type, ingest_id: _ingest, manifest_sha256: _manifest,
      artifact_sha256: _artifact, acknowledged_findings: _findings, ...oldGeometry
    } = old.geometry;
    expect(setup.geometry).toEqual(oldGeometry);
    expect(setup.options).toEqual(old.options);
  });

  it('gates solveCurrentCadImport on readiness and reports a busy solve instead of dropping it', async () => {
    // Automatic callers (Pull & Solve, a Fusion solve command) use this action,
    // so its refusal has to be a thrown reason and never a silent no-op.
    await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport())
      .rejects.toThrow('Ingest a CAD return before solving.');
    expect(mocks.submitImported).not.toHaveBeenCalled();

    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    readyCad(ingestId);
    const pending = deferred<CadOperationSummary>();
    mocks.prepareCadOperation.mockReturnValue(pending.promise);

    let first!: Promise<'submitted' | 'busy'>;
    let second!: 'submitted' | 'busy';
    await act(async () => {
      first = jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
      second = await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport();
    });
    expect(second).toBe('busy');
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.submitImported).not.toHaveBeenCalled();
    await act(async () => { pending.resolve(operation('manual', 'processing')); await expect(first).resolves.toBe('submitted'); });
  });

  it('reuses the manual operation id when a click is retried after prepare fails', async () => {
    readyCad('wgi_retry');
    mocks.prepareCadOperation.mockRejectedValueOnce(new Error('temporary prepare failure'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).rejects.toThrow('temporary prepare failure');
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
    });
    expect(mocks.createCadOperation).toHaveBeenCalledTimes(2);
    expect(mocks.createCadOperation.mock.calls[0][0].operationId)
      .toBe(mocks.createCadOperation.mock.calls[1][0].operationId);
    expect(mocks.submitImported).not.toHaveBeenCalled();
  });

  it('rotates the operation id after the authoritative row is terminal', async () => {
    readyCad('wgi_repeat');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
    });
    const first = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    mocks.getCadOperation.mockResolvedValueOnce(operation(first, 'accepted', { jobId: 'job-first' }));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
    });
    expect(mocks.createCadOperation.mock.calls[1][0].operationId).not.toBe(first);
    expect((mocks.createSetupRevision.mock.calls[1][0] as CadSolveSetup).label).toBe('Speaker2');
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'Speaker', runSequenceNext: 2 });
  });

  it('does not rotate when a lost prepare response already became terminal', async () => {
    readyCad('wgi_lost_prepare');
    mocks.prepareCadOperation.mockRejectedValueOnce(new Error('connection closed'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).rejects.toThrow('connection closed');
    });
    const first = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    mocks.getCadOperation.mockResolvedValueOnce(operation(first, 'accepted'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
    });
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.prepareCadOperation).toHaveBeenCalledOnce();
    mocks.getCadOperation.mockResolvedValueOnce(operation(first, 'accepted'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
    });
    expect(mocks.createCadOperation).toHaveBeenCalledTimes(2);
    expect(mocks.createCadOperation.mock.calls[1][0].operationId).not.toBe(first);
  });

  it('advances a lost-response solve label once when terminal recovery finds it accepted', async () => {
    readyCad('wgi_lost_label');
    act(() => workspaceModeStore.setMode('cad'));
    mocks.prepareCadOperation.mockRejectedValueOnce(new Error('connection closed'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).rejects.toThrow('connection closed');
    });
    const operationId = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    mocks.getCadOperation.mockResolvedValueOnce(operation(operationId, 'accepted', { jobId: 'job-lost' }));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).resolves.toBe('submitted');
      await Promise.resolve();
    });
    expect(preferencesStore.getSnapshot()).toMatchObject({ runSequenceName: 'Speaker', runSequenceNext: 2 });
    expect(compareSelection.getSnapshot().awaiting).toBe('job-lost');
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
  });

  it.each(['rejected', 'cancelled'])('surfaces a recovered %s operation instead of reporting submitted', async (state) => {
    readyCad(`wgi_${state}`);
    mocks.prepareCadOperation.mockRejectedValueOnce(new Error('connection closed'));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport()).rejects.toThrow('connection closed');
    });
    const operationId = mocks.createCadOperation.mock.calls[0][0].operationId as string;
    mocks.getCadOperation.mockResolvedValueOnce(operation(operationId, state, { message: `${state} by backend` }));
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport())
        .rejects.toThrow(`${state} by backend`);
    });
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
  });

  it.each([
    ['setup revision', 'createSetupRevision'],
    ['operation lookup', 'getCadOperation'],
    ['operation creation', 'createCadOperation'],
    ['operation preparation', 'prepareCadOperation'],
  ] as const)('shows a 409 from %s instead of swallowing it', async (_label, route) => {
    readyCad(`wgi_${route}`);
    act(() => workspaceModeStore.setMode('cad'));
    const refusal = new Error(`${route} refused with 409`);
    mocks[route].mockRejectedValueOnce(refusal);
    await act(async () => { root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>); });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('button')!.click();
      await Promise.resolve(); await Promise.resolve();
    });
    expect(jobsCoordinatorBridge.getSnapshot().actionError).toBe(refusal.message);
    expect(mocks.submitImported).not.toHaveBeenCalled();
  });

  it('keeps a standalone msh import inspection-only', async () => {
    importedMeshStore.setFile({ name: 'inspection.msh', source: 'file' } as ImportedMeshScene);
    await act(async () => { root.render(<JobsCoordinator><MainSolveButton/></JobsCoordinator>); });
    const solve = host.querySelector<HTMLButtonElement>('button')!;
    expect(solve.disabled).toBe(true);
    expect(solve.title).toContain('viewport-only');
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.submitImported).not.toHaveBeenCalled();
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
    vi.spyOn(cadLinkCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...cadLinkCoordinatorBridge.getSnapshot(),
      pullAndSolve,
      pullFromFusion,
      fusionStatus: {
        running: true, state: 'stale', fusionChangesAvailable,
        observationFreshness: 'current', documentChangeDetectable: true,
      } as never,
    });
    readyCad('wgi_displayed');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    const buttons = [...host.querySelectorAll<HTMLButtonElement>('.solve-button')];
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toContain('Solve');
    expect(buttons[0].className).toBe('solve-button');
    expect(host.textContent).not.toContain('Pull from Fusion & Solve');

    await act(async () => { buttons[0].click(); await Promise.resolve(); await Promise.resolve(); });
    expect(pullAndSolve).not.toHaveBeenCalled();
    expect(pullFromFusion).not.toHaveBeenCalled();
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.createCadOperation).toHaveBeenCalledWith(expect.objectContaining({ ingestId: 'wgi_displayed' }));
    expect(mocks.submitImported).not.toHaveBeenCalled();
  });

  it('gives the keyboard shortcut the same meaning as the button while Fusion reports newer geometry', async () => {
    const pullAndSolve = vi.fn(async () => 'solving' as const);
    vi.spyOn(cadLinkCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...cadLinkCoordinatorBridge.getSnapshot(),
      pullAndSolve,
      fusionStatus: { running: true, state: 'stale', fusionChangesAvailable: true, observationFreshness: 'current', documentChangeDetectable: true } as never,
    });
    readyCad('wgi_displayed');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', metaKey: true, bubbles: true }));
      await Promise.resolve(); await Promise.resolve();
    });
    expect(pullAndSolve).not.toHaveBeenCalled();
    expect(mocks.createCadOperation).toHaveBeenCalledOnce();
    expect(mocks.createCadOperation).toHaveBeenCalledWith(expect.objectContaining({ ingestId: 'wgi_displayed' }));
  });

  it('offers the Fusion refresh as its own action on the source line, which never solves', async () => {
    const pullFromFusion = vi.fn(async () => ({}) as never);
    vi.spyOn(cadLinkCoordinatorBridge, 'getSnapshot').mockReturnValue({
      ...cadLinkCoordinatorBridge.getSnapshot(),
      pullFromFusion,
      fusionStatus: { running: true, state: 'stale', fusionChangesAvailable: true, observationFreshness: 'current', documentChangeDetectable: true } as never,
    });
    readyCad('wgi_displayed');
    act(() => workspaceModeStore.setMode('cad'));
    await act(async () => { root.render(<JobsCoordinator><SolveActions/></JobsCoordinator>); });

    const line = host.querySelector('.cad-source-line')!;
    expect(line.textContent).toContain('Model loaded from Fusion · Newer CAD changes available');
    const refresh = [...line.querySelectorAll('button')].find((button) => button.textContent === 'Refresh')!;
    await act(async () => { refresh.click(); await Promise.resolve(); });
    expect(pullFromFusion).toHaveBeenCalledOnce();
    expect(mocks.createCadOperation).not.toHaveBeenCalled();
    expect(mocks.prepareCadOperation).not.toHaveBeenCalled();
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
    act(() => useSolveOptionsStore.getState().updatePolar(invalid));
    expect(solve.disabled).toBe(true);
    expect(solve.title).toMatch(/Directivity|directivity/);

    act(() => workspaceModeStore.setMode('cad'));
    expect(solve.disabled).toBe(true);
    expect(solve.title).toMatch(/Directivity|directivity/);
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
