import { describe, expect, it } from 'vitest';
import { applyCommand, importedSketch } from './ops';
import { filletPreview, trimPreview, extendPreview } from './topology';
import { arcGeometry, curveEnds, curveIntersections, distance, onArc } from './geometry';
import { validateSketch, type Arc, type Sketch } from './model';
import type { Command } from './commands';
import { History } from './history';

function corner() { return importedSketch([{ id: 'p1', x: 0, y: 10 }, { id: 'p2', x: 0, y: 0 }, { id: 'p3', x: 10, y: 0 }],
  [{ id: 'l4', kind: 'line', p1: 'p1', p2: 'p2' }, { id: 'l5', kind: 'line', p1: 'p2', p2: 'p3' }]); }
function cutters(): Sketch { return importedSketch([
  { id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 10, y: 0 }, { id: 'p3', x: 3, y: -5 }, { id: 'p4', x: 3, y: 5 }, { id: 'p5', x: 7, y: -5 }, { id: 'p6', x: 7, y: 5 },
], [{ id: 'l7', kind: 'line', p1: 'p1', p2: 'p2' }, { id: 'l8', kind: 'line', p1: 'p3', p2: 'p4', reference: true, construction: true }, { id: 'l9', kind: 'line', p1: 'p5', p2: 'p6', construction: true }]); }
function edit(s: Sketch, command: Command) { const r = applyCommand(s, command); if ('error' in r) throw new Error(r.error); expect(validateSketch(r.sketch)).toEqual({ ok: true }); return r; }
function span(s: Sketch, id: string) { const e = s.entities.find(e => e.id === id)!; if (e.kind !== 'line' && e.kind !== 'arc') throw new Error('curve'); return curveEnds(s, e).map(p => [p.x, p.y]); }
function circleArc(): Sketch { return importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 5, y: 0 }, { id: 'p3', x: -5, y: 0 }, { id: 'p4', x: 0, y: -6 }, { id: 'p5', x: 0, y: 6 }],
  [{ id: 'a6', kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw: true }, { id: 'l7', kind: 'line', p1: 'p4', p2: 'p5', construction: true }]); }

