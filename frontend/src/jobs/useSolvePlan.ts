import { useEffect, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { postSolvePlan, solvePlanRequestBody, type SolvePlan } from './actions';
import type { DesignDocument } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';

export const SOLVE_PLAN_DEBOUNCE_MS = 150;
export const SOLVE_PLAN_GC_MS = 60_000;

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
    retry: false,
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
