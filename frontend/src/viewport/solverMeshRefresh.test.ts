import { mesh } from '../shell/cadlink/selection.fixtures';
import { commitAssembly, emptyAssembly } from '../stores/assembly';
import { designModelCurrent, prepareDesignModel, requestAssemblyBuild, resetAssemblyBuild } from '../stores/assemblyBuild';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { designPreparedRecord } from '../stores/importedModel';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { importedMeshStore } from './importedMeshStore';
import { solverMeshOptions } from '../api/solverMesh';
import { toSolveDesign } from '../jobs/actions';
import { designFingerprint } from '../results/runCoherence';
import { currentModel, currentModelScene } from '../model/currentModel';
import { workspaceModeStore } from '../stores/workspaceMode';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  prepareDesignSolverMesh, SolverMeshRefreshController,
  SOLVER_MESH_AUTO_BUDGET_MS,
  SOLVER_MESH_DEBOUNCE_MS,
  type SolverMeshBuildOutcome,
  type SolverMeshRefreshState,
} from './solverMeshRefresh';

interface Harness {
  controller: SolverMeshRefreshController;
  builds: Array<(outcome: SolverMeshBuildOutcome) => void>;
  signals: AbortSignal[];
  states: SolverMeshRefreshState[];
  solveActive: { value: boolean };
}

function harness(): Harness {
  const builds: Array<(outcome: SolverMeshBuildOutcome) => void> = [];
  const signals: AbortSignal[] = [];
  const states: SolverMeshRefreshState[] = [];
  const solveActive = { value: false };
  const controller = new SolverMeshRefreshController({
    runBuild: (signal) => {
      signals.push(signal);
      return new Promise((resolve) => builds.push(resolve));
    },
    isSolveActive: () => solveActive.value,
    onState: (state) => states.push(state),
  });
  return { controller, builds, signals, states, solveActive };
}

async function flush(): Promise<void> {
  // Resolve the promise chain queued by a completed build.
  await Promise.resolve();
  await Promise.resolve();
}

const ok = (durationMs = 100): SolverMeshBuildOutcome => {
  const owner = { producer: 'horn', key: 'test-request' } as const;
  importedMeshStore.ensureSolver(owner, () => ({ source: 'solver' } as never));
  return { ok: true, durationMs, owner };
};

