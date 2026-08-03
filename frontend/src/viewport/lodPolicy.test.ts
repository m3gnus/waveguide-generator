import { describe, expect, it } from 'vitest';
import type { DecodedFrame } from '../api/frame';
import { selectPreferredFrame } from './lodPolicy';

function frame(revision: number, lod: 'coarse' | 'fine', seq: number): DecodedFrame {
  return {
    header: { v: 1, kind: 'preview', designRevision: revision, lod, seq, surfaces: [], sections: [] },
    sections: {},
  };
}

describe('selectPreferredFrame', () => {
  it('silently swaps fine after coarse for the same revision', () => {
    const coarse = frame(7, 'coarse', 10);
    const fine = frame(7, 'fine', 11);
    expect(selectPreferredFrame(coarse, fine)).toBe(fine);
  });

  it('never replaces fine with coarse for the same revision', () => {
    const fine = frame(7, 'fine', 11);
    const lateCoarse = frame(7, 'coarse', 12);
    expect(selectPreferredFrame(fine, lateCoarse)).toBe(fine);
  });

  it('accepts the newest revision even when it starts coarse', () => {
    const fine = frame(7, 'fine', 11);
    const next = frame(8, 'coarse', 12);
    expect(selectPreferredFrame(fine, next)).toBe(next);
  });
});
