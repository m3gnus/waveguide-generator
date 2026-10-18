// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import { writeFileSync } from 'node:fs';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
import { seed, lower, rules, applySourceCommand } from './adapters/sourceContour';
import { createSession } from './session';
import { applyCommand, importedSketch } from './ops';
import type { Command } from './commands';
import type { NewConstraint, Sketch } from './model';
import * as model from './model';
import * as geometry from './geometry';
import { findSnap } from './snap';
import { createSolver } from './solver/types';
const run = (s: Sketch, c: Command) => { const r = applyCommand(s, c); if ('error' in r) throw Error(r.error); return r; };
const line = () => importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }, { id: 'p', x: 5, y: 1 }],
  [{ kind: 'line', id: 'left', p1: 'a', p2: 'p' }, { kind: 'line', id: 'right', p1: 'p', p2: 'b' }]);
const dome = () => { const d = structuredClone(INITIAL_SOURCE); d.contour.points[0].z_mm = 3;
  d.contour.segments[0] = { ...d.contour.segments[0], kind: 'arc', direction: 'cw', center_mm: [0, -9.1667] }; return d; };
const output = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env.SKETCH_FIX_OUTPUT;
const session = (s: Sketch) => createSession(s, { references: [] });
describe('Phase 1a review regressions', () => {
  it('P1.1 dome rim tracks both cursor steps without pole drift or release collapse', async () => {
    const { sketch, meta } = seed(dome()), s = await createSession(sketch, { references: meta.references });
    const measurements: { target: number; cursorError: number; poleMovement: number }[] = [];
    try { await s.beginDrag(['rim']); for (const x of [8.5, 9.3]) {
      const r = await s.dragTo({ pointId: 'rim', x, y: 0 }); expect(r.stopped).toBe(false);
      measurements.push({ target: x, cursorError: geometry.distance(s.sketch.points.find(p => p.id === 'rim')!, { x, y: 0 }),
        poleMovement: geometry.distance(s.sketch.points.find(p => p.id === 'pole')!, { x: 0, y: 3 }) });
      expect(geometry.distance(s.sketch.points.find(p => p.id === 'rim')!, { x, y: 0 })).toBeLessThanOrEqual(1e-6);
      expect(geometry.distance(s.sketch.points.find(p => p.id === 'pole')!, { x: 0, y: 3 })).toBeLessThanOrEqual(1e-6);
    } expect((await s.endDrag()).status).toBe('ok'); expect(s.sketch.points.find(p => p.id === 'rim')!.x).toBeCloseTo(9.3, 6);
      expect(rules(s.sketch, meta)).toEqual([]); expect(lower(s.sketch, meta).ok).toBe(true);
      if (output) writeFileSync(`${output}.dome.json`, JSON.stringify({ measurements, released: s.sketch.points }, null, 2));
    } finally { s.destroy(); }
  });
  it('P1.1 rejects an ok solve that moves away from the cursor', async () => {
    const s = await createSession(line(), { references: [], solverFactory: async () => { const solver = await createSolver('slvs');
      const drag = solver.dragTo.bind(solver); vi.spyOn(solver, 'dragTo').mockImplementation(ts => { const r = drag(ts); return { ...r, points: { ...r.points, p: { x: 5, y: 2 } } }; }); return solver; } });
    try { await s.beginDrag(['p']); expect((await s.dragTo({ pointId: 'p', x: 5, y: 0 })).stopped).toBe(true); expect(s.sketch.points.find(p => p.id === 'p')!.y).toBe(1); } finally { s.destroy(); }
  });
  it('P1.2 free vertex crosses collinearity', async () => { const s = await session(line()); try {
    await s.beginDrag(['p']); for (const y of [0, -.5, -1]) expect((await s.dragTo({ pointId: 'p', x: 5, y })).stopped).toBe(false);
    expect(s.sketch.points.find(p => p.id === 'p')!.y).toBe(-1); await s.endDrag(); } finally { s.destroy(); } });
  it('P1.2 dimension continuation lets an untethered vertex cross straight', async () => {
    let sketch = line();
    for (const point of ['a', 'b']) sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point } }).sketch;
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distanceY', a: 'a', b: 'p', value: 1, driving: true } }).sketch;
    const s = await session(sketch);
    try { expect((await s.setDimensionContinuation(sketch.constraints.at(-1)!.id, -1)).ok).toBe(true);
      expect(s.sketch.points.find(p => p.id === 'p')!.y).toBeCloseTo(-1, 8);
    } finally { s.destroy(); }
  });
  it('P1.3 edits 100 to 120 despite an unrelated 1mm dimension', async () => {
    let sketch = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 1, y: 0 }, { id: 'c', x: 0, y: 10 }, { id: 'd', x: 100, y: 10 }], []);
    for (const [a, b, value] of [['a', 'b', 1], ['c', 'd', 100]] as const) sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a, b, value, driving: true } }).sketch;
    const s = await session(sketch); try { expect((await s.setDimensionContinuation(sketch.constraints[1].id, 120)).ok).toBe(true); } finally { s.destroy(); }
  });
  it('P1.4 rules use the same snapped endpoint geometry as lower after solving', async () => {
    const { sketch, meta } = seed(dome()), s = await createSession(sketch, { references: meta.references });
    try { expect((await s.solveFull()).status).toBe('ok'); expect(rules(s.sketch, meta)).toEqual([]); expect(lower(s.sketch, meta).ok).toBe(true); }
    finally { s.destroy(); }
  });
  it('P2.5 held grid releases for an endpoint and for a nearer grid node', () => {
    const s = line(), held = { x: 0, y: 0, glyph: 'grid' as const };
    expect(findSnap(s, [], { x: 5, y: 1 }, 1, held)?.glyph).toBe('endpoint');
    expect(findSnap(importedSketch([], []), [], { x: 6, y: 0 }, 1, held)?.x).toBe(10);
  });
  it('P2.5 excluded point cannot snap to itself or hold itself', () => {
    const s = line(), held = { x: 5, y: 1, glyph: 'endpoint' as const, constraint: { kind: 'coincident' as const, point: 'p' } };
    const r = findSnap(s, [], { x: 5, y: 1 }, .1, held, { exclude: ['p'] } as never);
    expect(r?.constraint && 'point' in r.constraint && r.constraint.point).not.toBe('p');
  });
  const splitBase = () => importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }, { id: 'q', x: 20, y: 0 }, { id: 'v', x: 2, y: 0 }],
    [{ kind: 'line', id: 'l', p1: 'a', p2: 'b' }, { kind: 'line', id: 'other', p1: 'v', p2: 'q' }]);
  const removed: NewConstraint[] = [
    { kind: 'equal', a: 'l', b: 'other' }, { kind: 'midpoint', point: 'v', line: 'l' },
    { kind: 'horizontal', line: 'l' }, { kind: 'vertical', line: 'l' }, { kind: 'parallel', a: 'l', b: 'other' },
    { kind: 'perpendicular', a: 'l', b: 'other' }, { kind: 'angle', a: 'l', b: 'other', value: 0, driving: true },
    { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true },
  ] as NewConstraint[];
  it.each(removed)('P2.6 split removes and reports $kind', constraint => {
    const s = run(splitBase(), { kind: 'addConstraint', constraint }).sketch, r = run(s, { kind: 'splitAt', entity: 'l', x: 5, y: 0 });
    expect(r.removedConstraints).toEqual([s.constraints[0].id]); expect(r.sketch.constraints).toEqual([]);
  });
  it('P2.6 split preserves extension membership on both line halves', () => {
    const s = run(splitBase(), { kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: 'q', line: 'l' } }).sketch;
    const r = run(s, { kind: 'splitAt', entity: 'l', x: 5, y: 0 }); expect(r.removedConstraints).toEqual([]);
    expect(r.sketch.constraints.filter(c => c.kind === 'pointOnLine' && c.point === 'q')).toHaveLength(2);
  });
  it('P2.6 split arc preserves supporting-circle membership outside its span', () => {
    let s = importedSketch([{ id: 'c', x: 0, y: 0 }, { id: 'a', x: 1, y: 0 }, { id: 'b', x: 0, y: 1 }, { id: 'q', x: -1, y: 0 }],
      [{ kind: 'arc', id: 'arc', center: 'c', start: 'a', end: 'b', ccw: true }]);
    s = run(s, { kind: 'addConstraint', constraint: { kind: 'pointOnArc', point: 'q', arc: 'arc' } }).sketch;
    const r = run(s, { kind: 'splitAt', entity: 'arc', x: Math.SQRT1_2, y: Math.SQRT1_2 });
    expect(r.removedConstraints).toEqual([]); expect(r.sketch.constraints).toHaveLength(1);
  });
  it('P2.7 lower derives replacement rim from baffle lock', () => {
    let { sketch, meta } = seed(INITIAL_SOURCE);
    const apply = (c: Command) => { const r = applySourceCommand(sketch, meta, c); if ('error' in r) throw Error(r.error); sketch = r.sketch; meta = r.meta; return r; };
    const split = apply({ kind: 'splitAt', entity: 'piston', x: 4, y: 0 }), join = split.created[0], right = split.created[1];
    apply({ kind: 'deleteEntity', id: right }); apply({ kind: 'addLine', from: join, to: { x: 8, y: 0, snap: { x: 8, y: 0, glyph: 'baffle', constraint: { kind: 'pointOnLine', line: meta.references[1].id } } } });
    expect(lower(sketch, meta).ok).toBe(true);
  });
  it.each(['redundant', 'conflicting'] as const)('P2.8 existing %s does not reject an unrelated new constraint', async kind => {
    let sketch = run(line(), { kind: 'addConstraint', constraint: { kind: 'horizontal', a: 'a', b: 'b' } }).sketch;
    const s = await createSession(sketch, { references: [], solverFactory: async () => { const solver = await createSolver('slvs'), solve = solver.solve.bind(solver);
      vi.spyOn(solver, 'solve').mockImplementation(o => ({ ...solve(o), status: 'ok', [kind]: [sketch.constraints[0].id] })); return solver; } });
    try { expect((await s.tryAddConstraint(sketch, { kind: 'vertical', a: 'a', b: 'p' })).ok).toBe(true); } finally { s.destroy(); }
  });
  it.each([
    ['names only the existing duplicate', (old: string) => ({ redundant: [old], hasRedundancy: true })],
    ['flags redundancy against an internal equation only', () => ({ redundant: [], hasRedundancy: true })],
  ] as const)('a new duplicate is refused when the solver %s', async (_name, report) => {
    const sketch = run(line(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } }).sketch;
    const old = sketch.constraints[0].id;
    let calls = 0;
    const s = await createSession(sketch, { references: [], solverFactory: async () => { const solver = await createSolver('slvs'), solve = solver.solve.bind(solver);
      // The first full solve is the baseline; later ones see the duplicate and report it as SolveSpace may.
      vi.spyOn(solver, 'solve').mockImplementation(o => { const r = solve(o); return o.mode === 'full' && calls++ > 0 ? { ...r, ...report(old) } : r; }); return solver; } });
    try {
      calls = 0;
      expect((await s.tryAddConstraint(sketch, { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true })).ok).toBe(false);
    } finally { s.destroy(); }
  });
  it('P2.9 overlapping drags coalesce pending targets and resolve with covering result', async () => {
    const seen: number[] = [], s = await createSession(line(), { references: [], solverFactory: async () => { const solver = await createSolver('slvs'), drag = solver.dragTo.bind(solver);
      vi.spyOn(solver, 'dragTo').mockImplementation(ts => { seen.push(ts[0].y); return drag(ts); }); return solver; } });
    try { await s.beginDrag(['p']); const calls = [s.dragTo({ pointId: 'p', x: 5, y: 10 }), s.dragTo({ pointId: 'p', x: 5, y: 1.2 }), s.dragTo({ pointId: 'p', x: 5, y: 1.3 })];
      const results = await Promise.all(calls); expect(seen).toHaveLength(2); expect(seen.at(-1)).toBe(1.3); expect(results.every(r => r.sketch.points.find(p => p.id === 'p')!.y === 1.3)).toBe(true); await s.endDrag();
    } finally { s.destroy(); }
  });
  it('P2.9 overlapping dimension edits process only latest pending value', async () => {
    const sketch = run(line(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } }).sketch;
    const seen: number[] = [], s = await createSession(sketch, { references: [], solverFactory: async () => {
      const solver = await createSolver('slvs'), load = solver.load.bind(solver);
      vi.spyOn(solver, 'load').mockImplementation(input => { const c = input.constraints.find(c => c.id === sketch.constraints[0].id); if (c && 'value' in c) seen.push(c.value); load(input); }); return solver;
    } });
    try { const id = sketch.constraints[0].id; const results = await Promise.all([s.setDimensionContinuation(id, 11), s.setDimensionContinuation(id, 12), s.setDimensionContinuation(id, 13)]);
      expect(seen).not.toContain(12); expect(seen).toContain(13); expect(results.every(r => r.ok)).toBe(true); expect(results[1].sketch.constraints.find(c => c.id === id)).toMatchObject({ value: 13 });
    } finally { s.destroy(); }
  });
  it('P2.10 pointDetermined distinguishes fixed, free and partially constrained points', async () => {
    let sketch = run(line(), { kind: 'addConstraint', constraint: { kind: 'fix', point: 'a' } }).sketch;
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'horizontal', a: 'a', b: 'b' } }).sketch;
    const s = await session(sketch); try { expect((await s.solveFull()).pointDetermined).toEqual({ a: true, b: false, p: false }); } finally { s.destroy(); }
  });
  it('P2.10 reference dimensions refresh on full solve and drag frame', async () => {
    const sketch = run(line(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 999, driving: false } }).sketch, s = await session(sketch);
    try { await s.solveFull(); expect(s.sketch.constraints[0]).toMatchObject({ value: 10 }); await s.beginDrag(['b']); await s.dragTo({ pointId: 'b', x: 12, y: 0 });
      expect(s.sketch.constraints[0]).toMatchObject({ value: 12 }); await s.endDrag(); } finally { s.destroy(); }
  });
  it('P2.10 projectToEntity clamps line segments and arc spans', () => {
    const project = (geometry as typeof geometry & { projectToEntity?: (s: Sketch, id: string, x: number, y: number) => { x: number; y: number } }).projectToEntity;
    expect(project).toBeTypeOf('function'); expect(project!(line(), 'left', -3, -1)).toEqual({ x: 0, y: 0 });
    const s = importedSketch([{ id: 'c', x: 0, y: 0 }, { id: 'a', x: 1, y: 0 }, { id: 'b', x: 0, y: 1 }], [{ kind: 'arc', id: 'arc', center: 'c', start: 'a', end: 'b', ccw: true }]);
    expect(project!(s, 'arc', -2, 1)).toEqual({ x: 0, y: 1 }); expect(project!(s, 'arc', 2, 2).x).toBeCloseTo(Math.SQRT1_2);
  });
  it('P3 session requires explicit adapter references', async () => { let message = ''; try { const s = await createSession(line(), {} as never); s.destroy(); } catch (e) { message = String(e); } expect(message).toContain('references'); });
  it('P3 reference lines cannot be deleted', () => { const { sketch, meta } = seed(INITIAL_SOURCE); expect(applyCommand(sketch, { kind: 'deleteEntity', id: meta.references[0].id })).toHaveProperty('error'); });
  it.each(['addPoint', 'addLine'] as const)('P3 %s refuses caller supplied IDs', kind => {
    const command = kind === 'addPoint' ? { kind, x: 2, y: 3, id: 'foreign' } : { kind, from: 'a', to: 'b', id: 'foreign' };
    expect(applyCommand(line(), command as Command)).toHaveProperty('error');
  });
  it('P3 stray point diagnostic names only that point', () => { const { sketch, meta } = seed(INITIAL_SOURCE), r = run(sketch, { kind: 'addPoint', x: 30, y: 30 });
    expect(lower(r.sketch, meta)).toMatchObject({ ok: false, problems: [{ code: 'stray-point', ids: [r.created[0]] }] }); });
  it('P3 validates at most once per command', () => { const s = line(), spy = vi.spyOn(model, 'validateSketch'); try { run(s, { kind: 'movePoint', id: 'p', x: 5, y: 2 }); expect(spy.mock.calls.length).toBeLessThanOrEqual(1); } finally { spy.mockRestore(); } });
  it('P3 arc guard visits points linearly', async () => {
    const points = Array.from({ length: 513 }, (_, i) => ({ id: `v${i}`, x: i, y: i % 2 }));
    const entities = Array.from({ length: 256 }, (_, i) => ({ kind: 'arc' as const, id: `a${i}`, center: `v${2 * i + 1}`, start: `v${2 * i}`, end: `v${2 * i + 2}`, ccw: true }));
    const sketch = importedSketch(points, entities), s = await session(sketch);
    let reads = 0;
    const measured = { ...sketch, points: points.map(p => ({ x: p.x, y: p.y, get id() { reads++; return p.id; } })) };
    try { (s as unknown as { guarded(a: Sketch, b: Sketch): boolean }).guarded(measured, measured);
      expect(reads).toBeLessThan(10 * (points.length + entities.length));
    } finally { s.destroy(); }
  });
  it('P3 seeds 256 arcs in under 500ms', () => {
    const d = dome(); d.contour.points = Array.from({ length: 257 }, (_, i) => ({ id: `v${i}`, r_mm: i, z_mm: 256 - i }));
    d.contour.segments = Array.from({ length: 256 }, (_, i) => ({ ...d.contour.segments[0], id: `e${i}`, start: `v${i}`, end: `v${i + 1}` }));
    const start = performance.now(); const r = seed(d); const ms = performance.now() - start; expect(r.sketch.entities).toHaveLength(258); expect(ms).toBeLessThan(500); if (output) writeFileSync(`${output}.seed.json`, JSON.stringify({ arcs: 256, ms }));
  });
});
