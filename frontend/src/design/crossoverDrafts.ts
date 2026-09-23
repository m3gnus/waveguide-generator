import { useSyncExternalStore } from 'react';

// Editor drafts are intentionally outside the saved CAD settings. Solve needs
// to see an invalid draft even when the field has lost focus.
const errors = new Map<string, string>();
const listeners = new Set<() => void>();
let firstError: string | null = null;

export function setCrossoverDraftError(id: string, error: string | null): void {
  if (error) errors.set(id, error);
  else errors.delete(id);
  const next = errors.values().next().value ?? null;
  if (next === firstError) return;
  firstError = next;
  listeners.forEach((listener) => listener());
}

export function getCrossoverDraftError(): string | null { return firstError; }

export function useCrossoverDraftError(): string | null {
  return useSyncExternalStore(
    (listener) => { listeners.add(listener); return () => listeners.delete(listener); },
    getCrossoverDraftError,
    () => null,
  );
}