describe('SolverMeshRefreshController', () => {
  beforeEach(() => { vi.useFakeTimers(); importedMeshStore.clear(); });
  afterEach(() => { vi.useRealTimers(); importedMeshStore.clear(); });

  it('builds immediately on activation and settles fresh', async () => {
    const h = harness();
    h.controller.activate(1);
    expect(h.builds).toHaveLength(1);
    expect(h.controller.getState().building).toBe(true);

    h.builds[0](ok());
    await flush();
    expect(h.controller.getState()).toEqual({ building: false, stale: false, staleReason: null, error: null });
  });

  it('skips the build when the mesh already matches the design revision', async () => {
    const h = harness();
    h.controller.activate(1);
    h.builds[0](ok());
    await flush();

    h.controller.deactivate();
    h.controller.activate(1);
    expect(h.builds).toHaveLength(1);
  });

  it('debounces design edits and restarts the timer on each edit', async () => {
    const h = harness();
    h.controller.activate(1);
    h.builds[0](ok());
    await flush();

    h.controller.designChanged(2);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS - 100);
    expect(h.builds).toHaveLength(1);
    h.controller.designChanged(3);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS - 100);
    expect(h.builds).toHaveLength(1);
    vi.advanceTimersByTime(100);
    expect(h.builds).toHaveLength(2);
  });

  it('coalesces edits during a build into one follow-up build', async () => {
    const h = harness();
    h.controller.activate(1);
    expect(h.builds).toHaveLength(1);

    h.controller.designChanged(2);
    h.controller.designChanged(3);
    h.controller.designChanged(4);
    expect(h.builds).toHaveLength(1);

    h.builds[0](ok());
    await flush();
    // The follow-up starts immediately: latest design wins, one in flight.
    expect(h.builds).toHaveLength(2);
    h.builds[1](ok());
    await flush();
    expect(h.builds).toHaveLength(2);
    expect(h.controller.getState().stale).toBe(false);
  });

  it('raises the banner instead of auto-building after a slow build', async () => {
    const h = harness();
    h.controller.activate(1);
    h.builds[0](ok(SOLVER_MESH_AUTO_BUDGET_MS + 1));
    await flush();
    expect(h.controller.getState().stale).toBe(false);

    h.controller.designChanged(2);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS * 4);
    expect(h.builds).toHaveLength(1);
    expect(h.controller.getState()).toMatchObject({ stale: true, staleReason: 'build-slow' });

    // Manual refresh is always allowed and re-measures the budget.
    h.controller.refresh();
    expect(h.builds).toHaveLength(2);
    h.builds[1](ok(200));
    await flush();
    expect(h.controller.getState().stale).toBe(false);

    h.controller.designChanged(3);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS);
    expect(h.builds).toHaveLength(3);
  });

  it('automatically rebuilds after a failed build followed by an option fix', async () => {
    const h = harness();
    h.controller.activate(1);
    h.builds[0]({ ok: false, durationMs: SOLVER_MESH_AUTO_BUDGET_MS + 1, error: 'mesher refused' });
    await flush();
    expect(h.controller.getState()).toMatchObject({
      stale: true, staleReason: 'build-failed', error: 'mesher refused',
    });

    h.controller.designChanged(2);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS * 4);
    expect(h.builds).toHaveLength(2);
    h.builds[1](ok());
    await flush();
    expect(h.controller.getState()).toEqual({ building: false, stale: false, staleReason: null, error: null });
  });

  it('rebuilds the fix that arrived while a failing build was in flight', async () => {
    const h = harness();
    h.controller.activate(1);
    h.controller.designChanged(2);
    h.builds[0]({ ok: false, durationMs: 50, error: 'old option refused' });
    await flush();
    expect(h.builds).toHaveLength(2);
    h.builds[1]({ ok: false, durationMs: 50, error: 'still invalid' });
    await flush();
    expect(h.builds).toHaveLength(2);
  });

  it('withholds builds behind the banner while a solve is running, then resumes', async () => {
    const h = harness();
    h.solveActive.value = true;
    h.controller.activate(1);
    expect(h.builds).toHaveLength(0);
    expect(h.controller.getState()).toMatchObject({ stale: true, staleReason: 'solve-running' });

    h.controller.designChanged(2);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS * 4);
    expect(h.builds).toHaveLength(0);

    h.solveActive.value = false;
    h.controller.solveSettled();
    expect(h.builds).toHaveLength(1);
    h.builds[0](ok());
    await flush();
    expect(h.controller.getState().stale).toBe(false);
  });

  it('aborts the in-flight request and goes quiet on deactivation', async () => {
    const h = harness();
    h.controller.activate(1);
    expect(h.signals[0].aborted).toBe(false);

    h.controller.deactivate();
    expect(h.signals[0].aborted).toBe(true);

    // A late resolution from the aborted build must not publish new state or
    // poison the outcome history.
    const published = h.states.length;
    h.builds[0]({ ok: false, durationMs: 10, error: 'aborted' });
    await flush();
    expect(h.states.length).toBe(published);

    h.controller.activate(2);
    expect(h.builds).toHaveLength(2);
    h.builds[1](ok());
    await flush();
    expect(h.controller.getState().stale).toBe(false);
  });

  it('ignores edits and refreshes while inactive', () => {
    const h = harness();
    h.controller.designChanged(5);
    h.controller.refresh();
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS * 4);
    expect(h.builds).toHaveLength(0);
  });

  it('rebuilds after a document load that reuses the built revision number', async () => {
    const h = harness();
    h.controller.activate(2);
    h.builds[0](ok());
    await flush();
    expect(h.controller.getState().stale).toBe(false);

    // A restored document can come back under the very revision already built.
    h.controller.documentLoaded();
    h.controller.designChanged(2);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS);
    expect(h.builds).toHaveLength(2);

    // And re-activating the view must not serve the old document's mesh.
    h.builds[1](ok());
    await flush();
    h.controller.deactivate();
    h.controller.documentLoaded();
    h.controller.activate(2);
    expect(h.builds).toHaveLength(3);
  });
});

