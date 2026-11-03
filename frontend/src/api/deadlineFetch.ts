/** Safe REST reads have a deadline that also applies while consuming the body.
 * Native signals are used when available; older WebViews use AbortController.
 * Mutation callers retain their existing outcome/reconciliation contract.
 */
export const REST_READ_TIMEOUT_MS = 30_000;
export const PLAN_TIMEOUT_MS = 15_000;

/** Compose lifetimes with explicit disposal after response bodies are consumed. */
export function scopedAbortSignals(inputs: AbortSignal[]): { signal: AbortSignal; dispose: () => void } {
  const signals = [...new Set(inputs)];
  const controller = new AbortController();
  const dispose = () => {
    for (const signal of signals) signal.removeEventListener('abort', abort);
  };
  const abort = () => {
    const source = signals.find(signal => signal.aborted);
    if (!source) return;
    dispose();
    controller.abort(source.reason);
  };
  if (signals.some(signal => signal.aborted)) abort();
  else for (const signal of signals) signal.addEventListener('abort', abort, { once: true });
  return { signal: controller.signal, dispose };
}

export function combineAbortSignals(signals: AbortSignal[]): AbortSignal {
  return typeof AbortSignal.any === 'function' ? AbortSignal.any(signals) : scopedAbortSignals(signals).signal;
}

export function deadlineSignal(signal?: AbortSignal | null, timeoutMs = REST_READ_TIMEOUT_MS): AbortSignal {
  if (typeof AbortSignal.timeout === 'function'
      && (!signal || typeof AbortSignal.any === 'function')) {
    const deadline = AbortSignal.timeout(timeoutMs);
    return signal ? combineAbortSignals([signal, deadline]) : deadline;
  }
  // Keep the timer alive after headers so it still bounds body consumption.
  if (signal?.aborted) return signal;
  const controller = new AbortController();
  const timer = setTimeout(
    () => controller.abort(new DOMException('The request deadline expired', 'TimeoutError')),
    timeoutMs,
  );
  const composed = signal ? scopedAbortSignals([signal, controller.signal]).signal : controller.signal;
  composed.addEventListener('abort', () => clearTimeout(timer), { once: true });
  return composed;
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
