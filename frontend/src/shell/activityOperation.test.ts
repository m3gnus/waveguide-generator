import { afterEach, expect, it, vi } from 'vitest';
import { activityStore } from './activity';
import { abortableActivity, activityOperation } from './activityOperation';
import { deferred } from './cadlink/selection.fixtures';

const start = { kind: 'export' as const, label: 'Exporting STL' };
const fetcher = vi.fn<typeof fetch>();
afterEach(() => { activityStore.reset(); vi.restoreAllMocks(); });

it('lets a standalone nested stage own progress when its parent has no counter', async () => {
  await activityOperation(start, fetcher, scoped => activityOperation(start, scoped, async (_fetcher, activity) => {
    activity.progress(2, 4);
    expect(activityStore.getSnapshot()[0].progress).toEqual({ done: 2, total: 4 });
  }));
});

it.each([false, true])('shares a composed operation through settlement (failure=%s)', async fail => {
  const waiting = deferred<void>();
  const started = vi.spyOn(activityStore, 'start');
  const pending = activityOperation(start, fetcher, scoped => activityOperation(start, scoped, () => waiting.promise));
  const settled = pending.catch(error => error);
  expect(activityStore.getSnapshot()).toHaveLength(1);
  expect(started).toHaveBeenCalledTimes(1);
  if (fail) waiting.reject(new Error('failed')); else waiting.resolve();
  await settled;
  expect(activityStore.getSnapshot()).toHaveLength(0);
  expect(activityStore.typicalMs('export') === null).toBe(fail);
});

it('keeps unrelated concurrent requests independent and shares duplicate immutable loads until both end', async () => {
  const first = deferred<void>(); const second = deferred<void>();
  const a = activityOperation(start, fetcher, () => first.promise, 'artifact');
  const b = activityOperation(start, fetcher, () => second.promise, 'artifact');
  const independent = deferred<void>();
  const c = activityOperation(start, fetcher, () => independent.promise);
  expect(activityStore.getSnapshot()).toHaveLength(2);
  first.resolve(); await a;
  expect(activityStore.getSnapshot()).toHaveLength(2);
  second.resolve(); await b;
  expect(activityStore.getSnapshot()).toHaveLength(1);
  independent.resolve(); await c;
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

it('exposes Stop only during a cancellable nested stage', async () => {
  const stage = deferred<void>(); const published = deferred<void>();
  const cancel = vi.fn(() => stage.resolve());
  const pending = activityOperation(start, fetcher, async scoped => {
    await activityOperation({ ...start, cancel }, scoped, () => stage.promise);
    await published.promise;
  });
  activityStore.getSnapshot()[0].cancel!();
  await vi.waitFor(() => expect(activityStore.getSnapshot()[0].cancel).toBeNull());
  expect(cancel).toHaveBeenCalledOnce();
  published.resolve(); await pending;
  expect(activityStore.getSnapshot()).toHaveLength(0);
});

it('marks a child failure without ending its parent or remembering a completed duration', async () => {
  const fallback = deferred<void>();
  const pending = activityOperation(start, fetcher, async scoped => {
    await activityOperation(start, scoped, async (_fetcher, activity) => { activity.end(false); });
    await fallback.promise;
  });
  expect(activityStore.getSnapshot()).toHaveLength(1);
  fallback.resolve(); await pending;
  expect(activityStore.getSnapshot()).toHaveLength(0);
  expect(activityStore.typicalMs('export')).toBeNull();
});

it.each([true, false])('registers child keys until the whole shared scope settles (parent first=%s)', async parentFirst => {
  const child = deferred<void>(); const parent = deferred<void>(); const joined = deferred<void>();
  const started = vi.spyOn(activityStore, 'start');
  let childWork!: Promise<void>;
  const pending = activityOperation(start, fetcher, async scoped => {
    childWork = activityOperation(start, scoped, () => child.promise, 'child-artifact');
    await childWork;
    await parent.promise;
  });
  const other = activityOperation(start, fetcher, () => joined.promise, 'child-artifact');
  expect(started).toHaveBeenCalledTimes(1);
  child.resolve(); await childWork;
  if (parentFirst) { parent.resolve(); await pending; }
  else { joined.resolve(); await other; }
  expect(activityStore.getSnapshot()).toHaveLength(1);
  // Even when the keyed child has finished, another reader joins the live scope.
  await activityOperation(start, fetcher, async () => undefined, 'child-artifact');
  expect(started).toHaveBeenCalledTimes(1);
  parent.resolve(); joined.resolve(); await Promise.all([pending, other]);
  expect(activityStore.getSnapshot()).toHaveLength(0);
  // The key must not resurrect the ended scope on the next download.
  await activityOperation(start, fetcher, async () => undefined, 'child-artifact');
  expect(started).toHaveBeenCalledTimes(2);
});

it.each(['caller', 'Stop', 'already aborted'] as const)('aborts the request via %s and removes the activity', async via => {
  const caller = new AbortController();
  if (via === 'already aborted') caller.abort();
  let received: AbortSignal | undefined;
  const pending = abortableActivity(start, fetcher, caller.signal, async (_fetcher, signal) => {
    received = signal;
    return new Promise<void>((_resolve, reject) => {
      const rejectAbort = () => reject(signal.reason);
      if (signal.aborted) rejectAbort();
      else signal.addEventListener('abort', rejectAbort, { once: true });
    });
  });
  const rejected = expect(pending).rejects.toMatchObject({ name: 'AbortError' });
  if (via === 'caller') caller.abort();
  else if (via === 'Stop') activityStore.getSnapshot()[0].cancel!();
  await rejected;
  expect(received?.aborted).toBe(true);
  expect(activityStore.getSnapshot()).toHaveLength(0);
  expect(activityStore.typicalMs('export')).toBeNull();
});
