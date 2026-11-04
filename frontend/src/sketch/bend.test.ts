// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { arcBendTargets } from './bend';
import { importedSketch } from './ops';
import { arcGeometry, distance } from './geometry';
import { SketchSession, toSolverSketch } from './session';
import { createSolver } from './solver/types';
import type { Arc } from './model';

function sketch(ccw = true) { return importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 5, y: 0 }, { id: 'p3', x: 0, y: ccw ? 5 : -5 },
  { id: 'p4', x: 5, y: ccw ? -5 : 5 }, { id: 'p5', x: -5, y: ccw ? 5 : -5 }],
  [{ id: 'l6', kind: 'line', p1: 'p4', p2: 'p2' }, { id: 'a7', kind: 'arc', center: 'p1', start: 'p2', end: 'p3', ccw }, { id: 'l8', kind: 'line', p1: 'p3', p2: 'p5' }]); }
describe('arc bend targets', () => {
  it.each([true, false])('bends through the cursor with endpoints unchanged (ccw=%s)', ccw => {
    const s = sketch(ccw), cursor = { x: 4, y: ccw ? 4 : -4 }, targets = arcBendTargets(s, 'a7', cursor, 'bend');
    expect(targets.map(t => t.pointId)).toEqual(['p1', 'p2', 'p3']);
    expect(targets[1]).toMatchObject({ x: 5, y: 0 }); expect(targets[2]).toMatchObject({ x: 0, y: ccw ? 5 : -5 });
    expect(distance(targets[0], targets[1])).toBeCloseTo(distance(targets[0], cursor), 8);
  });
  it.each([true, false])('clamps chord crossing to a finite minimum sagitta on the original side (ccw=%s)', ccw => {
    const s = sketch(ccw), targets = arcBendTargets(s, 'a7', { x: -100, y: ccw ? -100 : 100 }, 'bend');
    for (const t of targets) expect(Number.isFinite(t.x + t.y)).toBe(true);
    const after = { ...s, points: s.points.map(p => { const t = targets.find(t => t.pointId === p.id); return t ? { ...p, x: t.x, y: t.y } : p; }) };
    const g = arcGeometry(after, after.entities[1] as Arc); expect(Math.sign(g.sweep)).toBe(ccw ? 1 : -1); expect(g.radius).toBeLessThan(10000);
  });
  it('changes radius along fixed rays while holding the centre', () => {
    const targets = arcBendTargets(sketch(), 'a7', { x: 10, y: 0 }, 'radius');
    expect(targets).toEqual([{ pointId: 'p1', x: 0, y: 0 }, { pointId: 'p2', x: 10, y: 0 }, { pointId: 'p3', x: 0, y: 10 }]);
  });
  it('refuses invalid arcs, cursor coordinates and zero radius with guidance', () => {
    expect(() => arcBendTargets(sketch(), 'l6', { x: 1, y: 1 }, 'bend')).toThrow('Select an arc');
    expect(() => arcBendTargets(sketch(), 'a7', { x: NaN, y: 1 }, 'bend')).toThrow('finite');
    expect(() => arcBendTargets(sketch(), 'a7', { x: 0, y: 0 }, 'radius')).toThrow('positive');
  });
  it('real slvs session keeps tangent-neighbour endpoints within multi-target drag tolerance', async () => {
    const s = sketch(); s.constraints = [{ id: 'c9', kind: 'tangent', a: 'l6', b: 'a7' }, { id: 'c10', kind: 'tangent', a: 'a7', b: 'l8' }]; s.nextId = 11;
    const session = await SketchSession.create(s, { references: [], solverFactory: () => createSolver('slvs'), dragTolerance: 1e-3 });
    try {
      expect((await session.solveFull()).status).toBe('ok'); const start = session.sketch;
      const targets = arcBendTargets(start, 'a7', { x: 3.8, y: 3.8 }, 'bend');
      await session.beginDrag(targets.map(t => t.pointId)); const result = await session.dragTo(targets);
      expect(result.stopped).toBe(false);
      for (const t of targets) expect(distance(result.sketch.points.find(p => p.id === t.pointId)!, t)).toBeLessThanOrEqual(1e-3);
      const final = await session.endDrag(); expect(final.status).toBe('ok');
      for (const id of ['p2', 'p3']) expect(distance(session.sketch.points.find(p => p.id === id)!, start.points.find(p => p.id === id)!)).toBeLessThanOrEqual(1e-3);
    } finally { session.destroy(); }
  });
  it.each(['slvs', 'planegcs'] as const)('%s receives only solver quantities, with authoring expression and diameter omitted', async name => {
    const s = sketch(); s.constraints = [{ id: 'c9', kind: 'radius', arc: 'a7', value: 5, driving: true, diameter: true, expression: '10' }]; s.nextId = 10;
    const raw = toSolverSketch(s, []); expect(raw.constraints[0]).not.toHaveProperty('diameter'); expect(raw.constraints[0]).not.toHaveProperty('expression'); expect(raw.constraints[0]).toMatchObject({ value: 5 });
    const solver = await createSolver(name);
    try { solver.load(raw); const result = solver.solve({ mode: 'full' }); expect(result.status).toBe('ok'); expect(distance(result.points.p1, result.points.p2)).toBeCloseTo(5, 7); }
    finally { solver.destroy(); }
  });
});
