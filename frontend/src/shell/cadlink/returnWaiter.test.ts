import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createCadConnectionStore } from '../../stores/cadConnection';
import { resetCadReturnStore, useCadReturnStore } from '../../stores/cadReturn';
import { designForFamily, resetDesignStore, useDesignStore } from '../../stores/design';
import { resetDocumentStore, useDocumentStore } from '../../stores/document';
import { workspaceModeStore } from '../../stores/workspaceMode';
import { createCadArrivalActions, SupersededError } from './arrivals';
import { createCadReturnWaiter, FUSION_RETURN_TIMEOUT_MS } from './returnWaiter';
import { workspaceFor } from './selection.fixtures';
import type { JobItem } from '../../api/jobsSocket';
import { openImported, type ImportedDisplay } from '../../stores/importedModel';
import './restores';
/** Reopen a CAD run as history does: `openImported` of the run, shown by the CAD rail. */
const restoreCadJobModel = (run: JobItem, display: ImportedDisplay = {}, fetcher: typeof fetch = fetch) =>
  openImported({ kind: 'run', job: run, display }, fetcher).catch(() => false);

import { importedMeshStore } from '../../viewport/importedMeshStore';
import { bundle, deferred, job, json, mesh, record } from './selection.fixtures';
import { identity, link, status } from './outbound.fixtures';

const returned = { ...bundle, requestId: 'pull-1', designIds: [identity.designId] };
const waiters: ReturnType<typeof createCadReturnWaiter>[] = [];
const connections: ReturnType<typeof createCadConnectionStore>[] = [];
beforeEach(() => {
  vi.useFakeTimers(); vi.setSystemTime('2026-10-11T12:00:00Z');
  localStorage.clear(); resetDesignStore(); resetDocumentStore(); resetCadReturnStore(); importedMeshStore.clear();
  useDocumentStore.getState().setCadLink(identity, 'current'); workspaceModeStore.setMode('parametric');
});
afterEach(() => {
  waiters.forEach((waiter) => waiter.dispose()); connections.forEach((connection) => connection.invalidate());
  waiters.length = connections.length = 0; importedMeshStore.clear(); vi.useRealTimers(); vi.restoreAllMocks();
});
const flush = () => vi.advanceTimersByTimeAsync(0);
function setup() {
  let items = [] as typeof returned[];
  let outcome = { requestId: 'pull-1', state: 'received', reason: null as string | null, message: null as string | null };
  let selected = link.instanceId;
  const connection = createCadConnectionStore(); connections.push(connection);
  const read = vi.fn(async () => status({ statusObserved: false }));
  const arrival = vi.fn((item: typeof bundle) => useCadReturnStore.getState().selectArrivedBundle(item));
  const fetcher = vi.fn(async (input: RequestInfo | URL, _init?: RequestInit) => {
    if (String(input).endsWith('/request-fusion-return')) return json({ status: 'requested', requestId: 'pull-1', documentName: 'Speaker' });
    if (String(input).endsWith('/returns')) return json({ cadFolderConfigured: true, items });
    if (String(input).includes('/return-requests/')) return json(outcome);
    throw new Error(`Unexpected path ${input}`);
  });
  const waiter = createCadReturnWaiter({ connection, fetcher, readFusionStatus: read,
    selectedInstanceId: () => selected, onArrival: arrival });
  waiters.push(waiter);
  return { waiter, fetcher, arrival, connection, read,
    setItems: (next: typeof items) => { items = next; },
    setOutcome: (next: typeof outcome) => { outcome = next; },
    selectInstance: (next: string) => { selected = next; } };
}
function posts(fetcher: typeof fetch) {
  return vi.mocked(fetcher).mock.calls.filter(([input]) => String(input).endsWith('/request-fusion-return'));
}

