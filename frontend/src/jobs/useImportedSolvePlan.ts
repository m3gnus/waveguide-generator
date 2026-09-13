import { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  importedSolvePlanRequestBody,
  postImportedSolvePlan,
  SolvePlanRefused,
  type ImportedSolvePlan,
} from './actions';
import { buildImportedSubmission } from './importedSubmission';
import { useCapabilities } from './useCapabilities';
import {
  SOLVE_PLAN_DEBOUNCE_MS,
  SOLVE_PLAN_FAULT_RETRIES,
  SOLVE_PLAN_GC_MS,
  SOLVE_PLAN_RECOVERY_MS,
} from './planQueryPolicy';
import { useCadReturnStore } from '../stores/cadReturn';
import { useSolveOptionsStore } from '../stores/solveOptions';

export interface ImportedSolvePlanSnapshot {
  plan: ImportedSolvePlan | null;
  error: string | null;
  isPending: boolean;
}

/** A refusal is the server's answer and stands; anything else is worth asking again. */
function isFault(error: unknown): boolean {
  return !(error instanceof SolvePlanRefused);
}

/**
 * The server's verdict, per engine, on the CAD return the user would solve now.
 *
 * Imported geometry takes the same engine choice as a parametric design, and
 * which engines can take this particular return -- its domain, its features,
 * its frame -- is the server's call (`POST /api/solve/imported-plan`). The
 * solver selector, the Solve control, the status bar and the CAD Link notice
 * all read this one answer rather than each re-deriving it here.
 *
 * The request is exactly the submission `buildImportedSubmission` would send,
 * so the plan is keyed by the ingested record and by the user's current
 * choices. The capability snapshot is part of the key as well: an engine
 * becoming available -- BEAT's CPU runtime finishing its preparation -- changes
 * the answer without anything the user did.
 */
export function useImportedSolvePlan(enabled: boolean): ImportedSolvePlanSnapshot {
  const cadReturn = useCadReturnStore();
  const solveOptions = useSolveOptionsStore();
  const { engines } = useCapabilities();
  const currentBody = useMemo(() => {
    if (!enabled || !cadReturn.ingestRecord) return null;
    try {
      return importedSolvePlanRequestBody(buildImportedSubmission(cadReturn));
    } catch {
      // A submission that cannot be built has its own blocker on screen; there
      // is nothing to plan until it is fixed.
      return null;
    }
    // `solveOptions` is read inside buildImportedSubmission, from the store.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, cadReturn, solveOptions]);
  const capabilityKey = engines
    .map((engine) => `${engine.name}:${engine.available ? 1 : 0}`)
    .join(',');
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
    queryKey: ['imported-solve-plan', settledBody, capabilityKey],
    queryFn: ({ signal }) => postImportedSolvePlan(settledBody!, fetch, signal),
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
    error: currentIsSettled && query.error
      ? query.error instanceof Error ? query.error.message : String(query.error)
      : null,
    isPending: currentBody !== null && (!currentIsSettled || query.isPending),
  };
}
