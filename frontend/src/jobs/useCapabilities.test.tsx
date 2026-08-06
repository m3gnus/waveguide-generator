import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AppQueryProvider, appQueryClient } from '../queryClient';
import { CAPABILITIES_STALE_MS, useCapabilities, useCapabilityRefreshOnReconnect } from './useCapabilities';

const CAPABILITIES = {
  engines: [
    { name: 'metal', available: true, reason: 'ok', version: '0.1.0', fast_paths: ['axisymmetric-meridian'] },
    { name: 'bempp', available: false, reason: 'not installed', version: null, fast_paths: [] },
  ],
};

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
  });

  const tick = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });

  /**
   * Render, then pump until the query has resolved and re-rendered. The budget
   * covers the one retry `useCapabilities` allows, whose backoff is ~1s; a
   * settled query exits on the first poll, so the success paths stay fast.
   */
  const render = async (children: React.ReactNode, settled: () => boolean = () => true) => {
    await act(async () => {
      root.render(<QueryClientProvider client={client}>{children}</QueryClientProvider>);
    });
    for (let attempt = 0; attempt < 400 && !settled(); attempt += 1) await tick();
  };

  const textOf = (tag: string) => host.querySelector(`[data-tag="${tag}"]`)?.textContent ?? '';

  it('issues one request for every consumer on the page', async () => {
    // The status bar, the job coordinator and the solver-options section each
    // used to fetch independently, so a cold load made three identical calls.
    await render(
      <><Consumer tag="status"/><Consumer tag="jobs"/><Consumer tag="options"/></>,
      () => textOf('status') !== '',
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith('/api/capabilities');
    for (const tag of ['status', 'jobs', 'options']) expect(textOf(tag)).toBe('metal,bempp');
  });

  it('does not refetch when a panel remounts', async () => {
    await render(<Consumer tag="options"/>, () => textOf('options') !== '');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // Dockview disposes and recreates a panel's React root on every tab switch.
    await render(<></>);
    await render(<Consumer tag="options"/>, () => textOf('options') !== '');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(textOf('options')).toBe('metal,bempp');
  });

  it('surfaces a failure as a message rather than an empty engine list', async () => {
    fetchMock.mockImplementation(async () => new Response('{"detail":"probe exploded"}', { status: 500 }));
    await render(<Consumer tag="status"/>, () => textOf('status') !== '');
    expect(textOf('status')).toContain('probe exploded');
  });

  it('caches for a working session but not forever', () => {
    expect(CAPABILITIES_STALE_MS).toBeGreaterThan(60_000);
    expect(Number.isFinite(CAPABILITIES_STALE_MS)).toBe(true);
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
  });

  const show = async (connection: string) => {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><Subject connection={connection}/></QueryClientProvider>);
    });
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (host.textContent !== '') break;
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    }
  };

  const settleRefetch = async () => {
    for (let attempt = 0; attempt < 60; attempt += 1) {
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    }
  };

  it('refetches after the socket drops and comes back', async () => {
    await show('connecting');
    await show('connected');
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await show('reconnecting');
    await settleRefetch();
    // Still one: a drop alone is not evidence of a new server.
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await show('connected');
    await settleRefetch();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('does not refetch on the first connection, which is already loading', async () => {
    await show('idle');
    await show('connecting');
    await show('connected');
    await settleRefetch();
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
    }
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (hosts.every((host) => host.textContent !== '')) break;
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 10)); });
    }
    expect(fetchMock).toHaveBeenCalledTimes(1);
    for (const host of hosts) expect(host.textContent).toBe('metal,bempp');
  });
});
