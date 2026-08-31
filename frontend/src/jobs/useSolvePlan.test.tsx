import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { designForFamily, type DesignDocument } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';
import { SOLVE_PLAN_DEBOUNCE_MS, SOLVE_PLAN_RECOVERY_MS, useSolvePlan } from './useSolvePlan';

const PLAN = {
  engine: 'bempp',
  formulation: 'full-3d',
  reason: "explicit solver_mode='full_3d'",
  eligibility_reasons: [],
};

const OPTIONS: SolveOptions = {
  engine: 'auto', solver_mode: 'full_3d', symmetry: 'auto', mesh_validation_mode: 'warn',
  verbose: false, frequency_spacing: 'log',
  polar_config: {
    angle_range: [0, 180, 37], angle_step: 5, distance: 2, norm_angle: 5, inclination: 45,
    enabled_axes: ['horizontal'], observation_origin: 'mouth', spherical_sampling: false,
    field_plane: true,
  },
};

const ok = () => new Response(JSON.stringify(PLAN), {
  status: 200, headers: { 'Content-Type': 'application/json' },
});

const flushReact = () => act(async () => {
  await Promise.resolve();
  await vi.advanceTimersByTimeAsync(0);
  await Promise.resolve();
});

/** Solve is disabled unless this reads "ready"; that is the whole point. */
function Subject({ design }: { design: DesignDocument }) {
  const { plan, error, isPending } = useSolvePlan(design, OPTIONS);
  return <div data-tag="subject">{plan ? 'ready' : error ?? (isPending ? 'pending' : 'idle')}</div>;
}

describe('useSolvePlan', () => {
  let host: HTMLDivElement;
  let root: Root;
  let client: QueryClient;
  let fetchMock: ReturnType<typeof vi.fn>;
  let design: DesignDocument;

  beforeEach(() => {
    vi.useFakeTimers();
    (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
    fetchMock = vi.fn(async () => ok());
    vi.stubGlobal('fetch', fetchMock);
    client = new QueryClient();
    design = designForFamily('OSSE');
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

  const mount = async () => {
    await act(async () => {
      root.render(<QueryClientProvider client={client}><Subject design={design}/></QueryClientProvider>);
    });
    // The hook debounces the request body before the query is allowed to run.
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    await flushReact();
  };

  const text = () => host.querySelector('[data-tag="subject"]')?.textContent ?? '';

  it('retries a fault once, without the design changing', async () => {
    fetchMock.mockImplementationOnce(async () => { throw new TypeError('Failed to fetch'); });
    await mount();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    // The default retry delay, as useCapabilities relies on it too.
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(text()).toBe('ready');
  });

  it('keeps asking while the backend is unreachable, and recovers when it answers', async () => {
    // The reported failure: the window stays open, the socket never reconnects
    // to trigger `useCapabilityRefreshOnReconnect`, and Solve used to stay grey
    // for the rest of the session because nothing here ever asked again.
    fetchMock.mockImplementation(async () => { throw new TypeError('Failed to fetch'); });
    await mount();
    await act(async () => { await vi.advanceTimersByTimeAsync(1_000); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(text()).toContain('Failed to fetch');

    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_RECOVERY_MS); });
    await flushReact();
    expect(fetchMock.mock.calls.length).toBeGreaterThan(2);

    fetchMock.mockImplementation(async () => ok());
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_RECOVERY_MS); });
    await flushReact();
    expect(text()).toBe('ready');
  });

  it('stops polling once the plan arrives', async () => {
    await mount();
    expect(text()).toBe('ready');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_RECOVERY_MS * 4); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('takes a refusal as the answer: no retry, no polling, message shown', async () => {
    // 422 is the server considering the submission and declining it. Asking
    // again cannot change it, and polling would replace the reason the user
    // needs with a pending state.
    fetchMock.mockImplementation(async () => new Response(
      JSON.stringify({ detail: 'symmetry plane intersects the mouth' }),
      { status: 422, headers: { 'Content-Type': 'application/json' } },
    ));
    await mount();
    expect(text()).toContain('symmetry plane intersects the mouth');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_RECOVERY_MS * 4); });
    await flushReact();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
