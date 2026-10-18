// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { emptySketch } from '../model';
import { applyCommand, importedSketch } from '../ops';
import { cancelLine, continueLine, lineState, placeLine, typedTarget } from './line';
import { coalesceTargets, select } from './select';
import { candidateDimension, dimensionPicks } from './dimension';
import { constraintFor } from './constraints';

describe('pure sketch tools', () => {
  it('chains lines and passes endpoint and inference snaps to the commands', () => {
    const first = placeLine(lineState(), { x: 0, y: 0 }, null);
    expect(first.command).toBeUndefined();
    const snap = { x: 8, y: 0, glyph: 'horizontal' as const, constraint: { kind: 'horizontal' as const, point: 'origin' } };
    const base = importedSketch([{ id: 'origin', x: 0, y: 0 }], []);
    const next = placeLine({ ...first.state, from: 'origin' }, { x: 8, y: .1 }, snap);
    expect(next.command).toEqual({ kind: 'addLine', from: 'origin', to: { x: 8, y: .1, snap } });
    const result = applyCommand(base, next.command!);
    if ('error' in result) throw new Error(result.error);
    expect(result.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'horizontal', b: 'origin' }));
    const chain = continueLine(next.state, base, result.sketch);
    expect(typeof chain.from).toBe('string');
    const endpoint = { x: 0, y: 0, glyph: 'endpoint' as const, constraint: { kind: 'coincident' as const, point: 'origin' } };
    expect(placeLine(chain, { x: 0, y: 0 }, endpoint).command).toMatchObject({ from: chain.from, to: { snap: endpoint } });
    expect(cancelLine()).toEqual(lineState());
  });
  it('computes typed lengths and degrees exactly', () => {
    expect(typedTarget({ x: 2, y: 3 }, { x: 10, y: 3 }, 5)).toEqual({ x: 7, y: 3 });
    const p = typedTarget({ x: 2, y: 3 }, { x: 10, y: 3 }, 5, 90);
    expect(p.x).toBeCloseTo(2); expect(p.y).toBeCloseTo(8);
  });
  it('starts a three-point arc on empty space in tangent mode and rejects invalid chain starts immediately', () => {
    const s = importedSketch([{ id: 'pole', x: 0, y: 0 }, { id: 'join', x: 4, y: 0 }, { id: 'end', x: 8, y: 0 }], [
      { kind: 'line', id: 'left', p1: 'pole', p2: 'join' }, { kind: 'line', id: 'right', p1: 'join', p2: 'end' },
    ]);
    const state = { mode: 'tangent' as const };
    expect(placeLine(state, { x: 2, y: 3 }, null, s)).toMatchObject({ state: { mode: 'arc3', from: { x: 2, y: 3 } }, reason: expect.stringContaining('started a 3-point arc') });
    for (const [point, reason] of [['pole', 'no incoming curve'], ['join', 'not an open chain end']]) {
      const rejected = placeLine(state, { x: 0, y: 0 }, { x: 0, y: 0, glyph: 'endpoint', constraint: { kind: 'coincident', point } }, s);
      expect(rejected.state).toBe(state); expect(rejected.reason).toContain(reason);
    }
    expect(placeLine(state, { x: 8, y: 0 }, { x: 8, y: 0, glyph: 'endpoint', constraint: { kind: 'coincident', point: 'end' } }, s).state).toEqual({ mode: 'tangent', from: 'end' });
  });
  it('places tangent and three-point arcs through the command layer', () => {
    const result = applyCommand(emptySketch(), { kind: 'addLine', from: { x: 0, y: 0 }, to: { x: 5, y: 0 } });
    if ('error' in result) throw new Error(result.error);
    const chain = continueLine({ mode: 'tangent' }, emptySketch(), result.sketch);
    const next = placeLine(chain, { x: 8, y: 3 }, null);
    expect(next.command).toMatchObject({ kind: 'addTangentArc', from: chain.from });
    const arc = applyCommand(result.sketch, next.command!);
    if ('error' in arc) throw new Error(arc.error);
    expect(arc.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'tangent' }));
    let state = placeLine({ mode: 'arc3' }, { x: 0, y: 0 }, null).state;
    state = placeLine(state, { x: 4, y: 0 }, null).state;
    expect(placeLine(state, { x: 2, y: 2 }, null).command).toMatchObject({ kind: 'addArc3', through: { x: 2, y: 2 } });
  });
  it('chooses line length, two-point distance, arc radius and two-line angle', () => {
    const s = importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 4, y: 0 }, { id: 'r', x: 4, y: 3 }], [
      { id: 'l', kind: 'line', p1: 'p', p2: 'q' }, { id: 'm', kind: 'line', p1: 'q', p2: 'r' }, { id: 'a', kind: 'arc', center: 'p', start: 'q', end: 'r', ccw: true },
    ]);
    const dimensionFor = (sketch: typeof s, ids: string[], cursor = { x: -3, y: 4 }) => candidateDimension(dimensionPicks(sketch, ids), cursor);
    expect(dimensionFor(s, ['l'])).toMatchObject({ kind: 'distance', value: 4 });
    expect(dimensionFor(s, ['p', 'r'])).toMatchObject({ kind: 'distance', value: 5 });
    expect(dimensionFor(s, ['p', 'r'], { x: 2, y: 5 })).toMatchObject({ kind: 'distanceX', value: 4 });
    expect(dimensionFor(s, ['l'], { x: -2, y: 0 })).toMatchObject({ kind: 'distanceY', value: 0 });
    expect(dimensionFor(s, ['a'])).toMatchObject({ kind: 'radius', value: 4 });
    expect(dimensionFor(s, ['l', 'm'])).toMatchObject({ kind: 'angle', value: Math.PI / 2 });
    expect(dimensionFor(s, ['l', 'r'])).toMatchObject({ kind: 'pointLineDistance', value: 3 });
    expect(constraintFor(s, ['l', 'm'], 'tangent')).toBeUndefined();
    expect(constraintFor(s, ['l', 'a'], 'tangent')).toMatchObject({ kind: 'tangent' });
    expect(constraintFor(s, ['q', 'l'], 'point-on')).toMatchObject({ point: 'q', line: 'l' });
  });
  it('extends and toggles selection and coalesces to the latest frame target', () => {
    expect(select(['a'], 'b', true)).toEqual(['a', 'b']); expect(select(['a'], 'a', true)).toEqual([]);
    let callback = () => {}; const consumed: number[] = []; let scheduled = 0;
    const queue = coalesceTargets<number>(fn => { callback = fn; return ++scheduled; }, () => {}, v => consumed.push(v));
    queue.push(1); queue.push(2); queue.push(3); expect(scheduled).toBe(1); callback(); expect(consumed).toEqual([3]);
    queue.push(4); queue.flush(); expect(consumed).toEqual([3, 4]); queue.push(5); queue.cancel(); callback(); expect(consumed).toEqual([3, 4]);
  });
});
