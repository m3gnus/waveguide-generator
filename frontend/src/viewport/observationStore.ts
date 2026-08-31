import { create } from 'zustand';
import type { ResultData } from '../api/results';
import { observationFrameBasisOf, type ObservationFrameBasis, type ObservationPlane } from './observationRig';

/**
 * The observation frame the viewport draws microphones on.
 *
 * Only the frame is held here, and it is published by the run that was solved:
 * origin, axis, and the two transverse vectors. Everything else about the rig
 * -- distance, angles, planes, mouth or throat -- is read live from the solve
 * options, so those fields move the arc as they are edited. See
 * `observationRig.ts` for why the frame itself is never re-derived on the
 * client.
 *
 * A result with no frame clears it: the arc is drawn where a solve measured, or
 * not at all. There is deliberately no fallback that guesses one.
 */
export interface ObservationStore {
  /** Whether the rig is drawn at all. */
  visible: boolean;
  basis: ObservationFrameBasis | null;
  /** Which run the frame came from, for the readout beside the toggle. */
  sourceLabel: string | null;
  /** Hovered microphone, as plane and measured angle. */
  hovered: { plane: ObservationPlane; angleDeg: number } | null;
  setVisible: (visible: boolean) => void;
  toggle: () => void;
  /** Adopt (or clear) the frame published by the currently selected run. */
  adopt: (result: ResultData | undefined, sourceLabel: string | null) => void;
  setHovered: (hovered: ObservationStore['hovered']) => void;
}

function sameBasis(left: ObservationFrameBasis | null, right: ObservationFrameBasis | null): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  return JSON.stringify(left) === JSON.stringify(right);
}

export const useObservationStore = create<ObservationStore>((set, get) => ({
  visible: false,
  basis: null,
  sourceLabel: null,
  hovered: null,
  setVisible: (visible) => set({ visible, ...(visible ? {} : { hovered: null }) }),
  toggle: () => get().setVisible(!get().visible),
  adopt: (result, sourceLabel) => {
    const basis = observationFrameBasisOf(result);
    // Identity-stable: the results dock re-resolves its primary payload on
    // every socket event, and a fresh object here would rebuild the rig
    // geometry -- and drop a hover -- several times a second during a solve.
    if (sameBasis(get().basis, basis) && get().sourceLabel === sourceLabel) return;
    set({ basis, sourceLabel, hovered: null });
  },
  setHovered: (hovered) => {
    const current = get().hovered;
    if (current?.plane === hovered?.plane && current?.angleDeg === hovered?.angleDeg) return;
    set({ hovered });
  },
}));
