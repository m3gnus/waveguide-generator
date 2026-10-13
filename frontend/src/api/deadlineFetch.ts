/** Safe REST reads have a deadline that also applies while consuming the body.
 * Native signals are used when available; older WebViews use AbortController.
 * Mutation callers retain their existing outcome/reconciliation contract.
 */
export const REST_READ_TIMEOUT_MS = 30_000;
export const PLAN_TIMEOUT_MS = 15_000;

export function deadlineSignal(signal?: AbortSignal | null, timeoutMs = REST_READ_TIMEOUT_MS): AbortSignal {
  if (typeof AbortSignal.timeout === 'function'
      && (!signal || typeof AbortSignal.any === 'function')) {
    const deadline = AbortSignal.timeout(timeoutMs);
    return signal ? AbortSignal.any([signal, deadline]) : deadline;
  }
  // macOS WebViews can provide AbortController without the newer static
  // AbortSignal helpers. Keep the timer alive after headers so it still bounds
  // body consumption; remove listeners and the timer on the first abort.
  const controller = new AbortController();
  if (signal?.aborted) {
    controller.abort(signal.reason);
    return controller.signal;
  }
  const abort = (reason: unknown) => {
    clearTimeout(timer);
    signal?.removeEventListener('abort', lifetimeEnded);
    if (!controller.signal.aborted) controller.abort(reason);
  };
  const lifetimeEnded = () => abort(signal?.reason);
  const timer = setTimeout(
    () => abort(new DOMException('The request deadline expired', 'TimeoutError')),
    timeoutMs,
  );
  signal?.addEventListener('abort', lifetimeEnded, { once: true });
  return controller.signal;
}

export function deadlineFetch(
  fetcher: typeof fetch,
  input: RequestInfo | URL,
  init?: RequestInit,
  timeoutMs = REST_READ_TIMEOUT_MS,
): Promise<Response> {
  const request = typeof Request !== 'undefined' && input instanceof Request ? input : null;
  const method = (init?.method ?? request?.method ?? 'GET').toUpperCase();
  if (method !== 'GET' && method !== 'HEAD') return fetcher(input, init);
  return fetcher(input, { ...init, signal: deadlineSignal(init?.signal === undefined ? request?.signal : init.signal, timeoutMs) });
}
