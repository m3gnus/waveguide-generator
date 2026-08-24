import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  SolverMeshRefreshController,
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

const ok = (durationMs = 100): SolverMeshBuildOutcome => ({ ok: true, durationMs });

describe('SolverMeshRefreshController', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

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

  it('raises the banner instead of auto-building after a failure', async () => {
    const h = harness();
    h.controller.activate(1);
    h.builds[0]({ ok: false, durationMs: 50, error: 'mesher refused' });
    await flush();
    expect(h.controller.getState()).toMatchObject({
      stale: true, staleReason: 'build-failed', error: 'mesher refused',
    });

    h.controller.designChanged(2);
    vi.advanceTimersByTime(SOLVER_MESH_DEBOUNCE_MS * 4);
    expect(h.builds).toHaveLength(1);
    expect(h.controller.getState()).toMatchObject({ stale: true, staleReason: 'build-failed' });

    h.controller.refresh();
    expect(h.builds).toHaveLength(2);
    h.builds[1](ok());
    await flush();
    expect(h.controller.getState()).toEqual({ building: false, stale: false, staleReason: null, error: null });
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
});
