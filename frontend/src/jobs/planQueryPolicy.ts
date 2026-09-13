/**
 * How the solve plans are asked for: the parametric plan (`useSolvePlan`) and
 * the imported plan (`useImportedSolvePlan`) follow the same policy.
 */

export const SOLVE_PLAN_DEBOUNCE_MS = 150;
export const SOLVE_PLAN_GC_MS = 60_000;

/**
 * How often an unanswered plan asks again.
 *
 * Solve is disabled whenever a plan query has no plan, so a single unanswered
 * request used to disable it for the rest of the session: the key is the
 * request body, `staleTime` is Infinity, and nothing refetched on a timer. The
 * socket reconnecting invalidates the query (`useCapabilityRefreshOnReconnect`),
 * but that only fires when the socket comes *back*; a backend that stays down,
 * or one that answers the socket while failing this route, never produces the
 * signal. So the query heals itself.
 *
 * Only faults are retried, never refusals -- see `SolvePlanRefused`. Polling a
 * 422 the server will keep giving would be noise, and it would hide the message
 * the user actually needs behind a pending state.
 */
export const SOLVE_PLAN_RECOVERY_MS = 5_000;

/** One immediate retry absorbs the blip; the interval above covers the rest. */
export const SOLVE_PLAN_FAULT_RETRIES = 1;
