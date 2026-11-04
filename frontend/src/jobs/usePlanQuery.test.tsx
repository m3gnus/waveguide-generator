import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { SOLVE_PLAN_DEBOUNCE_MS } from './planQueryPolicy';
import { usePlanQuery } from './usePlanQuery';

it.each([
  ['solve-plan', []],
  ['imported-solve-plan', ['capabilities']],
] as const)('preserves %s identity, request bytes and cancellation through rapid edits', async (prefix, identity) => {
  vi.useFakeTimers();
  (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
  const client = new QueryClient();
  const host = document.createElement('div');
  const root = createRoot(host);
  const request = vi.fn((_body: string, _fetch: typeof fetch, _signal?: AbortSignal) => new Promise<{ engine: string }>(() => {}));
  let snapshot: ReturnType<typeof usePlanQuery<{ engine: string }>>;
  function Subject({ body }: { body: string | null }) {
    snapshot = usePlanQuery(body, prefix, request, identity);
    return null;
  }
  const render = async (body: string | null) => act(async () => {
    root.render(<QueryClientProvider client={client}><Subject body={body}/></QueryClientProvider>);
  });
  try {
    await render('{"engine":"auto"}');
    expect(request).toHaveBeenCalledOnce();
    expect(client.getQueryCache().getAll().map(query => query.queryKey)).toEqual([
      [prefix, '{"engine":"auto"}', ...identity],
    ]);
    const firstSignal = request.mock.calls[0][2]!;
    expect(firstSignal.aborted).toBe(false);
    await render('{"engine":"metal"}');
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS - 1); });
    await render('{"engine":"bempp"}');
    expect(snapshot!.plan).toBeNull();
    expect(snapshot!.isPending).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS); });
    expect(firstSignal.aborted).toBe(true);
    expect(request.mock.calls.map(call => call[0])).toEqual(['{"engine":"auto"}', '{"engine":"bempp"}']);
    const lastSignal = request.mock.calls[1][2]!;
    await render('{"engine":"metal"}');
    await render(null);
    await act(async () => { await vi.advanceTimersByTimeAsync(SOLVE_PLAN_DEBOUNCE_MS * 2); });
    expect(request).toHaveBeenCalledTimes(2);
    expect(lastSignal.aborted).toBe(true);
    expect(snapshot!).toEqual({ plan: null, error: null, isPending: false });
  } finally {
    act(() => root.unmount());
    client.clear();
    vi.clearAllTimers();
    vi.useRealTimers();
  }
});
