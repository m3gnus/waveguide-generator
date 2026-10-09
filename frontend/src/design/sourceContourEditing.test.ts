import { describe, expect, it } from 'vitest';
import { drawnSource, editPatch, editPoint, INITIAL_SOURCE, removePoint } from './sourceContourEditing';

describe('canonical source draft editing', () => {
  it('locks attachment endpoints and preserves IDs through point edits', () => {
    const pole = editPoint(INITIAL_SOURCE, 0, 5, 2);
    expect(pole.contour.points[0]).toEqual({ id: 'pole', r_mm: 0, z_mm: 2 });
    const rim = editPoint(pole, 1, 10, 7);
    expect(rim.contour.points[1]).toEqual({ id: 'rim', r_mm: 10, z_mm: 0 });
    expect(rim.drive).toEqual(INITIAL_SOURCE.drive);
    expect(INITIAL_SOURCE.contour.points[1].r_mm).toBe(8);
  });
  it('does not derive roles from signed or zero weights', () => {
    const doc = structuredClone(INITIAL_SOURCE);
    doc.drive.weights.piston = 0;
    expect(editPatch(doc, 0, { kind: 'arc' }).contour.segments[0].role).toBe('moving');
    doc.drive.weights.piston = -.5;
    expect(editPatch(doc, 0, { direction: 'cw' }).drive.weights.piston).toBe(-.5);
    const rigid = editPatch(doc, 0, { role: 'rigid' });
    expect(rigid.drive.weights).toEqual({});
    expect(editPatch(rigid, 0, { role: 'moving' }).drive.weights).toEqual({ piston: 1 });
  });
  it('drawing writes explicit line patches and one physical source/channel', () => {
    let count = 0;
    const result = drawnSource(INITIAL_SOURCE, [{ id: 'a', r_mm: 3, z_mm: -4 }, { id: 'b', r_mm: 5, z_mm: -2 }, { id: 'c', r_mm: 9, z_mm: 1 }], () => `patch-${++count}`);
    expect(result.contour.points.map((p) => [p.r_mm, p.z_mm])).toEqual([[0, -4], [5, -2], [9, 0]]);
    expect(result.contour.segments.map((p) => [p.start, p.end, p.kind])).toEqual([['a', 'b', 'line'], ['b', 'c', 'line']]);
    expect(result.contour.physical_source_id).toBe('diaphragm');
    expect(result.drive).toEqual({ channel_id: 'motor', motion: 'normal', weights: { 'patch-1': 1, 'patch-2': 1 } });
  });
  it('removing a join cannot silently discard a distinct role or weight', () => {
    const doc = drawnSource(INITIAL_SOURCE, [{ id: 'a', r_mm: 0, z_mm: 0 }, { id: 'b', r_mm: 4, z_mm: 1 }, { id: 'c', r_mm: 8, z_mm: 0 }], (() => { let i = 0; return () => `${++i}`; })());
    doc.drive.weights['2'] = 0;
    expect(() => removePoint(doc, 1)).toThrow('same role and weight');
    doc.drive.weights['2'] = 1;
    const joined = removePoint(doc, 1);
    expect(joined.contour.segments).toEqual([{ ...doc.contour.segments[0], end: 'c' }]);
    expect(joined.drive.weights).toEqual({ '1': 1 });
    expect(() => removePoint(doc, 0)).toThrow('Pole and rim');
  });
});
