import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { postSolvePlan, SolvePlanRefused, solvePlanRequestBody, type SolvePlan } from './actions';
import type { DesignDocument } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';

import {
  SOLVE_PLAN_DEBOUNCE_MS,
  SOLVE_PLAN_FAULT_RETRIES,
  SOLVE_PLAN_GC_MS,
  SOLVE_PLAN_RECOVERY_MS,
} from './planQueryPolicy';

export {
  SOLVE_PLAN_DEBOUNCE_MS,
  SOLVE_PLAN_FAULT_RETRIES,
  SOLVE_PLAN_GC_MS,
  SOLVE_PLAN_RECOVERY_MS,
};

export interface SolvePlanSnapshot {
  plan: SolvePlan | null;
  error: string | null;
  isPending: boolean;
}

function requestBody(
  design: DesignDocument,
  options: SolveOptions | null,
  enabled: boolean,
): string | null {
  if (!enabled || options === null) return null;
  return solvePlanRequestBody(design, options);
}

/** A refusal is the server's answer and stands; anything else is worth asking again. */
function isFault(error: unknown): boolean {
  return !(error instanceof SolvePlanRefused);
}

/** Resolve the server's actual plan for the current parametric submission. */
export function useSolvePlan(
  design: DesignDocument,
  options: SolveOptions | null,
  enabled = true,
): SolvePlanSnapshot {
  const currentBody = requestBody(design, options, enabled);
  const [settledBody, setSettledBody] = useState<string | null>(currentBody);

  useEffect(() => {
    if (currentBody === null) {
      setSettledBody(null);
      return undefined;
    }
    if (currentBody === settledBody) return undefined;
    const timer = window.setTimeout(
      () => setSettledBody(currentBody),
      SOLVE_PLAN_DEBOUNCE_MS,
    );
    return () => window.clearTimeout(timer);
  }, [currentBody, settledBody]);

  const query = useQuery({
    queryKey: ['solve-plan', settledBody],
    queryFn: ({ signal }) => postSolvePlan(settledBody!, fetch, signal),
    enabled: settledBody !== null,
    staleTime: Infinity,
    gcTime: SOLVE_PLAN_GC_MS,
    retry: (failureCount, error) =>
      failureCount < SOLVE_PLAN_FAULT_RETRIES && isFault(error),
    refetchInterval: (entry) =>
      entry.state.status === 'error' && isFault(entry.state.error)
        ? SOLVE_PLAN_RECOVERY_MS
        : false,
  });
  const currentIsSettled = currentBody !== null && currentBody === settledBody;
  return {
    plan: currentIsSettled ? query.data ?? null : null,
    error: currentIsSettled && query.error
      ? query.error instanceof Error ? query.error.message : String(query.error)
      : null,
    isPending: currentBody !== null && (!currentIsSettled || query.isPending),
  };
}
