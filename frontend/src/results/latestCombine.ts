import type { CombineMetadata } from './types';

/**
 * The combine record of the result currently on screen, published by the
 * results dock and read by the pre-solve rail.
 *
 * The rail's crossover fields have automatic gains and delays, and "auto" is
 * only useful when the number it chose can be seen. That number exists only in
 * a solved result, which the rail has no business fetching for itself — so the
 * dock, which already holds it, hands it over here. Null while nothing
 * combined is shown, and the rail simply says "auto" then.
 *
 * Deliberately a bare snapshot store rather than another zustand store: it
 * holds one reference, is written from one place, and must not participate in
 * solve-profile persistence.
 */
let current: CombineMetadata | null = null;
const listeners = new Set<() => void>();

export const latestCombine = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => { listeners.delete(listener); };
  },
  getSnapshot(): CombineMetadata | null {
    return current;
  },
  publish(value: CombineMetadata | null): void {
    if (value === current) return;
    current = value;
    listeners.forEach((listener) => listener());
  },
  reset(): void {
    current = null;
    listeners.clear();
  },
};