describe('fillet', () => {
  it.each([false, true])('makes an exact short tangent fillet with construction=%s', construction => {
    const s = corner(); s.entities = s.entities.map(e => ({ ...e, construction }));
    s.constraints.push({ id: 'c6', kind: 'fix', point: 'p2' }, { id: 'c7', kind: 'vertical', line: 'l4' }, { id: 'c8', kind: 'horizontal', line: 'l5' }); s.nextId = 9;
    const preview = filletPreview(s, 'p2', 2); expect(preview).toMatchObject({ center: { x: 2, y: 2 }, start: { x: 0, y: expect.closeTo(2) }, end: { x: expect.closeTo(2), y: 0 }, ccw: true });
    const r = edit(s, { kind: 'fillet', point: 'p2', radius: 2 }), arc = r.sketch.entities[1] as Arc;
    expect(arc.kind).toBe('arc'); expect(arc.construction).toBe(construction); expect(r.sketch.entities.map(e => e.id)).toEqual(['l4', arc.id, 'l5']);
    expect(Math.abs(arcGeometry(r.sketch, arc).sweep)).toBeCloseTo(Math.PI / 2); expect(arcGeometry(r.sketch, arc).radius).toBeCloseTo(2);
    expect(r.sketch.points.some(p => p.id === 'p2')).toBe(false); expect(r.removedConstraints).toEqual(['c6']); expect(r.notice).toContain('Fix');
    expect(r.sketch.constraints.filter(c => c.kind === 'tangent')).toHaveLength(2); expect(r.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'radius', value: 2, driving: true }));
    expect(r.sketch.constraints.some(c => c.id === 'c7')).toBe(true); expect(r.sketch.constraints.some(c => c.id === 'c8')).toBe(true);
  });
  it('handles reversed curve direction and an obtuse corner', () => {
    const s = corner(); s.points[2] = { ...s.points[2], x: 10, y: -10 }; s.entities[0] = { id: 'l4', kind: 'line', p1: 'p2', p2: 'p1' };
    const r = edit(s, { kind: 'fillet', point: 'p2', radius: 2 }); const arc = r.sketch.entities[1] as Arc;
    expect(arcGeometry(r.sketch, arc).radius).toBeCloseTo(2); expect(Math.abs(arcGeometry(r.sketch, arc).sweep)).toBeLessThan(Math.PI);
  });
  it.each([0, -1, NaN, 11])('refuses an invalid/unfitting radius %s without changing input', radius => {
    const s = corner(), before = JSON.stringify(s); expect(applyCommand(s, { kind: 'fillet', point: 'p2', radius })).toHaveProperty('error'); expect(JSON.stringify(s)).toBe(before);
  });
  it('refuses collinearity, branches, reference geometry and arcs with guidance', () => {
    const s = corner(); s.points[2] = { ...s.points[2], x: 0, y: -10 }; expect(filletPreview(s, 'p2', 1)).toHaveProperty('error', expect.stringContaining('Collinear'));
    s.entities[0].reference = true; expect(filletPreview(s, 'p2', 1)).toHaveProperty('error', expect.stringContaining('exactly two'));
    const a = circleArc(); a.entities.push({ id: 'l8', kind: 'line', p1: 'p2', p2: 'p4' }); a.nextId = 9;
    expect(filletPreview(a, 'p2', 1)).toHaveProperty('error', expect.stringContaining('line–line'));
  });
});
describe('trim', () => {
  it('previews and removes a middle span, preserving both outer pieces and attaching new ends to cutters', () => {
    const s = cutters(); expect(trimPreview(s, 'l7', { x: 5, y: 0 })).toEqual({ kind: 'line', a: { x: 3, y: 0 }, b: { x: 7, y: 0 } });
    const r = edit(s, { kind: 'trim', entity: 'l7', x: 5, y: 0 }), lines = r.sketch.entities.filter(e => !e.construction);
    expect(lines).toHaveLength(2); expect(span(r.sketch, 'l7')).toEqual([[0, 0], [3, 0]]); expect(span(r.sketch, lines[1].id)).toEqual([[7, 0], [10, 0]]);
    expect(r.sketch.constraints.filter(c => c.kind === 'pointOnLine')).toHaveLength(2);
  });
  it.each([[1, [[3, 0], [10, 0]]], [9, [[0, 0], [7, 0]]]] as const)('shortens the clicked end near %s with stable identity', (x, expected) => {
    const r = edit(cutters(), { kind: 'trim', entity: 'l7', x, y: 0 }); expect(span(r.sketch, 'l7')).toEqual(expected);
    expect(r.sketch.points.some(p => p.id === (x === 1 ? 'p1' : 'p2'))).toBe(false);
  });
  it('retains shared old endpoints and attaches cuts at cutter endpoints with coincidence', () => {
    const s = cutters(); s.points.find(p => p.id === 'p3')!.y = 0;
    s.entities.push({ id: 'l10', kind: 'line', p1: 'p1', p2: 'p5' }); s.nextId = 11;
    const r = edit(s, { kind: 'trim', entity: 'l7', x: 1, y: 0 }); expect(r.sketch.points.some(p => p.id === 'p1')).toBe(true);
    expect(r.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'coincident', b: 'p3' }));
  });
  it('deletes an uncut entity like deleteEntity and refuses trimming a reference', () => {
    const s = corner(), r = edit(s, { kind: 'trim', entity: 'l4', x: 0, y: 5 }); expect(r.sketch.entities.map(e => e.id)).toEqual(['l5']);
    expect(trimPreview(cutters(), 'l8', { x: 3, y: 0 })).toHaveProperty('error', expect.stringContaining('Reference'));
  });
  it.each([true, false])('trims finite arcs with ccw=%s, maintaining the circle and radius dimension', ccw => {
    const s = circleArc(); const arc = s.entities[0] as Arc; arc.ccw = ccw;
    s.constraints = [{ id: 'c8', kind: 'radius', arc: 'a6', value: 5, driving: true }]; s.nextId = 9;
    const r = edit(s, { kind: 'trim', entity: 'a6', x: 4, y: ccw ? 2 : -2 }), kept = r.sketch.entities.find(e => e.id === 'a6') as Arc;
    expect(kept.kind).toBe('arc'); expect(kept.ccw).toBe(ccw); expect(arcGeometry(r.sketch, kept).radius).toBeCloseTo(5);
    expect(r.sketch.constraints).toContainEqual(s.constraints[0]); expect(r.sketch.constraints.some(c => c.kind === 'pointOnLine')).toBe(true);
  });
  it('finds finite line–arc and arc–arc intersections, excluding the other half-circle', () => {
    const s = circleArc(), [a, l] = s.entities as [Arc, import('./model').Line];
    expect(curveIntersections(s, a, l)).toEqual([{ x: 0, y: 5 }]);
    const t = importedSketch([...s.points, { id: 'p8', x: 6, y: 0 }, { id: 'p9', x: 11, y: 0 }, { id: 'p10', x: 1, y: 0 }],
      [...s.entities, { id: 'a11', kind: 'arc', center: 'p8', start: 'p9', end: 'p10', ccw: true }]);
    const hits = curveIntersections(t, a, t.entities[2] as Arc); expect(hits).toHaveLength(1); expect(hits[0].x).toBeCloseTo(3); expect(hits[0].y).toBeCloseTo(4);
    expect(onArc(t, a, hits[0])).toBe(true);
  });
});
describe('extend', () => {
  it.each([0, 1])('extends the nearest free endpoint %s to the first finite cutter', end => {
    const s = cutters(); s.points[0].x = 4; s.points[1].x = 6;
    const r = edit(s, { kind: 'extend', entity: 'l7', x: end ? 6 : 4, y: 0 }); expect(span(r.sketch, 'l7')).toEqual(end ? [[4, 0], [7, 0]] : [[3, 0], [6, 0]]);
    expect(r.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'pointOnLine', line: end ? 'l9' : 'l8' }));
  });
  it('drops endpoint constraints and reports them', () => {
    const s = cutters(); s.points[0].x = 4; s.points[1].x = 6; s.constraints.push({ id: 'c10', kind: 'fix', point: 'p2' }); s.nextId = 11;
    const r = edit(s, { kind: 'extend', entity: 'l7', x: 6, y: 0 }); expect(r.removedConstraints).toEqual(['c10']); expect(r.notice).toContain('Fix');
  });
  it('refuses shared endpoints, missed cutters and reference targets', () => {
    expect(extendPreview(corner(), 'l4', { x: 0, y: 0 })).toHaveProperty('error', expect.stringContaining('shared'));
    expect(extendPreview(corner(), 'l4', { x: 0, y: 10 })).toHaveProperty('error', expect.stringContaining('No curve'));
    expect(extendPreview(cutters(), 'l8', { x: 3, y: 5 })).toHaveProperty('error', expect.stringContaining('Reference'));
  });
  it.each([true, false])('extends an arc in its sweep direction (ccw=%s)', ccw => {
    const s = circleArc(); s.entities[0] = { ...s.entities[0], kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw };
    s.points[2] = { id: 'p3', x: 3, y: ccw ? 4 : -4 };
    const preview = extendPreview(s, 'a6', s.points[2]); expect(preview).toMatchObject({ kind: 'arc', to: { x: 0, y: ccw ? 5 : -5 } });
    const r = edit(s, { kind: 'extend', entity: 'a6', x: 3, y: ccw ? 4 : -4 }); expect(r.sketch.points.find(p => p.id === 'p3')).toMatchObject({ x: 0, y: ccw ? 5 : -5 });
    expect(distance(r.sketch.points[0], r.sketch.points[2])).toBeCloseTo(5);
  });
});
describe('new command history', () => {
  it.each(['fillet', 'trim', 'extend', 'setDiameter', 'setDimensionExpression'] as const)('undoes and redoes %s exactly', kind => {
    let s = kind === 'fillet' ? corner() : cutters(); let command: Command;
    if (kind === 'fillet') command = { kind, point: 'p2', radius: 2 };
    else if (kind === 'trim') command = { kind, entity: 'l7', x: 5, y: 0 };
    else if (kind === 'extend') { s.points[0].x = 4; s.points[1].x = 6; command = { kind, entity: 'l7', x: 6, y: 0 }; }
    else { s = circleArc(); s.constraints = [{ id: 'c8', kind: 'radius', arc: 'a6', value: 5, driving: true }]; s.nextId = 9;
      command = kind === 'setDiameter' ? { kind, id: 'c8', diameter: true } : { kind, id: 'c8', expression: '2 + 3' }; }
    const history = new History(s), next = edit(s, command).sketch; history.begin(kind); history.update(next); history.commit();
    expect(history.undo()).toBe(s); expect(history.redo()).toBe(next);
  });
});

