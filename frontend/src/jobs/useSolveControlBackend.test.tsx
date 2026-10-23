import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { resetDesignStore, useDesignStore } from '../stores/design';
import { DirectivityMapControls, SolveOptionsControls } from '../design/SolveOptionsSections';
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
  expect(host.textContent).toBe(' / pending');
  await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
  await flushReact();
  expect(host.textContent).toBe('metal / metal');
});

it('keeps adaptive and field controls enabled through their own toggles, sweep edits and geometry debounce', async () => {
  client.setQueryData(CAPABILITIES_QUERY_KEY, {
    engines: [
      { name: 'metal', available: true, field_traces: false },
      { name: 'beat-cpu', available: true, field_traces: true },
    ],
    engineSelection: { default: 'auto', resolvedDefault: 'metal', full3dOrder: ['metal', 'beat-cpu'] },
  });
  const fetchMock = vi.fn(async () => new Response(JSON.stringify({ engine: 'beat-cpu', formulation: 'full-3d', reason: 'Geometry routing', eligibility_reasons: [] })));
  vi.stubGlobal('fetch', fetchMock);
  act(() => root.render(<QueryClientProvider client={client}><Subject /><SolveOptionsControls /><DirectivityMapControls /></QueryClientProvider>));
  await flushReact();
  const adaptive = host.querySelector<HTMLInputElement>('#adaptive-frequency-sampling')!;
  const field = host.querySelector<HTMLInputElement>('#polar-field-plane')!;
  expect(adaptive.disabled).toBe(false);
  expect(field.disabled).toBe(false);
  act(() => adaptive.click());
  expect(adaptive.checked).toBe(true);
  expect(adaptive.disabled).toBe(false);
  expect(field.disabled).toBe(false);
  expect(host.textContent).toContain('beat-cpu / pending');
  act(() => field.click());
  expect(field.disabled).toBe(false);
  act(() => useSolveOptionsStore.getState().setFrequencySpacing('linear'));
  expect(adaptive.disabled).toBe(false);
  expect(field.disabled).toBe(false);
  act(() => useDesignStore.setState({ design: { ...useDesignStore.getState().design, length: 150 } }));
  expect(adaptive.disabled).toBe(false);
  expect(field.disabled).toBe(false);
  fetchMock.mockImplementation(async () => new Response(JSON.stringify({ engine: 'metal', formulation: 'full-3d', reason: 'New routing', eligibility_reasons: [] })));
  await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
  await flushReact();
  // A checked inactive preference can be removed; the settled field flag gates.
  expect(adaptive.disabled).toBe(false);
  expect(host.querySelector('#adaptive-sampling-reason')?.textContent).toContain('BEAT only');
  expect(field.disabled).toBe(true);
  act(() => adaptive.click());
  expect(adaptive.checked).toBe(false);
  expect(adaptive.disabled).toBe(true);
});
