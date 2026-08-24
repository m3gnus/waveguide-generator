import { useSyncExternalStore } from 'react';
import { combinedChannelId, resultChannels, type ResultPayload } from '../results/types';

/**
 * The combined sum, whatever it is called in a particular run.
 *
 * Stored as a sentinel rather than as the combined channel's id so the view
 * survives a switch between runs whose sums are named differently, and so a
 * run with no sum falls back rather than resolving to a channel that happens
 * to share the name.
 */
export const COMBINED_VIEW: unique symbol = Symbol('combined-result-view');

/** `COMBINED_VIEW`, or the id of one drive channel. */
export type ResultView = string | typeof COMBINED_VIEW;

/** Session-scoped on purpose: which driver you were looking at is a property of
 * the sitting, not of the workspace, and carrying it across restarts would
 * reopen the dock on a channel the next design may not have. */
const STORAGE_KEY = 'wg2.resultView.v1';
const CHANNEL_VIEW_STORAGE_PREFIX = '\u0000channel:';

function readStored(): ResultView {
  try {
    const stored = sessionStorage.getItem(STORAGE_KEY);
    if (!stored || stored === 'combined') return COMBINED_VIEW;
    return stored.startsWith(CHANNEL_VIEW_STORAGE_PREFIX)
      ? stored.slice(CHANNEL_VIEW_STORAGE_PREFIX.length)
      : stored;
  } catch {
    // Private-mode and sandboxed embeddings refuse session storage; the view
    // still works for as long as the tab lives.
    return COMBINED_VIEW;
  }
}

function writeStored(view: ResultView): void {
  try {
    if (view === COMBINED_VIEW) sessionStorage.removeItem(STORAGE_KEY);
    else sessionStorage.setItem(STORAGE_KEY, `${CHANNEL_VIEW_STORAGE_PREFIX}${view}`);
  } catch { /* the in-memory value remains authoritative for this tab */ }
}

class ResultViewStore {
  private value: ResultView = readStored();
  private readonly listeners = new Set<() => void>();
  getSnapshot = (): ResultView => this.value;
  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };
  setView(view: ResultView): void {
    if (!view || view === this.value) return;
    this.value = view;
    writeStored(view);
    this.listeners.forEach((listener) => listener());
  }
  resetForTests(): void {
    this.value = COMBINED_VIEW;
    try { sessionStorage.removeItem(STORAGE_KEY); } catch { /* nothing stored */ }
    this.listeners.forEach((listener) => listener());
  }
}

export const resultViewStore = new ResultViewStore();

export function useResultView(): ResultView {
  return useSyncExternalStore(resultViewStore.subscribe, resultViewStore.getSnapshot, resultViewStore.getSnapshot);
}

/**
 * The channel a run shows under the chosen view.
 *
 * The chosen view first, so looking at MF on one run and clicking the next
 * keeps MF. A run that lacks it falls back to its combined sum, then to its
 * first channel, so a comparison never silently draws nothing — the label
 * carries which channel was substituted. Null only for a run with no channels
 * at all, where the payload itself is the result.
 */
export function resolveResultView(result: ResultPayload | undefined, view: ResultView): string | null {
  if (!result) return null;
  const channels = resultChannels(result);
  if (!channels.length) return null;
  const combined = combinedChannelId(result);
  if (view !== COMBINED_VIEW && channels.some(({ id }) => id === view)) return view;
  return combined ?? channels[0].id;
}
