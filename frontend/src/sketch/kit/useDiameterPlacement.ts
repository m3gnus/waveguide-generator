import { useState } from 'react';
import { supportsDiameter } from '../dimensionValue';
import type { Sketch } from '../model';
import type { CandidateDimension } from '../tools/dimension';

/** Presentation only: the candidate retains its solver radius until committed. */
export function useDiameterPlacement(sketch: Sketch) {
  const [diameter, setDiameter] = useState(false), [shift, setShift] = useState(false);
  return {
    reset() { setDiameter(false); setShift(false); },
    key(key: string) {
      if (key === 'o' || key === 'ø') { setDiameter(value => !value); return true; }
      if (key === 'shift') setShift(true);
      return false;
    },
    release(key: string) { if (key === 'Shift') setShift(false); },
    candidate(candidate: CandidateDimension | undefined, heldShift = shift): CandidateDimension | undefined {
      return candidate && supportsDiameter(sketch, { ...candidate, id: '__dimension_preview' }) && (diameter || heldShift)
        ? { ...candidate, diameter: true } : candidate;
    },
  };
}
