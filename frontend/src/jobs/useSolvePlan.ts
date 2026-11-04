import { postSolvePlan, SolvePlanRefused, solvePlanRequestBody, type SolvePlan, type SolveBlocker } from './actions';
import type { DesignDocument } from '../stores/design';
import type { SolveOptions } from '../stores/solveOptions';
import { failureWords } from '../api/httpError';
import { usePlanQuery } from './usePlanQuery';

export {
  SOLVE_PLAN_DEBOUNCE_MS,
  SOLVE_PLAN_FAULT_RETRIES,
  SOLVE_PLAN_GC_MS,
  SOLVE_PLAN_RECOVERY_MS,
} from './planQueryPolicy';

export interface SolvePlanSnapshot {
  plan: SolvePlan | null;
  error: string | null;
  readiness?: SolveBlocker | null;
  isPending: boolean;
}

function requestBody(
  design: DesignDocument,
  options: SolveOptions | null,
  enabled: boolean,
): string | null {
  // An assembly solves its compiled model; the server refuses it this horn-only plan.
  if (!enabled || options === null || design.assembly) return null;
  return solvePlanRequestBody(design, options);
}

/** Resolve the server's actual plan for the current parametric submission. */
export function useSolvePlan(
  design: DesignDocument,
  options: SolveOptions | null,
  enabled = true,
): SolvePlanSnapshot {
  const currentBody = requestBody(design, options, enabled);
  const query = usePlanQuery(currentBody, 'solve-plan', postSolvePlan);
  return {
    ...query,
    readiness: query.error instanceof SolvePlanRefused ? query.error.readiness : null,
    error: query.error ? failureWords(query.error) : null,
  };
}
