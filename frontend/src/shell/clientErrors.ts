import { reportClientError } from '../api/diagnostics';

/**
 * Interface errors, reported to the backend so a problem report can carry them.
 *
 * Two kinds of failure never reach the server otherwise: a throw outside the
 * React tree, and a rejected promise nobody awaited. Both leave a working
 * window and a broken feature, which is the shape of the reports that are
 * hardest to act on -- "it just does nothing" with an empty log.
 *
 * Bounded twice, because the failure being reported is often a loop. The same
 * message is reported once per session, and no session reports more than a
 * couple of dozen; the backend enforces its own limit, and this one exists so
 * a render loop does not spend the session issuing requests.
 */

const SESSION_LIMIT = 20;

const seen = new Set<string>();
let sent = 0;

export function resetClientErrorReporting(): void {
  seen.clear();
  sent = 0;
}

export function reportInterfaceError(
  entry: { message: string; stack?: string; source: string },
  report: typeof reportClientError = reportClientError,
): boolean {
  const message = entry.message?.trim();
  if (!message) return false;
  // Deduplicate on message and origin rather than on the stack: a repeating
  // failure produces one message from many stacks, and the first stack is as
  // diagnostic as the hundredth.
  const key = `${entry.source}:${message}`;
  if (seen.has(key) || sent >= SESSION_LIMIT) return false;
  seen.add(key);
  sent += 1;
  void report({
    message,
    stack: entry.stack,
    source: entry.source,
    at: new Date().toISOString(),
  });
  return true;
}

/** Attach the window-level handlers. Returns a function that detaches them. */
export function startClientErrorReporting(target: Window = window): () => void {
  const onError = (event: ErrorEvent) => {
    reportInterfaceError({
      message: event.message || String(event.error ?? 'Unknown error'),
      stack: event.error instanceof Error ? event.error.stack : undefined,
      source: 'window',
    });
  };
  const onRejection = (event: PromiseRejectionEvent) => {
    const reason: unknown = event.reason;
    reportInterfaceError({
      message: reason instanceof Error ? reason.message : String(reason),
      stack: reason instanceof Error ? reason.stack : undefined,
      source: 'promise',
    });
  };
  target.addEventListener('error', onError);
  target.addEventListener('unhandledrejection', onRejection);
  return () => {
    target.removeEventListener('error', onError);
    target.removeEventListener('unhandledrejection', onRejection);
  };
}
