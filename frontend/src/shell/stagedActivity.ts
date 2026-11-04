import { combineAbortSignals, deadlineFetch, deadlineSignal } from '../api/deadlineFetch';
import type { StartActivity } from './activity';
import { abortableActivity } from './activityOperation';

// Stages count completed work. Name the step now underway, using the same
// vocabulary for Build, exports and Send rather than another progress surface.
const nextStep: Record<string, string> = {
  locate: 'Verifying returned bytes', verify: 'Returned STEP verified',
  resolve: 'Building geometry', mesh: 'Checking geometry',
  check: 'Writing files', write: 'Files ready', publish: 'Files ready',
};

/** The same request-owned stage channel for Build, geometry exports and Send.
 * Fetch bodies inside work so Stop also covers response body consumption. */
export function stagedActivity<T>(
  start: StartActivity, fetcher: typeof fetch, signal: AbortSignal | undefined,
  work: (fetcher: typeof fetch, signal: AbortSignal) => Promise<T>,
  operationGet?: { method: 'GET'; url: string },
): Promise<T> {
  return abortableActivity(start, fetcher, signal, async (scoped, signal, activity, abort) => {
    const id = crypto.randomUUID();
    const polling = new AbortController();
    let done = 0;
    let active = true;
    let cancelPending = false;
    const cancelPublication = async () => {
      if (cancelPending || !active) return;
      cancelPending = true;
      try {
        while (active) {
          const response = await scoped(`/api/operation-progress/${id}`,
            { method: 'DELETE', signal: deadlineSignal(polling.signal, 1000) });
          if (!active) return;
          if (response.status === 204) { abort(); return; }
          if (response.status === 409) { activity.setCancel(null); return; }
          if (response.status !== 404) return;
          // The POST can still be queued when Stop arrives. Wait for the
          // server to register it; never turn a missing acknowledgement into
          // a claim that a possibly committed operation was stopped.
          await new Promise(resolve => setTimeout(resolve, 200));
        }
      } catch { /* Keep awaiting the result when Stop was not acknowledged. */ }
      finally { cancelPending = false; }
    };
    let timer: ReturnType<typeof setTimeout> | undefined;
    const advance = (count: { stages?: unknown; total?: unknown }) => {
      if (polling.signal.aborted || !Array.isArray(count.stages) || !Number.isInteger(count.total) || Number(count.total) <= 0) return;
      while (done < count.stages.length && done < Number(count.total)) {
        const stage = count.stages[done];
        if (typeof stage === 'string' && nextStep[stage]) activity.label(`${start.label} · ${nextStep[stage]}`);
        activity.progress(++done, Number(count.total));
      }
    };
    const read = async () => {
      try {
        const response = await deadlineFetch(scoped, `/api/operation-progress/${id}`, { signal: polling.signal }, 1000);
        if (!response.ok || polling.signal.aborted) return;
        const count = await response.json() as { stages?: unknown; total?: unknown };
        advance(count);
      } catch { /* Progress is advisory; the operation's response owns errors. */ }
    };
    const poll = () => {
      void read().finally(() => {
        if (!polling.signal.aborted) timer = setTimeout(poll, 200);
      });
    };
    const abortPolling = () => { polling.abort(); clearTimeout(timer); };
    signal.addEventListener('abort', abortPolling, { once: true });
    const countedFetch: typeof fetch = (input, init) => {
      // Preliminary folder reads are ordinary reads. A build-bearing GET must
      // be designated explicitly; POST remains the default operation method.
      const requestSignal = init?.signal ? combineAbortSignals([init.signal, signal]) : signal;
      const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();
      const url = input instanceof Request ? input.url : String(input);
      const operation = operationGet ? method === 'GET' && url === operationGet.url : method === 'POST';
      if (!operation) return scoped(input, { ...init, signal: requestSignal });
      const headers = new Headers(init?.headers ?? (input instanceof Request ? input.headers : undefined));
      headers.set('X-WG-Operation-Id', id);
      if (start.kind === 'send-to-cad' || start.kind === 'assembly-build') {
        activity.setCancel(() => { void cancelPublication(); });
      }
      activity.progress(0, 4);
      const response = scoped(input, { ...init, headers, signal: requestSignal }).then(response => {
        // The operation POST can confirm rollback before DELETE's 204 arrives.
        // Treat that server cancellation as Stop regardless of response order.
        if (response.status === 499) { abort(); signal.throwIfAborted(); }
        // A fast operation can finish between polls. Its final header carries
        // the very same history, without delaying the result for another GET.
        const completed = response.headers.get('X-WG-Operation-Stages');
        if (completed) { try { advance(JSON.parse(completed)); } catch { /* advisory */ } }
        return response;
      });
      timer = setTimeout(poll, 200);
      return response;
    };
    try {
      const result = await work(countedFetch, signal);
      signal.throwIfAborted();
      return result;
    } finally {
      active = false;
      abortPolling();
      signal.removeEventListener('abort', abortPolling);
    }
  });
}
