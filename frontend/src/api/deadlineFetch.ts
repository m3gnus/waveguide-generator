/** Safe REST reads have a deadline that also applies while consuming the body.
 * Native timeout signals do not keep the page alive and preserve TimeoutError.
 * Mutation callers retain their existing outcome/reconciliation contract.
 */
export const REST_READ_TIMEOUT_MS = 30_000;
export const PLAN_TIMEOUT_MS = 15_000;

export function deadlineSignal(signal?: AbortSignal | null, timeoutMs = REST_READ_TIMEOUT_MS): AbortSignal {
  const deadline = AbortSignal.timeout(timeoutMs);
  return signal ? AbortSignal.any([signal, deadline]) : deadline;
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
