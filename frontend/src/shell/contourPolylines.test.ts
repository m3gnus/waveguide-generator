import { describe, expect, it } from 'vitest';
import { contourPolylines } from './ResultsPanel';

type Segment = [number, number, number, number];

/** The original quadratic implementation, kept as the correctness oracle. */
function referenceJoin(segments: Segment[]): [number, number][][] {
  const unused = new Set(segments.map((_segment, index) => index));
  const close = (a: [number, number], b: [number, number]) =>
    Math.abs(a[0] - b[0]) < 1e-7 && Math.abs(a[1] - b[1]) < 1e-7;
  const polylines: [number, number][][] = [];
  while (unused.size) {
    const first = unused.values().next().value as number;
    unused.delete(first);
    const segment = segments[first];
    const points: [number, number][] = [[segment[0], segment[1]], [segment[2], segment[3]]];
    let joined = true;
    while (joined) {
      joined = false;
      for (const index of unused) {
        const candidate = segments[index];
        const start: [number, number] = [candidate[0], candidate[1]];
        const end: [number, number] = [candidate[2], candidate[3]];
        if (close(points.at(-1)!, start)) points.push(end);
        else if (close(points.at(-1)!, end)) points.push(start);
        else if (close(points[0], end)) points.unshift(start);
        else if (close(points[0], start)) points.unshift(end);
        else continue;
        unused.delete(index);
        joined = true;
        break;
      }
    }
    polylines.push(points);
  }
  return polylines;
}

/** Deterministic pseudo-random shuffle, so a failure reproduces exactly. */
function shuffle<T>(items: T[], seed: number): T[] {
  const out = [...items];
  let state = seed;
  for (let i = out.length - 1; i > 0; i -= 1) {
    state = (state * 1664525 + 1013904223) >>> 0;
    const j = state % (i + 1);
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

function openChain(length: number, offset = 0): Segment[] {
  return Array.from({ length }, (_unused, i): Segment => [
    i + offset, offset, i + 1 + offset, offset,
  ]);
}

describe('contourPolylines', () => {
  it('joins a chain into one polyline regardless of input order', () => {
    const chain = openChain(6);
    const joined = contourPolylines(shuffle(chain, 7));
    expect(joined).toHaveLength(1);
    expect(joined[0]).toHaveLength(7);
    const xs = joined[0].map(([x]) => x);
    // Either direction is a correct traversal of an undirected chain.
    expect(xs[0] === 0 || xs[0] === 6).toBe(true);
    expect(new Set(xs).size).toBe(7);
  });

  it('keeps disjoint chains apart', () => {
    const segments = [...openChain(3), ...openChain(3, 100)];
    const joined = contourPolylines(shuffle(segments, 11));
    expect(joined).toHaveLength(2);
    expect(joined.map((line) => line.length).sort()).toEqual([4, 4]);
  });

  it('closes a loop without losing or duplicating segments', () => {
    const square: Segment[] = [
      [0, 0, 1, 0],
      [1, 0, 1, 1],
      [1, 1, 0, 1],
      [0, 1, 0, 0],
    ];
    const joined = contourPolylines(shuffle(square, 3));
    expect(joined).toHaveLength(1);
    expect(joined[0]).toHaveLength(5);
    expect(joined[0][0]).toEqual(joined[0][4]);
  });

  it('matches the previous implementation on a large shuffled set', () => {
    // The old join rescanned every remaining segment per step: 62 ms at 3,000
    // segments, four times per heatmap. The replacement must be a pure speed-up.
    const segments = shuffle([...openChain(400), ...openChain(400, 5000)], 42);
    const fast = contourPolylines(segments);
    const reference = referenceJoin(segments);
    const shape = (lines: [number, number][][]) =>
      lines.map((line) => line.length).sort((a, b) => a - b);
    expect(shape(fast)).toEqual(shape(reference));
    const pointCount = (lines: [number, number][][]) =>
      lines.reduce((total, line) => total + line.length, 0);
    expect(pointCount(fast)).toBe(pointCount(reference));
  });

  it('returns nothing for no segments', () => {
    expect(contourPolylines([])).toEqual([]);
  });
});
