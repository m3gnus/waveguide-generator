import type { JobResults } from '../api/results';
import type { CombineMetadata } from './types';

/**
 * The combined result currently on screen, published by the results dock and
 * read by the pre-solve rail.
 *
 * Two things ride on it. The rail's automatic gains and delays are only
 * useful when the number auto chose can be seen, and that number exists only
 * in a solved result — which the rail has no business fetching for itself.
 * And since the crossover settings live in the rail alone, the rail is also
 * where an edit must reach the shown run: `onApplied` is the dock's own
 * swap-in, handed over so a live recombine repaints without the rail knowing
 * anything about how results are displayed.
 *
 * Deliberately a bare snapshot store rather than another zustand store: it
 * holds one reference, is written from one place, and must not participate in
 * solve-profile persistence.
 */
export interface ShownCombine {
  jobId: string;
  channelId: string;
  combine: CombineMetadata;
  /** Whether the shown run accepts a live recombine: complete, and not a
   * provisional live view that a running solve is still revising. */
  canApply: boolean;
  /** Why it does not, in a sentence the rail can show. Silence would read as
   * "the edit was applied", which is the one thing it must not read as. */
  blockedReason: string | null;
  /** The dock's own "show this run's model", offered when loading it is what
   * unblocks the edit -- the case a reopened session lands in, where the run
   * on screen is this project's but no ingestion is loaded to own it. */
  recall: (() => void) | null;
  onApplied: (jobId: string, updated: JobResults) => void;
}

let current: ShownCombine | null = null;
const listeners = new Set<() => void>();

export const latestCombine = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot(): ShownCombine | null {
    return current;
  },
  publish(value: ShownCombine | null): void {
    if (value === current) return;
    current = value;
    listeners.forEach((listener) => listener());
  },
  reset(): void {
    current = null;
    listeners.clear();
  },
};
