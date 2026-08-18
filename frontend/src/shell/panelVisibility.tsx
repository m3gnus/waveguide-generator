import { createContext, useCallback, useContext, useRef, useSyncExternalStore } from 'react';

/**
 * A panel's own answer to "is anything I draw actually on screen?".
 *
 * Declared as a plain interface rather than a dockview import so the charts can
 * be driven from an ordinary object in tests, and so nothing under results/ has
 * to know which dock it was mounted in.
 */
export interface PanelVisibility {
  readonly isVisible: boolean;
  subscribe(listener: () => void): () => void;
}

/** The part of dockview's panel API this needs, restated so the adapter can be
 * type-checked against a stub as well as against the real dock. */
export interface DockviewVisibilitySource {
  readonly isVisible: boolean;
  readonly onDidVisibilityChange: (listener: (event: { isVisible: boolean }) => void) => { dispose(): void };
}

/**
 * Adapt a dockview panel API to the seam above.
 *
 * `isVisible` is read through a getter rather than captured, because the API
 * object is long-lived and mutates in place; capturing the boolean would pin
 * the panel to whatever it happened to be at mount.
 */
export function dockviewPanelVisibility(api: DockviewVisibilitySource): PanelVisibility {
  return {
    get isVisible() {
      return api.isVisible;
    },
    subscribe(listener) {
      const subscription = api.onDidVisibilityChange(() => listener());
      return () => subscription.dispose();
    },
  };
}

export const PanelVisibilityContext = createContext<PanelVisibility | null>(null);

const ALWAYS_VISIBLE = () => true;
const NEVER_CHANGES = () => () => { /* nothing outside a dock can hide this */ };

/**
 * Whether the surrounding dock panel is on screen.
 *
 * dockview reports `isVisible: false` both for a panel whose group is collapsed
 * and for one that is merely behind a sibling tab. The second case is the one
 * the charts care about and the one no DOM measurement finds: a covered panel
 * keeps its layout box and its size, so `offsetParent`, `getBoundingClientRect`
 * and IntersectionObserver all still call it on screen.
 *
 * Outside a provider this is always true. A chart rendered on its own -- in the
 * expanded detail dialog, or in a test -- has nothing covering it, and the
 * first render must never be the one that gets held back.
 */
export function usePanelVisible(): boolean {
  const source = useContext(PanelVisibilityContext);
  const subscribe = useCallback(
    (listener: () => void) => source ? source.subscribe(listener) : NEVER_CHANGES(),
    [source],
  );
  const snapshot = useCallback(() => source ? source.isVisible : true, [source]);
  return useSyncExternalStore(subscribe, snapshot, ALWAYS_VISIBLE);
}

/**
 * Hold a covered panel's rendered output still, and catch up once on reveal.
 *
 * Data keeps arriving for a panel nobody can see -- a live solve publishes a
 * new snapshot roughly every 250 ms -- and every one of those used to rebuild
 * the full chart set behind a covered tab, including the directivity map's
 * 180k-cell interpolation and marching-squares contour pass, only to throw the
 * frame away. Returning the previous value keeps the element identity React
 * needs to skip that subtree entirely.
 *
 * Nothing is buffered and nothing is refetched: the caller goes on computing
 * from the newest data it has, and the render that follows the visibility flip
 * simply passes the current value through. So a panel covered across fifty
 * updates repaints exactly once, from the fiftieth.
 *
 * The seed makes the mount case right in both directions -- a panel that mounts
 * already covered still draws once, rather than staying blank until its tab is
 * touched.
 */
export function useVisibleRedraw<T>(value: T): T {
  const visible = usePanelVisible();
  const shown = useRef(value);
  if (visible) shown.current = value;
  return shown.current;
}
