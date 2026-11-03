import { deadlineFetch } from '../api/deadlineFetch';
import type { StartActivity } from './activity';
import { abortableActivity } from './activityOperation';

/** The same request-owned stage channel for Build, geometry exports and Send.
 * Fetch bodies inside work so Stop also covers response body consumption. */
export function stagedActivity<T>(
  start: StartActivity, fetcher: typeof fetch, signal: AbortSignal | undefined,
  work: (fetcher: typeof fetch, signal: AbortSignal) => Promise<T>,
): Promise<T> {
  return abortableActivity(start, fetcher, signal, async (scoped, signal, activity) => {
    const id = crypto.randomUUID();
    const polling = new AbortController();
    let done = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const advance = (count: { stages?: unknown; total?: unknown }) => {
      if (polling.signal.aborted || !Array.isArray(count.stages) || !Number.isInteger(count.total) || Number(count.total) <= 0) return;
      while (done < count.stages.length && done < Number(count.total)) {
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
      // Preliminary folder reads are ordinary reads; only the operation POST
      // starts a channel. One id belongs to exactly one POST.
      if (init?.method?.toUpperCase() !== 'POST') return scoped(input, { ...init, signal });
      const headers = new Headers(init.headers);
      headers.set('X-WG-Operation-Id', id);
      activity.progress(0, 4);
      const response = scoped(input, { ...init, headers, signal }).then(response => {
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
      abortPolling();
      signal.removeEventListener('abort', abortPolling);
    }
  });
}
