import { useEffect, useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';
import { postSymmetry, toSolveDesign } from '../jobs/actions';
import type { DesignDocument } from '../stores/design';

/** Resolving samples the surface, so it must not run on every keystroke. */
const SYMMETRY_DEBOUNCE_MS = 400;

/**
 * Long enough that an editing session never re-resolves a shape it has already
 * seen, finite so a page that outlives a server restart eventually asks the new
 * process rather than trusting the old one's answer.
 */
const SYMMETRY_STALE_MS = 5 * 60_000;

/** How long a superseded payload's answer is kept in case the user comes back. */
const SYMMETRY_GC_MS = 60_000;

/** Hold a value back until it has stopped changing for `delay` milliseconds. */
function useSettled<T>(value: T, delay: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

export interface AutoSymmetryOptions {
  /** False keeps the query idle (no request) while the caller has no use for it. */
  enabled?: boolean;
  /** Keep showing the last answer while a new payload resolves. */
  keepPrevious?: boolean;
}

/**
 * The server's auto-symmetry answer for `design`, shared by every caller.
 *
 * Key on the wire payload, not the revision. Surface sampling costs 57-150 ms
 * per call, and most revisions -- anything the solve design does not carry,
 * plus every undo back to a shape already resolved -- produce bytes we have
 * already answered. React Query then dedupes in flight and caches the rest, so
 * the parameter panel and the viewport share one request per settled edit.
 *
 * The debounce, not the signal, is what bounds server work: the resolver runs
 * in `asyncio.to_thread`, so aborting a superseded request frees the
 * connection and discards the answer but does not stop the thread.
 */
export function useAutoSymmetry(design: DesignDocument, { enabled = true, keepPrevious = false }: AutoSymmetryOptions = {}) {
  const settledDesign = useSettled(design, SYMMETRY_DEBOUNCE_MS);
  const body = useMemo(() => JSON.stringify(toSolveDesign(settledDesign)), [settledDesign]);
  return useQuery({
    queryKey: ['symmetry', body],
    queryFn: ({ signal }) => postSymmetry(body, fetch, signal),
    enabled,
    placeholderData: keepPrevious ? keepPreviousData : undefined,
    staleTime: SYMMETRY_STALE_MS,
    // Only the settled key is ever active, so every superseded payload is
    // inactive and collected a minute later. The cache is bounded by time, not
    // by count: pausing on many distinct values inside one minute does keep
    // them all. Each entry is a ~125-byte resolution, so that is affordable,
    // and a short window is what keeps it so.
    gcTime: SYMMETRY_GC_MS,
    retry: false,
  });
}