describe('design solver mesh preparation', () => {
  beforeEach(() => {
    resetDesignStore();
    resetAssemblyBuild();
    resetSolveOptionsStore();
    importedMeshStore.clear();
    importedMeshStore.showSolver();
    workspaceModeStore.setMode('parametric');
  });
  afterEach(() => vi.restoreAllMocks());

  const hornResult = () => ({ msh_text: mesh, cut_planes: [],
    stats: { triangle_count: 1, mesh_cache_key: 'horn-artifact' }, plan: { refusal: 'Choose another engine.' } });
  const assemblyResult = () => ({ status: 'published', reused: false,
    model: { model_id: 'design-assembly:x', producer: 'design-assembly', display_name: 'x' },
    prepared: { ingest_id: 'wgi_build', producer: 'design-assembly', sources: [], source_tags: {}, msh_text: mesh } });
  const prepare = (controller = new AbortController(), onRefusal = vi.fn()) => prepareDesignSolverMesh({
    signal: controller.signal, generation: importedMeshStore.captureGeneration(), onRefusal,
  });

  it('dispatches the horn producer with the same request and retains an unchanged artifact', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(hornResult())));
    const refusal = vi.fn();
    const generation = importedMeshStore.captureGeneration();
    expect(await prepare(new AbortController(), refusal)).toMatchObject({ ok: true });
    expect(fetcher).toHaveBeenCalledWith('/api/solver-mesh', expect.objectContaining({ method: 'POST' }));
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      design: toSolveDesign(useDesignStore.getState().design), options: solverMeshOptions(useSolveOptionsStore.getState()),
    });
    expect(refusal).toHaveBeenLastCalledWith('Choose another engine.');
    const scene = importedMeshStore.getSnapshot().solver;
    expect(scene).toMatchObject({ modelKey: designFingerprint(useDesignStore.getState().design), source: 'solver' });
    fetcher.mockResolvedValue(new Response(JSON.stringify(hornResult())));
    expect(await prepare()).toMatchObject({ ok: true });
    expect(importedMeshStore.getSnapshot().solver).toBe(scene);
    expect(importedMeshStore.captureGeneration()).toBe(generation);
  });

  it('dispatches a WG build without changing viewport intent and reuses its current session', async () => {
    commitAssembly(emptyAssembly());
    const generation = importedMeshStore.captureGeneration();
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(assemblyResult())));
    expect(await prepare()).toMatchObject({ ok: true });
    expect(fetcher).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledWith('/api/models/assembly/compile', expect.anything());
    expect(designPreparedRecord()?.ingest_id).toBe('wgi_build');
    expect(importedMeshStore.getSnapshot().solver).toMatchObject({ ingestId: 'wgi_build', modelKey: 'prepared:wgi_build' });
    expect(importedMeshStore.captureGeneration()).toBe(generation);
    expect(await prepare()).toMatchObject({ ok: true });
    expect(fetcher).toHaveBeenCalledOnce();
  });

  it('replaces a horn artifact when the server returns a new mesh for the same request', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(hornResult())));
    expect(await prepare()).toMatchObject({ ok: true });
    const scene = importedMeshStore.getSnapshot().solver;
    fetcher.mockResolvedValue(new Response(JSON.stringify({ ...hornResult(),
      stats: { ...hornResult().stats, mesh_cache_key: 'new-artifact' } })));
    expect(await prepare()).toMatchObject({ ok: true });
    expect(importedMeshStore.getSnapshot().solver?.artifactToken).not.toBe(scene?.artifactToken);
  });

  const refreshController = () => new SolverMeshRefreshController({
    runBuild: signal => prepareDesignSolverMesh({ signal, generation: importedMeshStore.captureGeneration(), onRefusal: () => {} }),
    isSolveActive: () => false,
    onState: () => {},
  });

  it('rebuilds the horn after a Geometry assembly build displaced its cached Mesh scene and was undone', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async url => new Response(JSON.stringify(
      url === '/api/solver-mesh' ? hornResult() : assemblyResult(),
    )));
    const controller = refreshController();
    const hornKey = designFingerprint(useDesignStore.getState().design)!;
    controller.activate(hornKey);
    await vi.waitFor(() => expect(controller.getState().building).toBe(false));
    const horn = importedMeshStore.getSnapshot().solver;
    controller.deactivate();
    importedMeshStore.showParametric();
    commitAssembly(emptyAssembly());
    await requestAssemblyBuild();
    expect(importedMeshStore.getSnapshot().solver?.ingestId).toBe('wgi_build');
    useDesignStore.getState().undo();
    importedMeshStore.showSolver();
    expect(currentModelScene(currentModel())).toBeNull();
    controller.activate(hornKey);
    try {
      await vi.waitFor(() => expect(controller.getState().building).toBe(false));
      expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
        '/api/solver-mesh', '/api/models/assembly/compile', '/api/solver-mesh',
      ]);
      expect(currentModelScene(currentModel())).toMatchObject({ artifactToken: horn?.artifactToken, ingestId: null });
      expect(currentModel().viewportShowing).toBe(true);
      expect(controller.getState()).toMatchObject({ building: false, stale: false });
    } finally { controller.deactivate(); }
  });

  it('restores a cached assembly scene after undo, horn Mesh preparation, and redo without recompiling', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(async url => new Response(JSON.stringify(
      url === '/api/solver-mesh' ? hornResult() : assemblyResult(),
    )));
    commitAssembly(emptyAssembly());
    await requestAssemblyBuild();
    const record = designPreparedRecord();
    useDesignStore.getState().undo();
    expect(await prepare()).toMatchObject({ ok: true });
    expect(importedMeshStore.getSnapshot().solver?.ingestId).toBeNull();
    useDesignStore.getState().redo();
    expect(designModelCurrent(useDesignStore.getState().design)).toBe(true);
    const generation = importedMeshStore.captureGeneration();
    expect(await prepareDesignModel()).toMatchObject({ status: 'published', reused: true });
    expect(designPreparedRecord()).toBe(record);
    expect(currentModelScene(currentModel())).toMatchObject({ ingestId: 'wgi_build' });
    expect(currentModel().viewportShowing).toBe(true);
    expect(importedMeshStore.captureGeneration()).toBe(generation);
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/models/assembly/compile', '/api/solver-mesh']);
  });

  it.each(['horn', 'wg-build'])('reports a refusal from the %s producer', async producer => {
    if (producer === 'wg-build') commitAssembly(emptyAssembly());
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify(producer === 'wg-build'
      ? { error: { code: 'assembly_refused', message: 'Set an enclosure depth.' } }
      : { detail: 'Set an enclosure depth.' }), { status: 422 }));
    const refusal = vi.fn();
    const outcome = await prepare(new AbortController(), refusal);
    expect(outcome).toMatchObject({ ok: false, error: expect.any(String) });
    if (producer === 'wg-build') expect(outcome.error).toBe('Set an enclosure depth.');
    expect(refusal).toHaveBeenLastCalledWith(null);
    expect(importedMeshStore.getSnapshot().solver).toBeNull();
  });

  it.each(['horn', 'wg-build'])('does not report success when a %s mesh view is aborted', async producer => {
    if (producer === 'wg-build') commitAssembly(emptyAssembly());
    let resolve!: (response: Response) => void;
    const fetcher = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(done => { resolve = done; }));
    const controller = new AbortController();
    const refusal = vi.fn();
    const pending = prepare(controller, refusal);
    controller.abort();
    resolve(new Response(JSON.stringify(producer === 'wg-build' ? assemblyResult() : hornResult())));
    expect(await pending).toMatchObject({ ok: false });
    if (producer === 'horn') {
      expect(fetcher.mock.calls[0][1]?.signal).toBe(controller.signal);
      expect(importedMeshStore.getSnapshot().solver).toBeNull();
      expect(refusal).not.toHaveBeenCalled();
    } else {
      // Leaving Mesh does not cancel the shared Build/Solve preparation.
      expect(designPreparedRecord()?.ingest_id).toBe('wgi_build');
    }
  });

  it('does not start a producer for an already aborted view', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch');
    const controller = new AbortController();
    controller.abort();
    expect(await prepare(controller)).toMatchObject({ ok: false });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
