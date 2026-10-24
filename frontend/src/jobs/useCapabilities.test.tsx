import { jobsSocket } from '../api/jobsSocket';
import { getCapabilities } from './actions';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { AppQueryProvider, appQueryClient } from '../queryClient';
import { CAPABILITIES_QUERY_KEY, CAPABILITIES_STALE_MS, OPENCL_POLL_FALLBACK_MS, OPENCL_POLL_SAFETY_FACTOR, useCapabilities, useCapabilityRefreshOnReconnect } from './useCapabilities';

const CAPABILITIES = {
  engines: [
    { name: 'metal', available: true, reason: 'ok', version: '0.1.0', fast_paths: [] },
    { name: 'bempp', available: false, reason: 'not installed', version: null, fast_paths: [] },
  ],
  engineSelection: {
    default: 'auto',
    resolvedDefault: 'metal',
    full3dOrder: ['metal', 'beat', 'bempp', 'dryrun'],
  },
};

const flushReact = () => act(async () => {
  await Promise.resolve();
  await vi.advanceTimersByTimeAsync(0);
  await Promise.resolve();
});

function Consumer({ tag }: { tag: string }) {
  const { engines, error } = useCapabilities();
  return <div data-tag={tag}>{error ?? engines.map((engine) => engine.name).join(',')}</div>;
}

