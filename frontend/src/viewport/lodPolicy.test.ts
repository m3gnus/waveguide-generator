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

  it('accepts a low sequence frame after the socket epoch changes', () => {
    const retained = frame(12, 'fine', 900);
    retained.header.epoch = 7;
    const reconnected = frame(12, 'fine', 1);
    reconnected.header.epoch = 8;
    expect(selectPreferredFrame(retained, reconnected)).toBe(reconnected);
  });

  it('accepts a replacement connection even when the server repeats its epoch', () => {
    const retained = { ...frame(12, 'fine', 900), documentLoad: 3, connectionGeneration: 1 };
    retained.header.epoch = 7;
    const reconnected = { ...frame(1, 'coarse', 1), documentLoad: 4, connectionGeneration: 2 };
    reconnected.header.epoch = 7;
    expect(selectPreferredFrame(retained, reconnected)).toBe(reconnected);
    // The generation alone also distinguishes a reconnect of the same document.
    reconnected.documentLoad = retained.documentLoad;
    expect(selectPreferredFrame(retained, reconnected)).toBe(reconnected);
    const lateCoarse = { ...frame(1, 'coarse', 3), documentLoad: 3, connectionGeneration: 2 };
    const fine = { ...frame(1, 'fine', 2), documentLoad: 3, connectionGeneration: 2 };
    expect(selectPreferredFrame(fine, lateCoarse)).toBe(fine);
  });
});
