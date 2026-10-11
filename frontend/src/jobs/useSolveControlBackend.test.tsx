import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resetDesignStore } from '../stores/design';
import { resetSolveOptionsStore, useSolveOptionsStore } from '../stores/solveOptions';
import { CAPABILITIES_QUERY_KEY } from './useCapabilities';
import { useSolveControlBackend } from './useSolveControlBackend';
import { SOLVE_PLAN_DEBOUNCE_MS } from './useSolvePlan';

function Subject() {
  const { capability, plan } = useSolveControlBackend('parametric');
  return <span>{capability?.name} / {plan?.engine ?? 'pending'}</span>;
}
const flushReact = () => act(async () => {
  await Promise.resolve();
  await vi.advanceTimersByTimeAsync(0);
  await Promise.resolve();
});
let host: HTMLDivElement;
let root: Root;
let client: QueryClient;
beforeEach(() => {
  (globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  resetDesignStore();
  resetSolveOptionsStore();
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    engines: ['metal', 'bempp', 'beat-cpu'].map((name) => ({ name, available: true })),
    engineSelection: { default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal', 'bempp', 'beat-cpu'] },
  });
  host = document.createElement('div');
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount());
  host.remove();
  client.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it.each(['fast', 'accurate'] as const)('uses the server %s plan instead of guessing from host order', async (accuracy) => {
  useSolveOptionsStore.getState().setAccuracy(accuracy);
  const engine = accuracy === 'accurate' ? 'beat-cpu' : 'bempp';
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ engine, formulation: 'full-3d', reason: 'Geometry routing', eligibility_reasons: [] })));
  vi.stubGlobal('fetch', fetchMock);
  act(() => root.render(<QueryClientProvider client={client}><Subject /></QueryClientProvider>));
  await flushReact();
  expect(host.textContent).toBe(`${engine} / ${engine}`);
  const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
  expect(url).toBe('/api/solve/plan');
  expect(JSON.parse(init.body as string).options).toMatchObject({ engine: 'auto', ...(accuracy === 'accurate' ? { accuracy } : {}) });
});

it('drops a stale plan while a changed engine is being resolved', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ engine: 'metal', formulation: 'full-3d', reason: 'Ready', eligibility_reasons: [] }))));
  act(() => root.render(<QueryClientProvider client={client}><Subject /></QueryClientProvider>));
  await flushReact();
  act(() => useSolveOptionsStore.getState().setEngine('beat-cpu'));
  expect(host.textContent).toBe('beat-cpu / pending');
  await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
  await flushReact();
  expect(host.textContent).toBe('metal / metal');
});
