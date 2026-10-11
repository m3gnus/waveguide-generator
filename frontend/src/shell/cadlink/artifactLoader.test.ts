import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { jobsSocket } from '../../api/jobsSocket';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { importedMeshStore } from '../../viewport/importedMeshStore';
import { showIngestedMeshInViewport, showIngestedSolverMeshInViewport } from './artifactLoader';
import { bundle, deferred, mesh, record } from './selection.fixtures';

let ready: (ingestId: string) => void;
let unsubscribe = vi.fn<() => void>();
beforeEach(() => {
  vi.useFakeTimers(); localStorage.clear(); resetCadReturnStore(); importedMeshStore.clear();
  workspaceModeStore.setMode('cad');
  const state = useCadReturnStore.getState(); state.selectBundle(bundle);
  state.applyIngest(record, state.beginIngestIntent());
  unsubscribe = vi.fn();
  vi.spyOn(jobsSocket, 'subscribeCadViewportReady').mockImplementation((listener) => {
    ready = listener; return unsubscribe;
  });
});
afterEach(async () => {
  importedMeshStore.clear(); resetCadReturnStore();
  await vi.runAllTimersAsync(); vi.useRealTimers(); vi.restoreAllMocks();
});
const displayMesh = mesh.replace('2 1 0 0', '2 2 0 0');

describe('ingest-keyed artifact loading without the coordinator', () => {
  it('shows the solver artifact immediately and recovers a missed readiness event by a bounded re-read', async () => {
    let reads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/viewport-mesh')) return ++reads <= 2
        ? new Response('', { status: 202 }) : new Response(displayMesh);
      return new Response(mesh);
    });
    await showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(reads).toBe(2);
    await vi.advanceTimersByTimeAsync(20_000);
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(2);
    expect(reads).toBe(3); expect(unsubscribe).toHaveBeenCalledOnce();
  });

  it('closes the readiness-before-subscription race with an immediate re-read', async () => {
    let reads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/viewport-mesh')
      ? ++reads === 1 ? new Response('', { status: 202 }) : new Response(displayMesh)
      : new Response(mesh));
    await showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    await vi.advanceTimersByTimeAsync(0);
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(2);
    expect(reads).toBe(2);
  });

  it('bounds recovery reads when the display artifact never becomes ready', async () => {
    let reads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).endsWith('/viewport-mesh')) { reads++; return new Response('', { status: 202 }); }
      return new Response(mesh);
    });
    await showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    await vi.runAllTimersAsync();
    expect(reads).toBe(5); expect(unsubscribe).toHaveBeenCalledOnce();
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(1);
  });

  it('wakes for its own ingest readiness event', async () => {
    let available = false;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/viewport-mesh')
      ? available ? new Response(displayMesh) : new Response('', { status: 202 }) : new Response(mesh));
    await showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    await vi.advanceTimersByTimeAsync(0);
    available = true; ready('another-ingest'); await vi.advanceTimersByTimeAsync(0);
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(1);
    ready(record.ingest_id); await vi.advanceTimersByTimeAsync(0);
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(2);
  });

  it('never lets an old display completion cancel a newer model load that ignores abort', async () => {
    const oldDisplay = deferred<Response>(); const newDisplay = deferred<Response>();
    let reads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      if (path.includes('wgi_new')) return newDisplay.promise;
      if (path.endsWith('/viewport-mesh')) return ++reads === 1 ? new Response('', { status: 202 }) : oldDisplay.promise;
      return new Response(mesh);
    });
    await showIngestedMeshInViewport(record, 'Old', undefined, fetcher);
    const newRecord = { ...record, ingest_id: 'wgi_new' };
    const state = useCadReturnStore.getState();
    state.selectBundle({ ...bundle, bundlePath: 'new.wgreturn' });
    state.applyIngest(newRecord, state.beginIngestIntent());
    const pending = showIngestedMeshInViewport(newRecord, 'New', undefined, fetcher);
    oldDisplay.resolve(new Response(displayMesh)); await vi.advanceTimersByTimeAsync(0);
    newDisplay.resolve(new Response(mesh)); await pending;
    expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe('wgi_new');
    expect(importedMeshStore.getSnapshot().cad?.name).toBe('New');
  });

  it('upgrades the model without cancelling or stealing an in-flight solver view for the same ingest', async () => {
    const display = deferred<Response>(); const solver = deferred<Response>();
    let displayReads = 0; let solverReads = 0;
    const fetcher = vi.fn(async (input: RequestInfo | URL) => String(input).endsWith('/viewport-mesh')
      ? ++displayReads === 1 ? new Response('', { status: 202 }) : display.promise
      : ++solverReads === 1 ? new Response(mesh) : solver.promise);
    await showIngestedMeshInViewport(record, 'Speaker', undefined, fetcher);
    importedMeshStore.showCadSolver();
    const pending = showIngestedSolverMeshInViewport(record, 'Speaker', fetcher);
    display.resolve(new Response(displayMesh)); await vi.advanceTimersByTimeAsync(0);
    expect(importedMeshStore.getSnapshot().showing).toBe('cadSolver');
    solver.resolve(new Response(mesh)); expect(await pending).toBeNull();
    expect(importedMeshStore.getSnapshot().cadSolver?.ingestId).toBe(record.ingest_id);
    expect(importedMeshStore.getSnapshot().cad?.scene.bounds.max.x).toBe(2);
    expect(importedMeshStore.getSnapshot().cadSolver?.scene.bounds.max.x).toBe(1);
  });

  it.each(['success', 'error'] as const)('drops an initial mesh %s after a direct store selection', async (outcome) => {
    const pending = deferred<Response>(); const notice = vi.fn();
    const fetcher = vi.fn(() => pending.promise) as typeof fetch;
    const first = showIngestedMeshInViewport(record, 'Old', notice, fetcher);
    useCadReturnStore.getState().selectBundle({ ...bundle, bundlePath: 'new.wgreturn' });
    pending.resolve(outcome === 'success' ? new Response(mesh) : new Response('', { status: 409 }));
    await first;
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
    expect(notice).not.toHaveBeenCalled(); expect(fetcher).toHaveBeenCalledOnce();
  });

  it('fetches only the verified solve triangles into its own slot and caches by ingest', async () => {
    const fetcher = vi.fn(async () => new Response(mesh));
    importedMeshStore.showCadSolver();
    await showIngestedSolverMeshInViewport(record, 'Speaker', fetcher);
    const scene = importedMeshStore.getSnapshot().cadSolver;
    expect(scene?.scene.bounds.max.x).toBe(1);
    expect(importedMeshStore.getSnapshot().cad).toBeNull();
    await showIngestedSolverMeshInViewport(record, 'Speaker', fetcher);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith(`/api/cadlink/ingest/${record.ingest_id}/mesh`);
    expect(importedMeshStore.getSnapshot().cadSolver).toBe(scene);
  });

  it('refuses a solver artifact that fails verification', async () => {
    expect(await showIngestedSolverMeshInViewport(record, 'Speaker',
      (async () => new Response('', { status: 409 })) as typeof fetch)).toContain('failed verification');
    expect(importedMeshStore.getSnapshot().cadSolver).toBeNull();
  });

  it('suppresses a stale solver fetch rejection after newer viewport intent', async () => {
    const pending = deferred<Response>();
    const first = showIngestedSolverMeshInViewport(record, 'Old', (() => pending.promise) as typeof fetch);
    importedMeshStore.showParametric(); pending.reject(new Error('old network error'));
    expect(await first).toBeNull();
    expect(importedMeshStore.getSnapshot().cadSolver).toBeNull();
  });
});