describe('Bring in geometry without a mounted coordinator (row 25)', () => {
  it('joins repeated pulls before and after publication, with one exact file-transport target', async () => {
    const { waiter, fetcher, setItems, arrival } = setup();
    const first = waiter.pullFromFusion(); const second = waiter.pullFromFusion();
    expect(second).toBe(first); await flush(); expect(waiter.pullFromFusion()).toBe(first);
    expect(posts(fetcher)).toHaveLength(1);
    expect(JSON.parse(String(posts(fetcher)[0][1]?.body))).toEqual({ designId: identity.designId,
      documentId: 'document-1', instanceId: link.instanceId, expectedReturnStateHash: link.documentSignatureHash });
    expect(waiter.awaitedRequestIds()).toEqual(['pull-1']);
    setItems([returned]); await waiter.refresh(); expect(await first).toEqual(returned);
    await waiter.refresh(); expect(arrival).toHaveBeenCalledOnce();
    expect(waiter.awaitedRequestIds()).toEqual([]); expect(vi.getTimerCount()).toBe(0);
  });
  it('ignores newer unrelated and unreadable returns, then settles the correlated readable return exactly once', async () => {
    const { waiter, setItems, arrival } = setup(); const complete = vi.fn();
    const first = waiter.pullFromFusion().then(complete); await flush();
    setItems([{ ...returned, requestId: 'unrelated' }, { ...returned, readable: false }]);
    await waiter.refresh(); expect(complete).not.toHaveBeenCalled(); expect(arrival).not.toHaveBeenCalled();
    setItems([returned, returned]); await Promise.all([waiter.refresh(), waiter.refresh()]); await first;
    expect(complete).toHaveBeenCalledOnce(); expect(arrival).toHaveBeenCalledOnce();
  });
  it('does not treat adapter acceptance as returned geometry', async () => {
    const { waiter, setOutcome, arrival, setItems } = setup();
    const first = waiter.pullFromFusion(); await flush();
    setOutcome({ requestId: 'pull-1', state: 'accepted', reason: null, message: 'Done' });
    await waiter.refresh(); expect(waiter.getState().pullingFromFusion).toBe(true); expect(arrival).not.toHaveBeenCalled();
    setItems([returned]); await waiter.refresh(); await first;
  });
  it.each(['rejected', 'cancelled', 'failed'] as const)('reports a correlated %s without waiting for the timeout', async (state) => {
    const { waiter, setOutcome, arrival } = setup();
    const first = waiter.pullFromFusion().catch((error) => error); await flush();
    setOutcome({ requestId: 'pull-1', state, reason: 'adapter_refused', message: 'The Fusion document changed.' });
    await waiter.refresh(); expect(await first).toBeInstanceOf(Error);
    expect(waiter.getState().error).toContain('The Fusion document changed.');
    expect(arrival).not.toHaveBeenCalled(); expect(vi.getTimerCount()).toBe(0);
  });
  it('ignores a refusal for another request and gives arrived geometry precedence over refusal', async () => {
    const { waiter, setOutcome, setItems, arrival } = setup(); const first = waiter.pullFromFusion(); await flush();
    setOutcome({ requestId: 'other', state: 'rejected', reason: null, message: 'Refused' });
    await waiter.refresh(); expect(waiter.getState().pullingFromFusion).toBe(true);
    setOutcome({ requestId: 'pull-1', state: 'rejected', reason: null, message: 'Refused' });
    setItems([returned]); await waiter.refresh(); expect(await first).toEqual(returned); expect(arrival).toHaveBeenCalledOnce();
  });
  it('reports publication refusal immediately and permits retry', async () => {
    const { waiter, fetcher, setItems } = setup();
    fetcher.mockImplementationOnce(async () => json({ detail: 'The active Fusion document changed.' }, 409));
    await expect(waiter.pullFromFusion()).rejects.toThrow('document changed'); await flush();
    expect(waiter.getState().pullingFromFusion).toBe(false); expect(vi.getTimerCount()).toBe(0);
    setItems([returned]); const retry = waiter.pullFromFusion(); await flush(); await retry;
  });
  it.each(['no response', 'listing failures', 'stalled POST', 'stalled listing'] as const)('times out after 60 seconds with %s', async (failure) => {
    const { waiter, fetcher, arrival } = setup();
    const original = fetcher.getMockImplementation()!;
    if (failure !== 'no response') fetcher.mockImplementation(async (input, init) => {
      if (String(input).endsWith(failure === 'stalled POST' ? '/request-fusion-return' : '/returns')) {
        if (failure === 'listing failures') throw new Error('network');
        return new Promise<Response>(() => undefined);
      }
      return original(input, init);
    });
    const first = waiter.pullFromFusion().catch((error) => error);
    await vi.advanceTimersByTimeAsync(FUSION_RETURN_TIMEOUT_MS - 1);
    expect(waiter.getState().pullingFromFusion).toBe(true);
    await vi.advanceTimersByTimeAsync(1); expect((await first).message).toContain('within 60 seconds');
    expect(arrival).not.toHaveBeenCalled(); expect(waiter.awaitedRequestIds()).toEqual([]);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('settles under manual supersession once and drops a delayed correlated listing that ignores cancellation', async () => {
    const { waiter, fetcher, arrival } = setup(); const listing = deferred<Response>();
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (input, init) => String(input).endsWith('/returns') ? listing.promise : original(input, init));
    const failed = vi.fn(); const first = waiter.pullFromFusion().catch(failed); await flush();
    const manual = { ...bundle, bundlePath: 'wgreturn/manual.wgreturn' };
    createCadArrivalActions().selectBundleAs(manual);
    await first; expect(failed).toHaveBeenCalledOnce(); expect(failed.mock.calls[0][0]).toBeInstanceOf(SupersededError);
    const feedback = waiter.getState(); listing.resolve(json({ items: [returned], cadFolderConfigured: true }));
    await flush(); await waiter.refresh(); await vi.advanceTimersByTimeAsync(60_000);
    expect(failed).toHaveBeenCalledOnce(); expect(arrival).not.toHaveBeenCalled();
    expect(useCadReturnStore.getState().selectedBundle).toBe(manual); expect(waiter.getState()).toBe(feedback);
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each(['manual pick', 'document', 'identity', 'instance', 'dispose'] as const)('fences a late publication after %s', async (change) => {
    const { waiter, fetcher, connection, selectInstance, arrival } = setup(); const post = deferred<Response>();
    fetcher.mockImplementation(() => post.promise);
    const first = waiter.pullFromFusion().catch((error) => error); await flush();
    if (change === 'manual pick') useCadReturnStore.getState().selectBundle(bundle);
    if (change === 'document') useDesignStore.getState().replaceDocument(designForFamily('ICW'));
    if (change === 'identity') useDocumentStore.getState().setCadLink({ ...identity, designId: 'wgd_new' }, 'current');
    if (change === 'instance') { selectInstance('instance-new'); await connection.read(async () => status({ selectedInstanceId: 'instance-new' })); }
    if (change === 'dispose') waiter.dispose();
    expect(await first).toBeInstanceOf(SupersededError); post.resolve(json({ requestId: 'pull-1', documentName: 'Speaker' }));
    await flush(); expect(arrival).not.toHaveBeenCalled(); expect(waiter.awaitedRequestIds()).toEqual([]);
    expect(fetcher).toHaveBeenCalledOnce();
  });
  it('keeps a newer pull when an older superseded poll finishes', async () => {
    const { waiter, fetcher, setItems, arrival } = setup(); const listing = deferred<Response>(); let listings = 0;
    const original = fetcher.getMockImplementation()!;
    fetcher.mockImplementation(async (input, init) => String(input).endsWith('/returns') && ++listings === 1
      ? listing.promise : original(input, init));
    const old = waiter.pullFromFusion().catch((error) => error); await flush();
    useCadReturnStore.getState().selectBundle(bundle); await old;
    setItems([returned]); const next = waiter.pullFromFusion(); await flush(); expect(await next).toEqual(returned);
    listing.resolve(json({ items: [{ ...returned, bundlePath: 'wgreturn/old.wgreturn' }] })); await flush();
    expect(arrival).toHaveBeenCalledOnce(); expect(useCadReturnStore.getState().selectedBundle?.bundlePath).toBe(returned.bundlePath);
  });
  it('refuses correlated geometry belonging to another project', async () => {
    const { waiter, setItems, arrival } = setup(); const first = waiter.pullFromFusion().catch((error) => error); await flush();
    setItems([{ ...returned, designIds: ['wgd_foreign'] }]); await waiter.refresh();
    expect((await first).message).toContain('another CAD-linked project'); expect(arrival).not.toHaveBeenCalled();
  });
  it('requires fresh reconciliation evidence for a pull that is still pending (row 6)', async () => {
    const { waiter, fetcher } = setup(); const pull = waiter.pullFromFusion().catch(() => undefined); await flush();
    fetcher.mockRejectedValue(new Error('offline'));
    expect(await waiter.reconcile(['pull-1'])).toEqual(new Set());
    waiter.supersede(); await pull;
  });

  it('keeps settled reconciliation evidence when the reconnect feed resolves after the pull (row 25)', async () => {
    const { waiter, setItems } = setup(); const pull = waiter.pullFromFusion(); await flush();
    const ids = waiter.awaitedRequestIds();
    setItems([returned]); await waiter.refresh(); await pull;
    expect(await waiter.reconcile(ids)).toEqual(new Set(['pull-1']));
    expect(await waiter.reconcile(ids)).toEqual(new Set(['pull-1']));
  });

  it('can find the exact awaited return after reconnect without any event or operation store', async () => {
    const { waiter, setItems } = setup(); const first = waiter.pullFromFusion(); await flush();
    expect(waiter.awaitedRequestIds()).toEqual(['pull-1']); setItems([returned]);
    await waiter.refresh(); expect(await first).toEqual(returned);
  });
  it('lets a readable correlated return complete while the outcome reader stalls', async () => {
    const { waiter, fetcher, setItems } = setup(); const original = fetcher.getMockImplementation()!;
    setItems([returned]); fetcher.mockImplementation(async (input, init) => String(input).includes('/return-requests/')
      ? new Promise<Response>(() => undefined) : original(input, init));
    const first = waiter.pullFromFusion(); await flush(); expect(await first).toEqual(returned);
    expect(vi.getTimerCount()).toBe(0);
  });
  it('default arrival keeps compatible settings and enters CAD mode without changing the parametric document', async () => {
    const connection = createCadConnectionStore(); connections.push(connection);
    useCadReturnStore.getState().selectBundle(bundle); useCadReturnStore.getState().setDriveVoltage(8);
    const design = useDesignStore.getState(); const document = useDocumentStore.getState();
    const waiter = createCadReturnWaiter({ connection, readFusionStatus: async () => status({ statusObserved: false }),
      fetcher: (async (input) => String(input).endsWith('/request-fusion-return')
        ? json({ requestId: 'pull-1', documentName: 'Speaker' })
        : String(input).endsWith('/returns') ? json({ items: [returned] }) : json({ requestId: 'pull-1', state: 'accepted' })) as typeof fetch });
    waiters.push(waiter); const first = waiter.pullFromFusion(); await flush(); await first;
    expect(useCadReturnStore.getState().driveVoltageV).toBe(8); expect(workspaceModeStore.getSnapshot().mode).toBe('cad');
    expect(useDesignStore.getState()).toBe(design); expect(useDocumentStore.getState()).toBe(document);
  });
});

it.each(['listing revision', 'mesh settings', 'automatic arrival'] as const)('keeps the return waiter through a background %s', async (change) => {
  const { waiter, setItems } = setup(); useCadReturnStore.getState().selectBundle(bundle);
  const first = waiter.pullFromFusion(); await flush();
  if (change === 'listing revision') useCadReturnStore.getState().refreshSelectedBundle({ ...bundle, modifiedAt: '2026-10-11T12:01:00Z' });
  if (change === 'mesh settings') useCadReturnStore.getState().setRigidSize(9);
  if (change === 'automatic arrival') useCadReturnStore.getState().selectArrivedBundle(bundle);
  expect(waiter.getState().pullingFromFusion).toBe(true);
  setItems([returned]); await waiter.refresh(); expect(await first).toEqual(returned);
});

it('drops a late exact refusal after manual supersession without changing feedback', async () => {
  const { waiter, fetcher } = setup(); const outcome = deferred<Response>();
  const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (input, init) => String(input).includes('/return-requests/') ? outcome.promise : original(input, init));
  const failed = vi.fn(); const first = waiter.pullFromFusion().catch(failed); await flush();
  useCadReturnStore.getState().selectBundle(bundle); await first;
  const before = waiter.getState(); outcome.resolve(json({ requestId: 'pull-1', state: 'rejected', message: 'Refused' })); await flush();
  expect(waiter.getState()).toBe(before); expect(failed).toHaveBeenCalledOnce();
});

it('refuses an unavailable exact target before publishing a file request', async () => {
  const { waiter, read, fetcher } = setup(); read.mockRejectedValueOnce(new Error('offline'));
  await expect(waiter.pullFromFusion()).rejects.toThrow('changed documents');
  expect(posts(fetcher)).toHaveLength(0);
});

it('reports exact refusal even when the return listing stalls', async () => {
  const { waiter, fetcher, setOutcome } = setup(); const original = fetcher.getMockImplementation()!;
  setOutcome({ requestId: 'pull-1', state: 'rejected', reason: null, message: 'Fusion refused this request.' });
  fetcher.mockImplementation(async (input, init) => String(input).endsWith('/returns')
    ? new Promise<Response>(() => undefined) : original(input, init));
  const first = waiter.pullFromFusion().catch((error) => error); await flush();
  expect((await first).message).toContain('refused'); expect(vi.getTimerCount()).toBe(0);
});

it('keeps polling geometry when an outcome read stalls', async () => {
  const { waiter, fetcher, setItems } = setup(); const original = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (input, init) => String(input).includes('/return-requests/')
    ? new Promise<Response>(() => undefined) : original(input, init));
  const first = waiter.pullFromFusion(); await flush(); setItems([returned]);
  await vi.advanceTimersByTimeAsync(2_500); expect(await first).toEqual(returned);
});

it.each(['status', 'listing'] as const)('a delayed archive restore supersedes a pull during its %s read', async (stage) => {
  const { waiter, read, fetcher, arrival } = setup();
  const statusRead = deferred<ReturnType<typeof status>>();
  const listing = deferred<Response>();
  const archive = deferred<Response>();
  const original = fetcher.getMockImplementation()!;
  if (stage === 'status') read.mockImplementationOnce(() => statusRead.promise);
  else fetcher.mockImplementation(async (input, init) => String(input).endsWith('/returns') ? listing.promise : original(input, init));
  const first = waiter.pullFromFusion().catch((error) => error); await flush();
  // Restoration immediately invalidates the previous solvable setup.
  const state = useCadReturnStore.getState();
  const restored = restoreCadJobModel(job(), { enterCadWorkspace: () => undefined },
    (async (input) => String(input).endsWith('/viewport-mesh') ? new Response(mesh) : String(input).endsWith('/workspace') ? json(workspaceFor()) : archive.promise) as typeof fetch);
  expect(useCadReturnStore.getState()).not.toBe(state);
  expect(useCadReturnStore.getState().ingestRecord).toBeNull();
  const viewportIntent = importedMeshStore.captureGeneration();
  if (stage === 'status') statusRead.resolve(status());
  else listing.resolve(json({ items: [returned] }));
  await flush();
  expect(waiter.getState().pullingFromFusion).toBe(false);
  expect(await first).toBeInstanceOf(SupersededError);
  expect(arrival).not.toHaveBeenCalled();
  expect(importedMeshStore.isCurrentGeneration(viewportIntent)).toBe(true);
  expect(posts(fetcher)).toHaveLength(stage === 'status' ? 0 : 1);
  archive.resolve(json(record)); expect(await restored).toBe(true);
  expect(useCadReturnStore.getState().ingestRecord?.ingest_id).toBe(record.ingest_id);
  expect(importedMeshStore.getSnapshot().cad?.ingestId).toBe(record.ingest_id);
});

it('publishes a pull across a background observation poll', async () => {
  const { waiter, read, connection, fetcher, setItems } = setup();
  const pending = deferred<ReturnType<typeof status>>(); read.mockImplementationOnce(() => pending.promise);
  const first = waiter.pullFromFusion();
  await connection.read(async () => status()); pending.resolve(status());
  await flush(); expect(posts(fetcher)).toHaveLength(1);
  setItems([returned]); await waiter.refresh(); expect(await first).toEqual(returned);
});

it.each(['listing', 'outcome'] as const)('aborts stalled %s reads before retrying and never overlaps batches', async (endpoint) => {
  const { waiter, fetcher, setItems } = setup();
  const original = fetcher.getMockImplementation()!;
  const signals: AbortSignal[] = [];
  let inFlight = 0; let maximum = 0;
  fetcher.mockImplementation(async (input, init) => {
    const stalled = endpoint === 'listing' ? String(input).endsWith('/returns') : String(input).includes('/return-requests/');
    if (!stalled) return original(input, init);
    const signal = init?.signal as AbortSignal; signals.push(signal);
    inFlight += 1; maximum = Math.max(maximum, inFlight);
    return new Promise<Response>((_resolve, reject) => signal?.addEventListener('abort', () => {
      inFlight -= 1; reject(new DOMException('Aborted', 'AbortError'));
    }, { once: true }));
  });
  const first = waiter.pullFromFusion().catch((error) => error); await flush();
  const batch = waiter.refresh(); expect(waiter.refresh()).toBe(batch);
  await vi.advanceTimersByTimeAsync(10_001);
  expect(signals.length).toBeGreaterThanOrEqual(4);
  expect(signals.slice(0, -1).every((signal) => signal.aborted)).toBe(true);
  expect(maximum).toBe(1);
  if (endpoint === 'listing') waiter.dispose();
  else { setItems([returned]); await vi.advanceTimersByTimeAsync(2_500); }
  await first; await flush();
  expect(signals.every((signal) => signal.aborted)).toBe(true);
  expect(inFlight).toBe(0); expect(vi.getTimerCount()).toBe(0);
});


it('settling a pull releases its hanging command status read before the client timeout', async () => {
  const hung = deferred<ReturnType<typeof status>>();
  const connection = createCadConnectionStore(); connections.push(connection);
  const waiter = createCadReturnWaiter({ connection, readFusionStatus: () => hung.promise }); waiters.push(waiter);
  const pull = waiter.pullFromFusion();
  const result = expect(pull).rejects.toBeInstanceOf(SupersededError);
  expect(await connection.read(async () => status())).toBeNull();
  waiter.supersede(); await result;
  await flush();
  expect(await connection.read(async () => status())).not.toBeNull();
  hung.resolve(status()); await flush();
});
