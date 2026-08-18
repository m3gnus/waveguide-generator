import { create } from 'zustand';
import type { MeasuredPoint, MeasuredTrace } from '../results/measuredTrace';

export interface MeasuredOverlay {
  id: string;
  label: string;
  points: MeasuredPoint[];
  /** Applied on the chart, not on the parsed points: gain matching is a
   * viewing decision and the file on disk stays the record of what was measured. */
  offsetDb: number;
  visible: boolean;
}

/**
 * A comparison chart stops being readable long before this, and each overlay
 * can carry thousands of points; the SPL card is not a measurement library.
 */
export const MAX_MEASURED_OVERLAYS = 4;

interface MeasuredOverlayStore {
  overlays: MeasuredOverlay[];
  /** Returns the stored overlay, or null when the limit is already reached. */
  add(trace: MeasuredTrace): MeasuredOverlay | null;
  remove(id: string): void;
  setOffsetDb(id: string, offsetDb: number): void;
  toggleVisible(id: string): void;
  clear(): void;
}

let counter = 0;

/**
 * Session-scoped on purpose, and deliberately not persisted.
 *
 * The overlay is a reference to a file the app never copied. Restoring a
 * remembered curve after a reload would put a measurement on the chart that
 * nothing on disk still backs -- and the file may have been re-measured or
 * re-exported in between. Loading it again is one click.
 */
export const useMeasuredOverlayStore = create<MeasuredOverlayStore>((set, get) => ({
  overlays: [],
  add(trace) {
    if (get().overlays.length >= MAX_MEASURED_OVERLAYS) return null;
    counter += 1;
    const overlay: MeasuredOverlay = {
      id: `measured-${counter}`,
      label: trace.label,
      points: trace.points,
      offsetDb: 0,
      visible: true,
    };
    set((state) => ({ overlays: [...state.overlays, overlay] }));
    return overlay;
  },
  remove(id) {
    set((state) => ({ overlays: state.overlays.filter((overlay) => overlay.id !== id) }));
  },
  setOffsetDb(id, offsetDb) {
    if (!Number.isFinite(offsetDb)) return;
    set((state) => ({
      overlays: state.overlays.map((overlay) => overlay.id === id ? { ...overlay, offsetDb } : overlay),
    }));
  },
  toggleVisible(id) {
    set((state) => ({
      overlays: state.overlays.map((overlay) => overlay.id === id ? { ...overlay, visible: !overlay.visible } : overlay),
    }));
  },
  clear() {
    set({ overlays: [] });
  },
}));