it('attaches a trimmed line endpoint to a finite arc cutter', () => {
  const s = circleArc(); s.points[3] = { id: 'p4', x: 0, y: 4 }; s.points[4] = { id: 'p5', x: 10, y: 4 }; s.entities[1].construction = false;
  const r = edit(s, { kind: 'trim', entity: 'l7', x: 1, y: 4 });
  expect(span(r.sketch, 'l7')[0]).toEqual([3, 4]);
  expect(r.sketch.constraints).toContainEqual(expect.objectContaining({ kind: 'pointOnArc', arc: 'a6' }));
});

it('removes a middle arc span, retaining both finite outer arcs and their common centre', () => {
  const s = circleArc(); s.points[3] = { id: 'p4', x: -10, y: 3 }; s.points[4] = { id: 'p5', x: 10, y: 3 };
  const preview = trimPreview(s, 'a6', { x: 0, y: 5 }); expect(preview).toMatchObject({ kind: 'arc', start: { x: 4, y: 3 }, end: { x: -4, y: 3 }, ccw: true });
  const r = edit(s, { kind: 'trim', entity: 'a6', x: 0, y: 5 }), arcs = r.sketch.entities.filter((e): e is Arc => e.kind === 'arc');
  expect(arcs).toHaveLength(2); expect(arcs.every(a => a.center === 'p1' && a.ccw && Math.abs(arcGeometry(r.sketch, a).radius - 5) < 1e-9)).toBe(true);
  expect(curveEnds(r.sketch, arcs[0])[1]).toMatchObject({ x: 4, y: 3 }); expect(curveEnds(r.sketch, arcs[1])[0]).toMatchObject({ x: -4, y: 3 });
});

it('extend retains endpoint constraints that still hold and drops only invalidated equations', () => {
  const s = cutters(); s.points[0].x = 4; s.points[1].x = 6;
  s.constraints = [{ id: 'c10', kind: 'distanceY', a: 'p1', b: 'p2', value: 0, driving: true },
    { id: 'c11', kind: 'distanceX', a: 'p1', b: 'p2', value: 2, driving: true }, { id: 'c12', kind: 'fix', point: 'p2' },
    { id: 'c13', kind: 'pointOnLine', point: 'p2', line: 'l7' }]; s.nextId = 14;
  const r = edit(s, { kind: 'extend', entity: 'l7', x: 6, y: 0 }); expect(r.removedConstraints).toEqual(['c11', 'c12']);
  expect(r.sketch.constraints).toContainEqual(s.constraints[0]); expect(r.sketch.constraints).toContainEqual(s.constraints[3]);
});
