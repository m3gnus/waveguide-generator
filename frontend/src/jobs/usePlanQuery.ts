import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { SolvePlanRefused } from './actions';
import {
  SOLVE_PLAN_DEBOUNCE_MS,
  SOLVE_PLAN_FAULT_RETRIES,
  SOLVE_PLAN_GC_MS,
  SOLVE_PLAN_RECOVERY_MS,
} from './planQueryPolicy';

/** A refusal is the server's answer and stands; anything else is worth asking again. */
function isFault(error: unknown): boolean {
  return !(error instanceof SolvePlanRefused);
}

/** Debounce plan requests, recover faults, and hide answers for superseded bodies. */
export function usePlanQuery<T>(
  currentBody: string | null,
  keyPrefix: string,
  request: (body: string, fetcher: typeof fetch, signal?: AbortSignal) => Promise<T>,
  identity: readonly unknown[] = [],
) {
  const [settledBody, setSettledBody] = useState<string | null>(currentBody);
  useEffect(() => {
    if (currentBody === null) {
      setSettledBody(null);
      return undefined;
    }
    if (currentBody === settledBody) return undefined;
    const timer = window.setTimeout(() => setSettledBody(currentBody), SOLVE_PLAN_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [currentBody, settledBody]);

  const query = useQuery({
    queryKey: [keyPrefix, settledBody, ...identity],
    queryFn: ({ signal }) => request(settledBody!, fetch, signal),
    enabled: settledBody !== null,
    staleTime: Infinity,
    gcTime: SOLVE_PLAN_GC_MS,
    retry: (failureCount, error) => failureCount < SOLVE_PLAN_FAULT_RETRIES && isFault(error),
    refetchInterval: (entry) =>
      entry.state.status === 'error' && isFault(entry.state.error)
        ? SOLVE_PLAN_RECOVERY_MS
        : false,
  });
  const currentIsSettled = currentBody !== null && currentBody === settledBody;
  return {
    plan: currentIsSettled ? query.data ?? null : null,
    error: currentIsSettled ? query.error : null,
    isPending: currentBody !== null && (!currentIsSettled || query.isPending),
  };
}
