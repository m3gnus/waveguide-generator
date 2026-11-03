import { afterEach, expect, it, vi } from 'vitest';
import { activityStore } from './activity';
import { stagedActivity } from './stagedActivity';
import { deferred } from './cadlink/selection.fixtures';

afterEach(() => { activityStore.reset(); vi.useRealTimers(); });

it.each(['assembly-build', 'export', 'send-to-cad'] as const)('%s receives every server stage in order and stops polling', async kind => {
  vi.useFakeTimers();
  const response = deferred<Response>();
  const stages = ['resolve', 'mesh', 'check', kind === 'export' ? 'write' : 'publish'];
  let completed = 1;
  let id: string | null = null;
  const fetcher = vi.fn<typeof fetch>(async (url, init) => {
    if (init?.method === 'POST') {
      id = new Headers(init.headers).get('X-WG-Operation-Id');
      return response.promise;
    }
    expect(url).toBe(`/api/operation-progress/${id}`);
    return new Response(JSON.stringify({ stages: stages.slice(0, completed), total: 4 }));
  });
  const counts: number[] = [];
  const unsubscribe = activityStore.subscribe(() => {
    const done = activityStore.getSnapshot()[0]?.progress?.done;
    if (done !== undefined && counts.at(-1) !== done) counts.push(done);
  });
  const pending = stagedActivity({ kind, label: 'Working' }, fetcher, undefined, async (fetcher, signal) => {
    const result = await fetcher('/operation', { method: 'POST', signal });
    return result.text();
  });
  await vi.advanceTimersByTimeAsync(200);
  completed = 3; // Fast stages between polls are replayed, never lost.
  await vi.advanceTimersByTimeAsync(200);
  response.resolve(new Response('same output', { headers: { 'X-WG-Operation-Stages': JSON.stringify({ stages, total: 4 }) } }));
  expect(await pending).toBe('same output');
  expect(counts).toEqual([0, 1, 2, 3, 4]);
  const calls = fetcher.mock.calls.length;
  await vi.advanceTimersByTimeAsync(1000);
  expect(fetcher).toHaveBeenCalledTimes(calls);
  expect(activityStore.getSnapshot()).toEqual([]);
  unsubscribe();
});

it('Stop aborts both the operation and an outstanding progress read, without recording a duration', async () => {
  vi.useFakeTimers();
  const signals: AbortSignal[] = [];
  const fetcher = vi.fn<typeof fetch>((_url, init) => new Promise((_resolve, reject) => {
    const signal = init!.signal!;
    signals.push(signal);
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }));
  const pending = stagedActivity({ kind: 'export', label: 'Exporting STL' }, fetcher, undefined,
    async (fetcher, signal) => (await fetcher('/operation', { method: 'POST', signal })).text());
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  await vi.advanceTimersByTimeAsync(200);
  activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(signals).toHaveLength(2);
  expect(signals.every(signal => signal.aborted)).toBe(true);
  expect(activityStore.getSnapshot()).toEqual([]);
  expect(activityStore.typicalMs('export')).toBeNull();
});


it.each([false, true])('Send Stop preserves the server commit decision (committed=%s)', async committed => {
  const response = deferred<Response>();
  let signal: AbortSignal | null | undefined;
  const fetcher = vi.fn<typeof fetch>((_url, init) => {
    if (init?.method === 'DELETE') return Promise.resolve(new Response(null, { status: committed ? 409 : 204 }));
    signal = init?.signal;
    signal?.addEventListener('abort', () => response.reject(signal?.reason), { once: true });
    return response.promise;
  });
  const pending = stagedActivity({ kind: 'send-to-cad', label: 'Sending to CAD' }, fetcher, undefined,
    async (fetcher, signal) => (await fetcher('/send', { method: 'POST', signal })).text());
  const outcome = pending.catch(reason => reason);
  activityStore.getSnapshot()[0].cancel!();
  await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  if (committed) {
    expect(signal?.aborted).toBe(false);
    expect(activityStore.getSnapshot()[0].cancel).toBeNull();
    response.resolve(new Response('Sent to CAD'));
    expect(await outcome).toBe('Sent to CAD');
  } else {
    expect(await outcome).toMatchObject({ name: 'AbortError' });
    expect(signal?.aborted).toBe(true);
  }
});

it.each([200, 503])('Stop during a committed Send response body preserves its outcome (%s)', async status => {
  const body = deferred<string>();
  const reading = vi.fn();
  let signal: AbortSignal | null | undefined;
  const fetcher: typeof fetch = async (_url, init) => {
    if (init?.method === 'DELETE') return new Response(null, { status: 409 });
    signal = init?.signal;
    signal?.addEventListener('abort', () => body.reject(signal?.reason), { once: true });
    return { ok: status === 200, status, headers: new Headers(), text: () => { reading(); return body.promise; } } as Response;
  };
  const pending = stagedActivity({ kind: 'send-to-cad', label: 'Sending to CAD' }, fetcher, undefined,
    async (fetcher, signal) => (await fetcher('/send', { method: 'POST', signal })).text());
  const outcome = pending.catch(reason => reason);
  await vi.waitFor(() => expect(reading).toHaveBeenCalledOnce());
  activityStore.getSnapshot()[0].cancel!();
  await vi.waitFor(() => expect(activityStore.getSnapshot()[0].cancel).toBeNull());
  expect(signal?.aborted).toBe(false);
  body.resolve('Sent to CAD');
  expect(await outcome).toBe('Sent to CAD');
});


it('Stop waits for a queued Send to register before aborting its transport', async () => {
  vi.useFakeTimers();
  const response = deferred<Response>();
  let registered = false;
  let signal: AbortSignal | null | undefined;
  const fetcher: typeof fetch = async (_url, init) => {
    if (init?.method === 'DELETE') return new Response(null, { status: registered ? 204 : 404 });
    if (init?.method !== 'POST') return new Response(null, { status: 404 });
    signal = init.signal;
    signal?.addEventListener('abort', () => response.reject(signal?.reason), { once: true });
    return response.promise;
  };
  const pending = stagedActivity({ kind: 'send-to-cad', label: 'Sending to CAD' }, fetcher, undefined,
    async (fetcher, signal) => (await fetcher('/send', { method: 'POST', signal })).text());
  const outcome = pending.catch(reason => reason);
  activityStore.getSnapshot()[0].cancel!();
  await vi.advanceTimersByTimeAsync(0);
  expect(signal?.aborted).toBe(false);
  registered = true;
  await vi.advanceTimersByTimeAsync(200);
  expect(await outcome).toMatchObject({ name: 'AbortError' });
});
