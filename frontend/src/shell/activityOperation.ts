import { activityStore, type ActivityHandle, type StartActivity } from './activity';

interface Scope {
  handle: ActivityHandle;
  active: boolean;
  pending: number;
  completed: boolean;
  cancels: Set<() => void>;
}
const scopes = new WeakMap<typeof fetch, Scope>();
const sharedScopes = new Map<string, Scope>();

/** Pass the scoped fetcher to composed steps. Independent operations using the
 * original fetcher remain independent. A key coalesces duplicate viewport loads
 * of the same immutable artifact, just as their existing request cache does. */
export async function activityOperation<T>(
  start: StartActivity,
  fetcher: typeof fetch,
  work: (fetcher: typeof fetch, activity: ActivityHandle) => Promise<T>,
  key?: string,
): Promise<T> {
  const parent = scopes.get(fetcher);
  const shared = parent?.active ? parent : key ? sharedScopes.get(key) : undefined;
  const scope = shared ?? {
    handle: activityStore.start(start), active: true, pending: 0, completed: true, cancels: new Set<() => void>(),
  };
  if (key && !shared) sharedScopes.set(key, scope);
  const updateCancel = () => scope.handle.setCancel(scope.cancels.size ? () => {
    for (const cancel of scope.cancels) cancel();
  } : null);
  scope.pending += 1;
  if (start.cancel) { scope.cancels.add(start.cancel); updateCancel(); }
  const scopedFetch: typeof fetch = (...args) => fetcher(...args);
  scopes.set(scopedFetch, scope);
  try { return await work(scopedFetch, scope.handle); }
  catch (error) { scope.completed = false; throw error; }
  finally {
    if (start.cancel) { scope.cancels.delete(start.cancel); updateCancel(); }
    scope.pending -= 1;
    if (!scope.pending) {
      scope.active = false;
      scope.handle.end(scope.completed);
      if (key && sharedScopes.get(key) === scope) sharedScopes.delete(key);
    }
  }
}

/** Only for endpoints that already honour an aborted request. Keep query or
 * caller cancellation connected, including during response body consumption. */
export async function abortableActivity<T>(
  start: StartActivity, fetcher: typeof fetch, signal: AbortSignal | undefined,
  work: (fetcher: typeof fetch, signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort(signal?.reason);
  if (signal?.aborted) abort();
  else signal?.addEventListener('abort', abort, { once: true });
  try {
    return await activityOperation({ ...start, cancel: () => controller.abort() }, fetcher,
      (scopedFetch) => work(scopedFetch, controller.signal));
  } finally { signal?.removeEventListener('abort', abort); }
}
