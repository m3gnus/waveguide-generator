import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket, type JobItem, type JobsSnapshot } from '../api/jobsSocket';
import { compareSelection } from '../api/results';
import { preferencesStore } from '../prefs/preferences';
import type { CadReturnIngestRecord } from '../api/cadlink';
import type { ImportedSolveSubmission } from '../jobs/actions';
import { resetCadReturnStore, useCadReturnStore } from '../stores/cadReturn';
import { resolveOuterBodyMode } from '../design/ParamPanel';
import { designForFamily, resetDesignStore, useDesignStore } from '../stores/design';
import { resetDocumentStore, useDocumentStore } from '../stores/document';
import { parkedSolveCommandStore } from '../stores/solveCommand';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { workspaceModeStore } from '../stores/workspaceMode';
import { importedMeshStore } from '../viewport/importedMeshStore';
import type { ImportedMeshScene } from '../viewport/importedMesh';
import { cadLinkCoordinatorBridge } from './CadLinkCoordinator';
import {
  JobsCoordinator,
  jobsCoordinatorBridge,
  refreshedArchiveJob,
  useSolveControl,
} from './JobsCoordinator';
import { SolveActions } from './TopBar';
import { JobsPanel } from './JobsPanel';

const mocks = vi.hoisted(() => ({
  planSolveDesign: vi.fn(),
  submitDesign: vi.fn(),
  submitImported: vi.fn(),
  solvePlan: {
    engine: 'metal', formulation: 'full-3d' as const,
    reason: "explicit solver_mode='full_3d'", eligibility_reasons: [] as string[],
  } as { engine: string; formulation: 'axisymmetric' | 'full-3d'; reason: string; eligibility_reasons: string[] } | null,
  solvePlanError: null as string | null,
  solvePlanPending: false,
  capabilities: {
    engines: [] as Array<{ name: string; available: boolean; reason: string | null; version: string | null; fast_paths: string[]; formulations?: string[]; mountings?: string[] }>,
    engineSelection: {
      default: 'auto', resolvedDefault: 'metal' as string | null,
      full3dOrder: ['metal', 'bempp', 'dryrun'], axisymmetricRunner: 'axisym',
    },
  },
}));

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
    compareSelection.clear();
    parkedSolveCommandStore.clear();
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
    parkedSolveCommandStore.clear();
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

  it('allows AUTO formulation planning when Axisym is available but the explicit fallback is offline', async () => {
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
      reason: 'AUTO selected the eligible platform-neutral axisymmetric runner',
      eligibility_reasons: [],
    };
    mocks.planSolveDesign.mockResolvedValue(mocks.solvePlan);
    useSolveOptionsStore.setState({ engine: 'beat', solverMode: 'auto' });
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

  it('names Axisym when an eligible AUTO design bypasses the full-3D default', async () => {
    mocks.solvePlan = {
      engine: 'axisym', formulation: 'axisymmetric',
      reason: 'AUTO selected the eligible platform-neutral axisymmetric runner',
      eligibility_reasons: [],
    };
    useSolveOptionsStore.setState({ engine: 'auto', solverMode: 'auto' });
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
    // No revision bump means no unsaved-changes dot and no autosave rewrite
    // for a solve that changed nothing.
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

  it('names CAD solves from the same design name and numbers them in the same sequence', async () => {
    mocks.submitImported.mockResolvedValue('job-cad');
    const first = importedSubmission('wgi_first');

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().runImported(first); });
    act(() => useDesignStore.getState().updateField('R', 141));
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().runImported(structuredClone(first)); });
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_second')); });

    expect(mocks.submitImported.mock.calls.map((call) => call[2])).toEqual(['horn1', 'horn2', 'horn3']);
  });

  it('keys a parked CAD command submission by its command id', async () => {
    useCadReturnStore.setState({
      selectedBundle: {
        name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn',
        modifiedAt: '2026-08-22T00:00:00Z', readable: true, documentName: 'Speaker',
        requestId: null, sourceCount: 0, instanceCount: 0, designIds: [], sources: [],
      },
    });
    parkedSolveCommandStore.park({
      commandId: 'cmd-1', bundlePath: 'wgreturn/speaker.wgreturn', blockers: [],
      parkedAt: '2026-08-22T00:00:00Z',
    });
    vi.stubGlobal('fetch', vi.fn(async () => new Response(
      JSON.stringify({ state: 'accepted', cleared: true }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    )));
    mocks.submitImported.mockResolvedValue('job-cad');

    await act(async () => {
      await jobsCoordinatorBridge.getSnapshot().runImported(importedSubmission('wgi_command'));
    });

    expect(mocks.submitImported.mock.calls[0][3]).toBe('cad-solve:cmd-1');
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
  });

  it('replays the identical keyed request after acknowledgement persistence fails', async () => {
    useCadReturnStore.setState({
      selectedBundle: {
        name: 'speaker.wgreturn', bundlePath: 'wgreturn/speaker.wgreturn',
        modifiedAt: '2026-08-22T00:00:00Z', readable: true, documentName: 'Speaker',
        requestId: null, sourceCount: 0, instanceCount: 0, designIds: [], sources: [],
      },
    });
    parkedSolveCommandStore.park({
      commandId: 'cmd-retry', bundlePath: 'wgreturn/speaker.wgreturn', blockers: [],
      parkedAt: '2026-08-22T00:00:00Z',
    });
    let outcomeAttempt = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      outcomeAttempt += 1;
      return outcomeAttempt === 1
        ? new Response(JSON.stringify({ detail: 'ledger unavailable' }), {
          status: 503, headers: { 'Content-Type': 'application/json' },
        })
        : new Response(JSON.stringify({ state: 'accepted', cleared: true }), {
          status: 200, headers: { 'Content-Type': 'application/json' },
        });
    }));
    mocks.submitImported.mockResolvedValue('job-existing');
    const submission = importedSubmission('wgi_command');

    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().runImported(submission))
        .rejects.toThrow('acknowledgement failed');
    });
    expect(parkedSolveCommandStore.getSnapshot().command?.commandId).toBe('cmd-retry');
    await act(async () => {
      await expect(jobsCoordinatorBridge.getSnapshot().runImported(submission))
        .resolves.toBe('job-existing');
    });

    expect(mocks.submitImported.mock.calls.map((call) => [call[2], call[3]])).toEqual([
      ['horn1', 'cad-solve:cmd-retry'],
      ['horn1', 'cad-solve:cmd-retry'],
    ]);
    expect(parkedSolveCommandStore.getSnapshot().command).toBeNull();
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

  it('labels a CAD Link run from the Fusion document rather than the design left open behind it', async () => {
    // The parametric design is `horn` here, and it is not the geometry being
    // solved. Naming CAD runs from it is how a Fusion return used to be filed
    // under whichever `.cfg` the autosave draft restored.
    const ingestId = 'wgi_01J5A8QK3M9T2XVBH0RD7NWE6C';
    useCadReturnStore.setState({
      selectedBundle: {
        name: 'Tritonia V-req7.wgreturn', bundlePath: '/cad/Tritonia V-req7.wgreturn',
        modifiedAt: '2026-08-19T12:00:00Z', readable: true, documentName: 'Tritonia V',
        requestId: 'req7', sourceCount: 1, instanceCount: 1, sources: [],
      },
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
    });
    mocks.submitImported.mockResolvedValue('job-cad');
    act(() => workspaceModeStore.setMode('cad'));

    await act(async () => { await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); });
    await act(async () => { await jobsCoordinatorBridge.getSnapshot().solveCurrentCadImport(); });

    expect(mocks.submitImported.mock.calls.map((call) => call[2]))
      .toEqual(['Tritonia V1', 'Tritonia V2']);
    // The design keeps its own name and its own numbering.
    expect(useDocumentStore.getState().designName).toBe('horn');
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
