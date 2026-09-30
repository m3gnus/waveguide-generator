import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { preferencesStore } from '../prefs/preferences';
import { AppQueryProvider, appQueryClient } from '../queryClient';
import { CAPABILITIES_STALE_MS, useCapabilities, useCapabilityRefreshOnReconnect } from './useCapabilities';

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

  it('issues one request for every consumer on the page', async () => {
    // The status bar, the job coordinator and the solver-options section each
    // used to fetch independently, so a cold load made three identical calls.
    await render(
      <><Consumer tag="status"/><Consumer tag="jobs"/><Consumer tag="options"/></>,
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/capabilities');
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

  it('bounds pending qualification polling even if the server never finishes', async () => {
    const pending = { ...CAPABILITIES, engines: [{ name: 'bempp', qualification: 'pending' }] };
    fetchMock.mockImplementation(async () => new Response(JSON.stringify(pending), { status: 200 }));
    await render(<Consumer tag="status"/>);
    await act(async () => { await vi.advanceTimersByTimeAsync(5 * 60_000 + 10_000); });
    await flushReact();
    const count = fetchMock.mock.calls.length;
    expect(count).toBeGreaterThan(2);
    expect(count).toBeLessThan(40);
    await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
    expect(fetchMock).toHaveBeenCalledTimes(count);
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