describe('useCapabilities', () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    fetchMock = vi.fn(async () => new Response(JSON.stringify(CAPABILITIES), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    client.clear();
    vi.unstubAllGlobals();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  const render = async (children: React.ReactNode) => {
    await act(async () => {
      root.render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
    });
    await flushReact();
  };

  const textOf = (tag: string) => host.querySelector(`[data-tag="${tag}"]`)?.textContent ?? '';

  it('versions requests even when an unchanged result shares its cached data', async () => {
    let snapshot!: ReturnType<typeof useCapabilities>;
    function Observer() { snapshot = useCapabilities(); return null; }
    await render(<Observer/>);
    const generation = snapshot.capabilityGeneration;
    const engines = snapshot.engines;
    expect(generation).toBeGreaterThan(0);
    await render(<Observer/>);
    expect(snapshot.capabilityGeneration).toBe(generation);
    await act(async () => snapshot.refreshCapabilities());
    expect(snapshot.engines).toBe(engines); // Query structural sharing keeps the same array.
    expect(snapshot.capabilityGeneration).toBe(generation + 1);
  });

  it('captures freshness before HTTP completion and shares the issue clock across consumers', async () => {
    let resolve!: (response: Response) => void;
    fetchMock.mockImplementationOnce(() => new Promise<Response>(done => { resolve = done; }));
    let first!: ReturnType<typeof useCapabilities>;
    let second!: ReturnType<typeof useCapabilities>;
    function Observer() { first = useCapabilities(); second = useCapabilities(); return null; }
    await render(<Observer/>);
    const acceptedAt = first.capabilityIssueSequence;
    expect(first.capabilityGeneration).toBe(0);
    expect(acceptedAt()).toBe(1);
    expect(second.capabilityIssueSequence()).toBe(1);
    await act(async () => { resolve(new Response(JSON.stringify(CAPABILITIES))); });
    await flushReact();
    expect(first.capabilityGeneration).toBe(1);
    expect(acceptedAt()).toBe(1); // Arrival never advances freshness.
    await act(async () => first.refreshCapabilities());
    expect(first.capabilityGeneration).toBe(2);
    expect(acceptedAt()).toBe(2); // Stable callback reads the live clock.
  });

  it.each(['failed', 'ready'])('ignores an older %s response after a newer snapshot', async olderState => {
    let snapshot!: ReturnType<typeof useCapabilities>;
    function Observer() { snapshot = useCapabilities(); return null; }
    await render(<Observer/>);
    const resolvers: ((response: Response) => void)[] = [];
    fetchMock.mockImplementation(() => new Promise<Response>(resolve => resolvers.push(resolve)));
    const query = client.getQueryCache().find({ queryKey: CAPABILITIES_QUERY_KEY })!;
    const fetchSnapshot = query.options.queryFn;
    if (typeof fetchSnapshot !== 'function') throw new Error('Missing capability transport');
    // Start two real transports independently, then deliver them in reverse
    // order through the same cache merge used by the query observer.
    const context = { client, queryKey: CAPABILITIES_QUERY_KEY, signal: new AbortController().signal, meta: undefined };
    const older = fetchSnapshot(context);
    const newer = fetchSnapshot(context);
    const response = (state: string) => new Response(JSON.stringify({ ...CAPABILITIES,
      engines: [{ name: 'beat-metal', available: state === 'ready', runtime_state: state }] }));
    resolvers[1](response(olderState === 'ready' ? 'failed' : 'ready'));
    await act(async () => { client.setQueryData(CAPABILITIES_QUERY_KEY, await newer); });
    await flushReact();
    const latest = snapshot;
    resolvers[0](response(olderState));
    await act(async () => { client.setQueryData(CAPABILITIES_QUERY_KEY, await older); });
    await flushReact();
    expect(snapshot.capabilityGeneration).toBe(latest.capabilityGeneration);
    expect(snapshot.engines).toBe(latest.engines);
    expect(snapshot.engines[0].runtime_state).toBe(olderState === 'ready' ? 'failed' : 'ready');
  });

  it('refreshes cached warming plans even when the capability fingerprint is unchanged', async () => {
    let refresh!: () => Promise<void>;
    function WaitingSolve() {
      refresh = useCapabilities().refreshCapabilities;
      return null;
    }
    await render(<WaitingSolve/>);
    const keys = [['solve-plan', 'same-inputs'], ['imported-solve-plan', 'same-inputs']];
    for (const key of keys) client.setQueryData(key, { readiness: { remedies: [{ kind: 'wait' }] } });
    await act(async () => refresh());
    for (const key of keys) expect(client.getQueryState(key)?.isInvalidated).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('replaces stale capability information with a backend connection notice', async () => {
    const manager = jobsSocket as unknown as { snapshot: ReturnType<typeof jobsSocket.getSnapshot>; listeners: Set<() => void> };
    const previous = manager.snapshot;
    try {
      await render(<Consumer tag="status"/>);
      await act(async () => {
        manager.snapshot = { ...previous, connection: 'reconnecting' };
        manager.listeners.forEach((listener) => listener());
      });
      expect(textOf('status')).toContain('Backend connection interrupted');
    } finally {
      await act(async () => {
        manager.snapshot = previous;
        manager.listeners.forEach((listener) => listener());
      });
    }
  });

  it('issues one request for every consumer on the page', async () => {
    // The status bar, the job coordinator and the solver-options section each
    // used to fetch independently, so a cold load made three identical calls.
    await render(
      <><Consumer tag="status"/><Consumer tag="jobs"/><Consumer tag="options"/></>,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/capabilities', { signal: expect.any(AbortSignal) });
    for (const tag of ['status', 'jobs', 'options']) expect(textOf(tag)).toBe('metal,bempp');
  });

  it('does not refetch when a panel remounts', async () => {
    await render(<Consumer tag="options"/>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Dockview disposes and recreates a panel's React root on every tab switch.
    await render(<></>);
    await render(<Consumer tag="options"/>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(textOf('options')).toBe('metal,bempp');
  });

  it.each([[{ onshape: true }, true], [{ onshape: false }, false], [{}, false]])(
    'reports the Onshape build flag to the preferences store (%j)', async (extra, offered) => {
      fetchMock.mockImplementation(async () => new Response(JSON.stringify({ ...CAPABILITIES, ...extra }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }));
      preferencesStore.setOnshapeAvailable(!offered);
      await render(<Consumer tag="status"/>);
      expect(preferencesStore.isOnshapeAvailable()).toBe(offered);
    },
  );

  it('surfaces a failure as a message rather than an empty engine list', async () => {
    fetchMock.mockImplementation(async () => new Response('{"detail":"probe exploded"}', { status: 500 }));
    await render(<Consumer tag="status"/>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(999); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(textOf('status')).toContain('probe exploded');
  });

  it('caches for a working session but not forever', () => {
    expect(CAPABILITIES_STALE_MS).toBeGreaterThan(60_000);
    expect(Number.isFinite(CAPABILITIES_STALE_MS)).toBe(true);
  });

  it('publishes a ready CPU transition without a reconnect and invalidates plans', async () => {
    const preparing = {
      ...CAPABILITIES,
      cpuPreparationInFlight: true,
      engines: [...CAPABILITIES.engines, {
        name: 'beat-cpu', available: false, reason: 'checking hardware', version: '0.1.0', fast_paths: [],
      }],
    };
    const ready = {
      ...preparing,
      cpuPreparationInFlight: false,
      engines: preparing.engines.map((engine) => engine.name === 'beat-cpu'
        ? { ...engine, available: true, reason: 'ready' }
        : engine),
    };
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify(preparing), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(ready), { status: 200 }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');

    await render(<Consumer tag="status"/>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await flushReact();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });

    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['solve-plan'] });
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('polls pending OpenCL with backoff, stops on done, and invalidates plans', async () => {
    const pending = { ...CAPABILITIES, engines: [{
      name: 'bempp', available: false, qualification: 'pending', assembly_backend: null,
    }] };
    const done = { ...pending, engines: [{
      name: 'bempp', available: true, qualification: 'done', assembly_backend: 'opencl',
    }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(pending), { status: 200 }));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await render(<Consumer tag="status"/>);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(1999); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(done), { status: 200 }));
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(3);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['solve-plan'] });
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it.each(['inventory_timeout', 'smoke_test_timeout', 'probe_error'])(
    'polls a retryable %s until OpenCL recovers without a remount', async (reason) => {
      const retrying = { ...CAPABILITIES, engines: [{
        name: 'bempp', available: true, qualification: 'done', assembly_backend: 'numba',
        opencl_unavailable_reason: reason, opencl_retry_pending: true,
      }] };
      const recovered = { ...retrying, engines: [{ ...retrying.engines[0],
        assembly_backend: 'opencl', opencl_unavailable_reason: null, opencl_retry_pending: false,
      }] };
      fetchMock.mockImplementation(async () => new Response(JSON.stringify(retrying), { status: 200 }));
      await render(<Consumer tag="status"/>);
      await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
      await flushReact();
      expect(fetchMock).toHaveBeenCalledTimes(2);
      fetchMock.mockImplementation(async () => new Response(JSON.stringify(recovered), { status: 200 }));
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      await flushReact();
      expect(fetchMock).toHaveBeenCalledTimes(3);
      await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60_000); });
      expect(fetchMock).toHaveBeenCalledTimes(3);
    },
  );

  it.each([false, undefined])('does not guess a retry from a cached timeout (%s)', async (retry) => {
    const terminal = { ...CAPABILITIES, engines: [{
      name: 'bempp', available: true, qualification: 'done', assembly_backend: 'numba',
      opencl_unavailable_reason: 'inventory_timeout', opencl_retry_pending: retry,
    }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(terminal), { status: 200 }));
    await render(<Consumer tag="status"/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('stops when a retry finishes with a terminal timeout', async () => {
    const row = { name: 'bempp', qualification: 'done', assembly_backend: 'numba',
      opencl_unavailable_reason: 'smoke_test_timeout', opencl_retry_pending: true };
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ ...CAPABILITIES, engines: [row] })))
      .mockImplementation(async () => new Response(JSON.stringify({ ...CAPABILITIES,
        engines: [{ ...row, opencl_retry_pending: false }] })));
    await render(<Consumer tag="status"/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(10 * 60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it.each([
    { name: 'bempp', qualification: 'pending' },
    // Retry ownership alone drives polling, independent of backend/reason.
    { name: 'bempp', qualification: 'done', opencl_retry_pending: true },
  ])('observes long qualification resolution after 300 seconds without remounting (%j)', async (engine) => {
    let response: { engines: Record<string, unknown>[]; engineSelection: Omit<typeof CAPABILITIES.engineSelection, 'resolvedDefault'> & { resolvedDefault: string | null }; opencl_qualification_max_seconds: number } = { ...CAPABILITIES, opencl_qualification_max_seconds: 460,
      engines: [engine], engineSelection: { ...CAPABILITIES.engineSelection, resolvedDefault: null as string | null } };
    function Lifecycle() {
      const snapshot = useCapabilities();
      return <div>{snapshot.engines[0]?.qualification}|{snapshot.engines[0]?.assembly_backend}|{snapshot.engineSelection.resolvedDefault}</div>;
    }
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(response)));
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    await render(<Lifecycle/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(310_000); });
    const count = fetchMock.mock.calls.length;
    expect(count).toBeGreaterThan(2);
    response = { ...response, engines: [{ ...engine, qualification: 'done', opencl_retry_pending: false,
      assembly_backend: 'opencl', available: true }],
      engineSelection: { ...response.engineSelection, resolvedDefault: 'bempp' } };
    await act(async () => { await vi.advanceTimersByTimeAsync(180_000); });
    await flushReact();
    expect(fetchMock.mock.calls.length).toBeGreaterThan(count);
    expect(host.textContent).toBe('done|opencl|bempp');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['solve-plan'] });
    const resolvedCount = fetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(resolvedCount);
  });

  it.each([10, 30, undefined, 0, -1])('derives its safety ceiling from the server budget (%s)', async (seconds) => {
    const pending = { ...CAPABILITIES, opencl_qualification_max_seconds: seconds,
      engines: [{ name: 'bempp', qualification: 'pending' }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(pending)));
    function Lifecycle({ tag }: { tag: string }) {
      const snapshot = useCapabilities();
      return <div data-tag={tag}>{snapshot.qualificationRefreshNeeded ? 'expired' : 'polling'}
        <button onClick={snapshot.refreshCapabilities}>Refresh</button></div>;
    }
    await render(<><Lifecycle tag="first"/><Lifecycle tag="second"/></>);
    const ceiling = seconds && seconds > 0 ? seconds * 1000 * OPENCL_POLL_SAFETY_FACTOR : OPENCL_POLL_FALLBACK_MS;
    await act(async () => { await vi.advanceTimersByTimeAsync(ceiling - 1); });
    expect(textOf('first')).toBe('pollingRefresh');
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    await flushReact();
    expect(textOf('first')).toBe('expiredRefresh');
    expect(textOf('second')).toBe('expiredRefresh');
    const count = fetchMock.mock.calls.length;
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(count);
    await act(async () => { host.querySelector<HTMLButtonElement>('button')!.click(); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(count + 1);
    expect(textOf('first')).toBe('pollingRefresh');
    expect(textOf('second')).toBe('pollingRefresh');
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(fetchMock.mock.calls.length).toBeGreaterThan(count + 1);
  });

  it('stops polling after terminal preparation failure', async () => {
    const preparing = { ...CAPABILITIES, cpuPreparationInFlight: true };
    const failed = {
      ...CAPABILITIES,
      cpuPreparationInFlight: false,
      engines: [...CAPABILITIES.engines, {
        name: 'beat-cpu', available: false,
        reason: 'Julia executable unavailable after provisioning failed',
        version: '0.1.0', fast_paths: [],
      }],
    };
    fetchMock
      .mockResolvedValueOnce(new Response(JSON.stringify(preparing), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify(failed), { status: 200 }));

    await render(<Consumer tag="status"/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(1000); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not poll a terminal old-package capability reason', async () => {
    const oldPackage = {
      ...CAPABILITIES,
      cpuPreparationInFlight: false,
      engines: [...CAPABILITIES.engines, {
        name: 'beat-cpu', available: false,
        reason: 'Installed package predates CPU runtime provisioning',
        version: '0.1.0', fast_paths: [],
      }],
    };
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(oldPackage), { status: 200 }),
    );

    await render(<Consumer tag="status"/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * staleTime alone does not heal a server restart: it marks data stale, it never
 * refetches on a timer. A focused tab that never remounts would sit on the old
 * engine list. The jobs socket reconnecting is the signal that closes that.
 */
describe('useCapabilityRefreshOnReconnect', () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let fetchMock: ReturnType<typeof vi.fn>;

  function Subject({ connection }: { connection: string }) {
    useCapabilityRefreshOnReconnect(connection);
    const { engines } = useCapabilities();
    return <div data-tag="subject">{engines.map((engine) => engine.name).join(',')}</div>;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    fetchMock = vi.fn(async () => new Response(JSON.stringify(CAPABILITIES), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    host = document.createElement('div');
    document.body.append(host);
    root = createRoot(host);
  });

  afterEach(() => {
    act(() => root.unmount());
    host.remove();
    client.clear();
    vi.unstubAllGlobals();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  const show = async (connection: string) => {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><Subject connection={connection}/></QueryClientProvider>);
    });
    await flushReact();
  };

  it('refetches after the socket drops and comes back', async () => {
    await show('connecting');
    await show('connected');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await show('reconnecting');
    // Still one: a drop alone is not evidence of a new server.
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await show('connected');
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not refetch on the first connection, which is already loading', async () => {
    await show('idle');
    await show('connecting');
    await show('connected');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});

/**
 * The dedupe only works in production because every mount -- the app root and
 * each of Dockview's independent per-panel React roots -- resolves to the one
 * `appQueryClient` module singleton. A second client anywhere would silently
 * restore the duplicate requests while the tests above still passed.
 */
describe('AppQueryProvider wiring', () => {
  let hosts: HTMLDivElement[];
  let roots: Root[];
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    fetchMock = vi.fn(async () => new Response(JSON.stringify(CAPABILITIES), {
      status: 200, headers: { 'Content-Type': 'application/json' },
    }));
    vi.stubGlobal('fetch', fetchMock);
    appQueryClient.clear();
    hosts = [];
    roots = [];
  });

  afterEach(() => {
    for (const root of roots) act(() => root.unmount());
    for (const host of hosts) host.remove();
    appQueryClient.clear();
    vi.unstubAllGlobals();
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('shares one client across separate React roots, as Dockview mounts them', async () => {
    for (const tag of ['viewport-root', 'geometry-root', 'jobs-root']) {
      const host = document.createElement('div');
      document.body.append(host);
      hosts.push(host);
      const root = createRoot(host);
      roots.push(root);
      await act(async () => {
        root.render(<AppQueryProvider><Consumer tag={tag}/></AppQueryProvider>);
      });
      await flushReact();
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    for (const host of hosts) expect(host.textContent).toBe('metal,bempp');
  });
});


it('bounds a stuck capability request independently of slow export requests', async () => {
  vi.useFakeTimers();
  try {
    const fetcher = vi.fn((_url: unknown, init?: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init!.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    const request = getCapabilities(fetcher as typeof fetch);
    const assertion = expect(request).rejects.toThrow('Capability refresh timed out');
    await vi.advanceTimersByTimeAsync(10_000);
    await assertion;
    expect(fetcher).toHaveBeenCalledOnce();
  } finally { vi.useRealTimers(); }
});
