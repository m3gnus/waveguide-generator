// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { importedSketch } from '../ops';
import type { Reference } from '../adapter';
import { candidateDimension, dimensionPicks, hitDimension } from './dimension';

const sketch = importedSketch([
  { id: 'p', x: 2, y: 3 }, { id: 'q', x: 10, y: 9 }, { id: 'r', x: 2, y: 9 },
  { id: 'u', x: 2, y: 13 }, { id: 'v', x: 10, y: 19 },
  { id: 'origin', x: 0, y: 0 }, { id: 'up', x: 0, y: 1 }, { id: 'right', x: 1, y: 0 },
  { id: 'center', x: 20, y: 5 }, { id: 'start', x: 25, y: 5 }, { id: 'end', x: 20, y: 10 },
], [
  { kind: 'line', id: 'a', p1: 'p', p2: 'q' }, { kind: 'line', id: 'b', p1: 'q', p2: 'r' },
  { kind: 'line', id: 'parallel', p1: 'u', p2: 'v' },
  { kind: 'line', id: 'axis', p1: 'origin', p2: 'up', construction: true, reference: true },
  { kind: 'line', id: 'baffle', p1: 'origin', p2: 'right', construction: true, reference: true },
  { kind: 'arc', id: 'arc', center: 'center', start: 'start', end: 'end', ccw: true },
]);
const references: Reference[] = [{ id: 'axis', kind: 'axis' }, { id: 'baffle', kind: 'baffle' }];
const candidate = (ids: string[], cursor = { x: -4, y: 14 }) => candidateDimension(dimensionPicks(sketch, ids, references), cursor);

describe('dimension candidate policy', () => {
  it.each([['a'], ['p', 'q']])('switches cursor regions for %j, with exact placements', (...ids) => {
    expect(candidate(ids)).toMatchObject({ kind: 'distance', a: 'p', b: 'q', value: 10 });
    for (const y of [-10, 22]) expect(candidate(ids, { x: 6, y })).toMatchObject({ kind: 'distanceX', value: 8, placement: { along: .5, offset: y - 6 } });
    for (const x of [-10, 22]) expect(candidate(ids, { x, y: 6 })).toMatchObject({ kind: 'distanceY', value: 6, placement: { along: .5, offset: x - 6 } });
    expect(candidate(ids, { x: 6, y: 6 })).toMatchObject({ kind: 'distance' });
  });
  it.each([['r', 'a'], ['a', 'r']])('measures point to supporting line in either pick order: %j', (...ids) => {
    expect(candidate(ids)).toMatchObject({ kind: 'pointLineDistance', point: 'r', line: 'a', value: 4.8, placement: { along: expect.any(Number), offset: expect.any(Number) } });
  });
  it.each([['p', 'axis', 'distanceX', 'r', 2], ['p', 'baffle', 'distanceY', 'z', 3]] as const)('measures %s to %s with fixed origin', (point, line, kind, label, value) => {
    for (const ids of [[point, line], [line, point]]) expect(candidate(ids)).toMatchObject({ kind, label, value, a: 'origin', b: point, driving: true });
  });
  it('uses positive projected magnitudes, including depth below the baffle', () => {
    expect(candidate(['q', 'p'], { x: 6, y: 22 })).toMatchObject({ kind: 'distanceX', a: 'p', b: 'q', value: 8 });
    const below = { kind: 'point' as const, id: 'below', point: { id: 'below', x: 4, y: -6 } };
    expect(candidateDimension([below, ...dimensionPicks(sketch, ['baffle'], references)], { x: 8, y: -2 })).toMatchObject({ kind: 'distanceY', a: 'below', b: 'origin', value: 6, label: 'z' });
    expect(candidate(['b', 'a'])).toMatchObject({ kind: 'angle', value: expect.any(Number) });
    expect(candidate(['b', 'a'])!.value).toBeGreaterThan(0);
  });
  it.each([['p', 'a'], ['a', 'q']])('rejects a line endpoint as a zero point-line distance: %j', (...ids) => {
    expect(candidate(ids)).toBeUndefined();
  });
  it.each([false, true])('keeps a shallow line aligned along its normal (reversed: %s)', reverse => {
    const a = { id: 'a', x: 0, y: 0 }, b = { id: 'b', x: 10, y: 1 };
    const picks = [{ kind: 'line' as const, id: 'line', a: reverse ? b : a, b: reverse ? a : b }];
    for (const sign of [-1, 1]) {
      expect(candidateDimension(picks, { x: 5 - sign, y: .5 + sign * 10 })).toMatchObject({ kind: 'distance', value: Math.sqrt(101) });
    }
    expect(candidateDimension(picks, { x: 5, y: 70 })).toMatchObject({ kind: 'distanceX', value: 10 });
    expect(candidateDimension(picks, { x: 15, y: .5 })).toMatchObject({ kind: 'distanceY', value: 1 });
  });
  it('measures parallel lines and an arc radius', () => {
    expect(candidate(['a', 'parallel'])).toMatchObject({ kind: 'pointLineDistance', point: 'u', line: 'a', value: 8 });
    expect(candidate(['arc'], { x: 20, y: 13 })).toMatchObject({ kind: 'radius', arc: 'arc', value: 5, placement: { angle: Math.PI / 2, offset: 3 } });
  });
  it('places a two-line angle in every cursor quadrant', () => {
    for (const cursor of [{ x: 16, y: 15 }, { x: 4, y: 15 }, { x: 4, y: 3 }, { x: 16, y: 3 }]) {
      const c = candidate(['a', 'b'], cursor)!;
      expect(c).toMatchObject({ kind: 'angle', a: 'a', b: 'b' });
      const placement = c.placement!;
      if (!('along' in placement)) throw new Error('Expected angle placement');
      const theta = Math.atan2(6, 8) + (c.supplementary ? Math.PI : 0) + (c.supplementary ? -c.value : c.value) * placement.along;
      expect(10 + placement.offset * Math.cos(theta)).toBeCloseTo(cursor.x);
      expect(9 + placement.offset * Math.sin(theta)).toBeCloseTo(cursor.y);
    }
  });
  it('waits for incomplete/unsupported picks without mutating the sketch', () => {
    const before = JSON.stringify(sketch);
    for (const ids of [[], ['p'], ['axis'], ['arc', 'a'], ['p', 'p'], ['p', 'q', 'r']]) expect(candidate(ids)).toBeUndefined();
    expect(JSON.stringify(sketch)).toBe(before);
  });
  it('prioritizes nearby points over curves and hits infinite references with generous tolerance', () => {
    expect(hitDimension(sketch, references, { x: 2.2, y: 3.1 }, .5)).toBe('p');
    expect(hitDimension(sketch, references, { x: 6, y: 6 }, .5)).toBe('a');
    expect(hitDimension(sketch, references, { x: .2, y: 100 }, .5)).toBe('axis');
    expect(hitDimension(sketch, references, { x: 100, y: .2 }, .5)).toBe('baffle');
    expect(hitDimension(sketch, references, { x: 23.54, y: 8.54 }, .5)).toBe('arc');
    expect(hitDimension(sketch, references, { x: 100, y: 100 }, .5)).toBeUndefined();
  });
});
