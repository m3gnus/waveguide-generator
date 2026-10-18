// @vitest-environment node
import { describe, expect, it } from 'vitest';
import type { Command } from './commands';
import { emptySketch, validateSketch, type Sketch } from './model';
import { applyCommand, importedSketch } from './ops';

function run(s: Sketch, c: Command) { const r = applyCommand(s, c); if ('error' in r) throw new Error(r.error); return r; }
const base = () => importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 10, y: 0 }, { id: 'p3', x: 20, y: 0 }],
  [{ kind: 'line', id: 'l4', p1: 'p1', p2: 'p2' }, { kind: 'line', id: 'l5', p1: 'p2', p2: 'p3' }]);
describe('immutable sketch commands', () => {
  it('allocates monotonically and converts committed snaps to constraints', () => {
    const original = base(), snapshot = JSON.stringify(original);
    const r = run(original, { kind: 'addLine', from: 'p3', to: { x: 31, y: 1, snap: { x: 30, y: 0, glyph: 'horizontal', constraint: { kind: 'horizontal', point: 'p3' } } } });
    expect(r.created).toEqual(['p6', 'c7', 'l8']);
    expect(r.sketch.constraints).toEqual([{ id: 'c7', kind: 'horizontal', a: 'p6', b: 'p3' }]);
    expect(r.sketch.points.at(-1)).toEqual({ id: 'p6', x: 30, y: 0 });
    const deleted = run(r.sketch, { kind: 'deleteEntity', id: 'l8' });
    expect(deleted.removedConstraints).toEqual(['c7']);
    expect(run(deleted.sketch, { kind: 'addPoint', x: 1, y: 2 }).created).toEqual(['p9']);
    expect(applyCommand(deleted.sketch, { kind: 'addPoint', id: 'p6', x: 1, y: 2 } as Command)).toHaveProperty('error');
    expect(JSON.stringify(original)).toBe(snapshot);
  });
  it('supports endpoint and reference snap constraints', () => {
    let s = base();
    s = run(s, { kind: 'addLine', from: 'p3', to: { x: 0, y: 0, snap: { x: 0, y: 0, glyph: 'endpoint', constraint: { kind: 'coincident', point: 'p1' } } } }).sketch;
    s = run(s, { kind: 'addLine', from: 'p3', to: { x: 4, y: 0, snap: { x: 4, y: 0, glyph: 'baffle', constraint: { kind: 'pointOnLine', line: 'l4' } } } }).sketch;
    expect(s.constraints.map(c => c.kind)).toEqual(['coincident', 'pointOnLine']);
  });
  it('edits only the point seed, dimensions, driving, construction and opaque data', () => {
    const original = base();
    let r = run(original, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p1', b: 'p2', value: 10, driving: true } });
    const id = r.created[0];
    r = run(r.sketch, { kind: 'setDimension', id, value: 12 });
    r = run(r.sketch, { kind: 'setDriving', id, driving: false });
    r = run(r.sketch, { kind: 'movePoint', id: 'p2', x: 9, y: 3 });
    expect(r.sketch.points[1]).toEqual({ id: 'p2', x: 9, y: 3 });
    expect(r.sketch.constraints[0]).toMatchObject({ value: 12, driving: false });
    r = run(r.sketch, { kind: 'setEntityData', id: 'l4', data: { role: 'rigid', other: [0, false] } });
    r = run(r.sketch, { kind: 'setConstruction', id: 'l4', construction: true });
    expect(r.sketch.entities[0]).toMatchObject({ construction: true, data: { role: 'rigid', other: [0, false] } });
    expect(run(r.sketch, { kind: 'removeConstraint', id }).removedConstraints).toEqual([id]);
    expect(original.points[1].x).toBe(10);
  });
  it('builds tangent arcs from lines and arcs and exact three-point arcs', () => {
    let r = run(base(), { kind: 'addTangentArc', from: 'p3', to: { x: 30, y: 10 } });
    const a = r.sketch.entities.at(-1)!;
    expect(a).toMatchObject({ kind: 'arc', start: 'p3', ccw: true });
    expect(r.sketch.constraints.at(-1)).toMatchObject({ kind: 'tangent', a: 'l5', b: a.id });
    const end = a.kind === 'arc' ? a.end : '';
    r = run(r.sketch, { kind: 'addTangentArc', from: end, to: { x: 20, y: 20 } });
    expect(validateSketch(r.sketch)).toEqual({ ok: true });
    const three = run(emptySketch(), { kind: 'addArc3', start: { x: -1, y: 0 }, end: { x: 1, y: 0 }, through: { x: 0, y: 1 } });
    const arc = three.sketch.entities[0]; expect(arc).toMatchObject({ kind: 'arc', ccw: false });
    expect(three.sketch.points.at(-1)).toMatchObject({ x: 0, y: 0 });
    expect(run(emptySketch(), { kind: 'addArc3', start: { x: -1, y: 0 }, end: { x: 1, y: 0 }, through: { x: 0, y: -1 } }).sketch.entities[0]).toMatchObject({ ccw: true });
  });
  it('split keeps the left identity and copies JSON while remapping curve membership', () => {
    let s = base();
    s = run(s, { kind: 'setEntityData', id: 'l4', data: { role: 'moving', extra: 0 } }).sketch;
    for (const [id, x, y] of [['left', 2, 0], ['right', 8, 0], ['off', 7, 2]] as const) s = { ...s, points: [...s.points, { id, x, y }] };
    for (const point of ['left', 'right', 'off']) s = run(s, { kind: 'addConstraint', constraint: { kind: 'pointOnLine', point, line: 'l4' } }).sketch;
    s = run(s, { kind: 'addConstraint', constraint: { kind: 'fix', point: 'p2' } }).sketch;
    const r = run(s, { kind: 'splitAt', entity: 'l4', x: 5, y: 0 }), right = r.sketch.entities[1];
    expect(r.sketch.entities[0]).toMatchObject({ id: 'l4', p1: 'p1', p2: r.created[0] });
    expect(right).toMatchObject({ p1: r.created[0], p2: 'p2', data: s.entities[0].data });
    expect(r.sketch.constraints.find(c => c.kind === 'pointOnLine' && c.point === 'right')).toMatchObject({ line: right.id });
    expect(r.removedConstraints).toEqual([s.constraints[2].id]);
    expect(r.sketch.constraints.at(-1)).toEqual(s.constraints.at(-1));
  });
  it('splits arcs without changing the center or direction and moves endpoint tangency', () => {
    let s = run(emptySketch(), { kind: 'addArc3', start: { x: -1, y: 0 }, end: { x: 1, y: 0 }, through: { x: 0, y: 1 } }).sketch;
    const arc = s.entities[0]; if (arc.kind !== 'arc') throw new Error('arc');
    s = run(s, { kind: 'addLine', from: arc.end, to: { x: 1, y: -2 } }).sketch;
    s = run(s, { kind: 'addConstraint', constraint: { kind: 'tangent', a: arc.id, b: s.entities[1].id } }).sketch;
    const r = run(s, { kind: 'splitAt', entity: arc.id, x: 0, y: 1 });
    expect(r.sketch.entities[0]).toMatchObject({ id: arc.id, center: arc.center, ccw: false });
    expect(r.sketch.entities[1]).toMatchObject({ center: arc.center, ccw: false });
    expect(r.sketch.constraints[0]).toMatchObject({ a: r.created[1] });
    const deleted = run(r.sketch, { kind: 'deleteEntity', id: r.created[1] });
    expect(deleted.removedConstraints).toEqual([s.constraints[0].id]);
    expect(deleted.sketch.points.some(p => p.id === arc.center)).toBe(true);
  });
  it('remaps finite arc membership and tangency through coincident endpoints', () => {
    let s = run(emptySketch(), { kind: 'addArc3', start: { x: -1, y: 0 }, end: { x: 1, y: 0 }, through: { x: 0, y: 1 } }).sketch;
    const arc = s.entities[0]; if (arc.kind !== 'arc') throw new Error('arc');
    for (const [id, x, y] of [['on', .6, .8], ['off', 0, 3], ['contact', 1, 0]] as const) s = { ...s, points: [...s.points, { id, x, y }] };
    for (const point of ['on', 'off']) s = run(s, { kind: 'addConstraint', constraint: { kind: 'pointOnArc', point, arc: arc.id } }).sketch;
    s = run(s, { kind: 'addConstraint', constraint: { kind: 'coincident', a: 'contact', b: arc.end } }).sketch;
    s = run(s, { kind: 'addLine', from: 'contact', to: { x: 1, y: -2 } }).sketch;
    s = run(s, { kind: 'addConstraint', constraint: { kind: 'tangent', a: arc.id, b: s.entities.at(-1)!.id } }).sketch;
    const r = run(s, { kind: 'splitAt', entity: arc.id, x: 0, y: 1 });
    expect(r.sketch.constraints[0]).toMatchObject({ kind: 'pointOnArc', point: 'on', arc: r.created[1] });
    expect(r.removedConstraints).toEqual([s.constraints[1].id]);
    expect(r.sketch.constraints.at(-1)).toMatchObject({ kind: 'tangent', a: r.created[1] });
  });
  it.each<Command>([
    { kind: 'addPoint', x: NaN, y: 0 }, { kind: 'addPoint', id: 'p1', x: 0, y: 1 } as Command,
    { kind: 'addLine', from: 'missing', to: 'p2' }, { kind: 'addLine', from: 'p1', to: 'p1' },
    { kind: 'addTangentArc', from: 'p1', to: 'p3' }, { kind: 'addTangentArc', from: 'p3', to: { x: 30, y: 0 } },
    { kind: 'addArc3', start: 'p1', end: 'p2', through: { x: 5, y: 0 } },
    { kind: 'removeConstraint', id: 'missing' }, { kind: 'setDimension', id: 'l4', value: 2 },
    { kind: 'setDriving', id: 'missing', driving: true }, { kind: 'movePoint', id: 'missing', x: 0, y: 0 },
    { kind: 'movePoint', id: 'p1', x: Infinity, y: 0 }, { kind: 'deleteEntity', id: 'missing' },
    { kind: 'splitAt', entity: 'l4', x: 10, y: 0 }, { kind: 'splitAt', entity: 'l4', x: 4, y: 1 },
    { kind: 'setEntityData', id: 'missing', data: null }, { kind: 'setConstruction', id: 'missing', construction: true },
    { kind: 'addConstraint', constraint: { kind: 'tangent', a: 'l4', b: 'l5' } },
    { kind: 'addConstraint', constraint: { kind: 'equal', a: 'l4', b: 'p1' } },
    { kind: 'addConstraint', constraint: { kind: 'radius', arc: 'l4', value: 1, driving: true } },
    { kind: 'addConstraint', constraint: { kind: 'distance', a: 'p1', b: 'p2', value: -1, driving: true } },
  ])('refuses bad commands atomically: %j', command => {
    const s = base(), before = JSON.stringify(s); expect(applyCommand(s, command)).toHaveProperty('error'); expect(JSON.stringify(s)).toBe(before);
  });
  it('refuses mixed line/arc equality before reaching a solver', () => {
    const s = run(base(), { kind: 'addTangentArc', from: 'p3', to: { x: 30, y: 10 } }).sketch;
    expect(applyCommand(s, { kind: 'addConstraint', constraint: { kind: 'equal', a: 'l4', b: s.entities.at(-1)!.id } })).toMatchObject({ error: expect.stringContaining('entity kinds') });
  });
});
