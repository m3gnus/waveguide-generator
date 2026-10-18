// @vitest-environment node
import { afterAll, describe, expect, it, vi } from 'vitest';
import { writeFileSync } from 'node:fs';
import type { SlvsModule } from 'slvs';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
import { lower, rules, seed } from './adapters/sourceContour';
import { applyCommand, importedSketch } from './ops';
import * as model from './model';
import type { Command } from './commands';
import { createSession } from './session';
import { createSolver } from './solver/types';
import { contourChain } from './solver/corpus';

const measurements: Record<string, unknown> = {};
const nodeProcess = (globalThis as { process?: { env: Record<string, string | undefined> } }).process;
afterAll(() => { const output = nodeProcess?.env.SKETCH_FIX2_OUTPUT; if (output) writeFileSync(output, JSON.stringify(measurements, null, 2)); });
const capture = vi.hoisted(() => ({ slvs: undefined as SlvsModule | undefined }));
vi.mock('slvs', async importOriginal => {
  const actual = await importOriginal<typeof import('slvs')>();
  return { ...actual, default: async (...args: unknown[]) => {
    capture.slvs = await (actual.default as (...a: unknown[]) => ReturnType<typeof actual.default>)(...args); return capture.slvs;
  } };
});
const run = (s: model.Sketch, c: Command) => { const r = applyCommand(s, c); if ('error' in r) throw new Error(r.error); return r.sketch; };
const line = () => importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }], [{ kind: 'line', id: 'l', p1: 'a', p2: 'b' }]);
function dome() {
  const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[0].z_mm = 3;
  doc.contour.segments[0] = { ...doc.contour.segments[0], kind: 'arc', center_mm: [0, (9 - 64) / 6], direction: 'cw' };
  return seed(doc);
}
const point = (s: model.Sketch, id: string) => s.points.find(p => p.id === id)!;

describe('fix2 off-locus drags', () => {
  it.each([.05, .5, 2].flatMap(offset => [1e-3, 1e-9].map(dragTolerance => ({ offset, dragTolerance }))))('keeps the dome pole still for rim cursor offset $offset at tolerance $dragTolerance', async ({ offset, dragTolerance }) => {
    const { sketch, meta } = dome(), session = await createSession(sketch, { references: meta.references, dragTolerance });
    const pole = sketch.entities.find(e => e.kind === 'arc')!; if (pole.kind !== 'arc') throw new Error('arc');
    let drift = 0;
    try {
      await session.beginDrag(['rim']);
      for (const direction of [1, -1]) for (let frame = 0; frame <= 50; frame++) {
        const x = direction === 1 ? 8 + 1.3 * frame / 50 : 9.3 - 1.3 * frame / 50;
        await session.dragTo({ pointId: 'rim', x, y: offset });
        drift = Math.max(drift, Math.abs(point(session.sketch, pole.start).y - 3));
      }
      expect((await session.endDrag()).status).toBe('ok');
      drift = Math.max(drift, Math.abs(point(session.sketch, pole.start).y - 3));
      measurements[`rimOffset${offset}Tolerance${dragTolerance}`] = { poleDrift: drift, rim: point(session.sketch, 'rim') };
      expect(drift).toBeLessThan(1e-6); expect(point(session.sketch, 'rim').x).toBeCloseTo(8, 6);
      expect(Math.abs(point(session.sketch, 'rim').y)).toBeLessThan(1e-6); expect(rules(session.sketch, meta)).toEqual([]);
    } finally { session.destroy(); }
  });
  it.each([.05, .5])('tracks pole motion with cursor offset %s from the axis', async offset => {
    const { sketch, meta } = dome(), arc = sketch.entities.find(e => e.kind === 'arc')!;
    if (arc.kind !== 'arc') throw new Error('arc');
    const session = await createSession(sketch, { references: meta.references }); let drift = 0;
    try {
      await session.beginDrag([arc.start]);
      for (const direction of [1, -1]) for (let frame = 0; frame <= 50; frame++) {
        const y = direction === 1 ? 3 + frame / 50 : 4 - frame / 50;
        await session.dragTo({ pointId: arc.start, x: offset, y });
        drift = Math.max(drift, Math.abs(point(session.sketch, 'rim').x - 8));
      }
      await session.endDrag(); measurements[`poleOffset${offset}`] = { rimDrift: drift, pole: point(session.sketch, arc.start) };
      expect(drift).toBeLessThan(1e-6); expect(point(session.sketch, arc.start).y).toBeCloseTo(3, 6);
      expect(Math.abs(point(session.sketch, arc.start).x)).toBeLessThan(1e-6); expect(rules(session.sketch, meta)).toEqual([]);
    } finally { session.destroy(); }
  });
  it('stops a pole on two loci without drifting the rest of the dome', async () => {
    const imported = dome(), arc = imported.sketch.entities.find(e => e.kind === 'arc')!;
    if (arc.kind !== 'arc') throw new Error('arc');
    let sketch = run(imported.sketch, { kind: 'addPoint', x: 1, y: 3 }); const fixed = sketch.points.at(-1)!.id;
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point: fixed } });
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: fixed, b: arc.start, value: 1, driving: true } });
    const session = await createSession(sketch, { references: imported.meta.references });
    try {
      await session.beginDrag([arc.start]);
      await session.dragTo({ pointId: arc.start, x: .5, y: 5 });
      const released = await session.endDrag(); expect(released.status).toBe('ok');
      for (const p of sketch.points) expect(Math.hypot(point(session.sketch, p.id).x - p.x, point(session.sketch, p.id).y - p.y)).toBeLessThan(1e-6);
    } finally { session.destroy(); }
  });
  it('rejects disproportionate other-point drift only for an unreachable step', async () => {
    const solver = await createSolver('slvs'), session = await createSession(line(), { references: [], solverFactory: async () => solver });
    const spy = vi.spyOn(solver, 'dragTo').mockImplementation(() => ({ status: 'ok', points: {
      a: { x: point(session.sketch, 'a').x + .001, y: 0 }, b: { x: point(session.sketch, 'b').x + 1, y: 0 },
    }, timings: { totalMs: 0, updateMs: 0, solveMs: 0, readbackMs: 0, marshalMs: 0 } }));
    try {
      await session.beginDrag(['a']); const result = await session.dragTo({ pointId: 'a', x: 1, y: 0 });
      expect(result.stopped).toBe(true); expect(point(session.sketch, 'b').x).toBe(10); expect(point(session.sketch, 'a').x).toBe(0);
    } finally { spy.mockRestore(); session.destroy(); }
  });
  it('skips exact release refinement when the projected target remains unreachable', async () => {
    let sketch = line(); sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point: 'a' } });
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } });
    const solver = await createSolver('slvs'), session = await createSession(sketch, { references: [], solverFactory: async () => solver });
    const spy = vi.spyOn(solver, 'dragTo');
    // A third direct locus deliberately disables projection.
    session.sketch = run(session.sketch, { kind: 'addConstraint', constraint: { kind: 'horizontal', a: 'a', b: 'b' } });
    session.sketch = run(session.sketch, { kind: 'addConstraint', constraint: { kind: 'pointOnLine', point: 'b', line: 'l' } });
    try {
      await session.beginDrag(['b']); await session.dragTo({ pointId: 'b', x: 11, y: 1 });
      const calls = spy.mock.calls.length; await session.endDrag(); expect(spy).toHaveBeenCalledTimes(calls);
    } finally { spy.mockRestore(); session.destroy(); }
  });
  it('does not re-seed a native pass whose cursor error did not improve', async () => {
    const sketch = { entities: [{ kind: 'point' as const, id: 'a', x: 0, y: 0, fixed: true },
      { kind: 'point' as const, id: 'b', x: 10, y: 0 }, { kind: 'line' as const, id: 'l', p1: 'a', p2: 'b' }],
      constraints: [{ id: 'h', kind: 'horizontal' as const, line: 'l' }] };
    const solver = await createSolver('slvs'); solver.load(sketch); solver.beginDrag(['b']);
    const spy = vi.spyOn(capture.slvs!, 'solveSketch');
    try { solver.dragTo([{ pointId: 'b', x: 10, y: 5 }]); expect(spy).toHaveBeenCalledTimes(1); }
    finally { spy.mockRestore(); solver.destroy(); }
  });
});

it('adds a reference distance directly and diagnoses it ok', async () => {
  const sketch = line(), session = await createSession(sketch, { references: [] });
  try {
    const result = await session.tryAddConstraint(sketch, { kind: 'distance', a: 'a', b: 'b', value: 7, driving: false });
    expect(result.ok).toBe(true); if (result.ok) { expect(result.diagnosis.constraintStates[result.id]).toBe('ok'); expect(result.sketch.constraints[0]).toMatchObject({ value: 10, driving: false }); }
  } finally { session.destroy(); }
});

it.each<Command>([{ kind: 'setConstruction', id: 'axis', construction: false }, { kind: 'setEntityData', id: 'axis', data: {} },
  { kind: 'movePoint', id: '', x: 2, y: 4 }, { kind: 'splitAt', entity: 'axis', x: 0, y: .5 }])('refuses reference mutation $kind', command => {
  const { sketch } = seed(INITIAL_SOURCE), axis = sketch.entities.find(e => e.id === 'axis')!;
  if (axis.kind !== 'line') throw new Error('line');
  const input = command.kind === 'movePoint' ? { ...command, id: axis.p1 } : command;
  expect(applyCommand(sketch, input)).toHaveProperty('error');
});
it('validates command output once and refuses an unsupported imported entity', () => {
  const sketch = line(); sketch.entities.push({ kind: 'circle', id: 'circle' });
  const spy = vi.spyOn(model, 'validateSketch');
  try {
    const fixed = applyCommand(sketch, { kind: 'deleteEntity', id: 'circle' });
    expect(fixed).toHaveProperty('sketch'); expect(spy).toHaveBeenCalledTimes(1);
    const broken = applyCommand(sketch, { kind: 'addPoint', x: 1, y: 2 });
    expect(broken).toHaveProperty('error'); expect(spy).toHaveBeenCalledTimes(2);
  } finally { spy.mockRestore(); }
});
it('reports a grid-snapped pole without an axis lock as a pole problem', () => {
  const imported = seed(INITIAL_SOURCE), axis = imported.meta.references.find(r => r.kind === 'axis')!.id;
  const sketch = { ...imported.sketch, constraints: imported.sketch.constraints.filter(c => c.kind !== 'pointOnLine' || c.line !== axis) };
  const expected = [{ code: 'pole', ids: [INITIAL_SOURCE.contour.points[0].id], message: 'Lock the pole to the axis' }];
  expect(rules(sketch, imported.meta)).toEqual(expected); expect(lower(sketch, imported.meta)).toEqual({ ok: false, problems: expected });
});

it.each(['solveFull', 'tryAddConstraint'] as const)('serialises %s after dimension work', async operation => {
  const sketch = run(line(), { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } });
  const solver = await createSolver('slvs'), session = await createSession(sketch, { references: [], solverFactory: async () => solver });
  // Record the dimension seen by each full diagnosis, including the internal final diagnosis.
  const nativeSolve = solver.solve.bind(solver), events: number[] = [];
  const spy = vi.spyOn(solver, 'solve').mockImplementation(options => {
    if (options.mode === 'full') { const c = session.sketch.constraints.find(c => c.id === sketch.constraints[0].id)!; events.push('value' in c ? c.value : -1); }
    return nativeSolve(options);
  });
  try {
    const edit = session.setDimensionContinuation(sketch.constraints[0].id, 20);
    const other = operation === 'solveFull' ? session.solveFull() : session.tryAddConstraint(sketch, { kind: 'horizontal', a: 'a', b: 'b' });
    await Promise.all([edit, other]); expect(events[0]).toBe(20);
  } finally { spy.mockRestore(); session.destroy(); }
});

it('keeps 24-segment full diagnosis bounded by skipping expensive point probes', async () => {
  const solver = await createSolver('slvs'), sketch = contourChain(24).sketch;
  solver.load(sketch);
  try {
    const started = performance.now(), result = solver.solve({ mode: 'full' });
    measurements.pointDetermination = { totalMs: performance.now() - started, nativeSolveMs: result.timings.solveMs, updateMs: result.timings.updateMs, entities: sketch.entities.length };
    expect(result.status).toBe('ok'); expect(result.pointDetermined).toBeUndefined();
  } finally { solver.destroy(); }
});

it('uses about one native solve per reachable frame at 24 segments', async () => {
  const input = contourChain(24).sketch;
  let sketch = importedSketch(input.entities.flatMap(e => e.kind === 'point' ? [{ id: e.id, x: e.x, y: e.y }] : []),
    input.entities.flatMap((e): model.Entity[] => e.kind === 'point' ? [] : e.kind === 'arc' ? [{ kind: 'arc', id: e.id, center: e.center, start: e.start, end: e.end, ccw: true }] : [{ ...e }]));
  for (const c of input.constraints) { const { id: _, ...constraint } = c; sketch = run(sketch, { kind: 'addConstraint', constraint }); }
  sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point: 'origin' } });
  const session = await createSession(sketch, { references: [] }), rim = point(sketch, 'rim');
  const native = vi.spyOn(capture.slvs!, 'solveSketch'), calls: number[] = [], frameMs: number[] = [];
  try {
    await session.beginDrag(['rim']);
    for (let frame = 1; frame <= 60; frame++) {
      const count = native.mock.calls.length, start = performance.now();
      const result = await session.dragTo({ pointId: 'rim', x: rim.x + .3 * Math.sin(frame * Math.PI / 30), y: 0 });
      frameMs.push(performance.now() - start); calls.push(native.mock.calls.length - count); expect(result.stopped).toBe(false);
    }
    const average = calls.reduce((a, b) => a + b, 0) / calls.length;
    measurements.drag24 = { frames: calls.length, nativeSolves: calls.reduce((a, b) => a + b, 0), solvesPerFrame: average,
      maxSolvesPerFrame: Math.max(...calls), medianFrameMs: frameMs.sort((a, b) => a - b)[30], maxFrameMs: Math.max(...frameMs) };
    expect(average).toBeLessThanOrEqual(1.2); expect((await session.endDrag()).status).toBe('ok');
  } finally { native.mockRestore(); session.destroy(); }
});

it.each(['horizontal', 'vertical'] as const)('projects a pair-form %s target without dragging its other point', async kind => {
  const sketch = run(line(), { kind: 'addConstraint', constraint: { kind, a: 'a', b: 'b' } });
  if (kind === 'vertical') sketch.points[1] = { id: 'b', x: 0, y: 10 };
  const session = await createSession(sketch, { references: [] });
  try {
    await session.beginDrag(['b']);
    const target = kind === 'horizontal' ? { pointId: 'b', x: 12, y: 3 } : { pointId: 'b', x: 3, y: 12 };
    await session.dragTo(target); await session.endDrag();
    expect(point(session.sketch, 'a')).toEqual(point(sketch, 'a'));
    expect(point(session.sketch, 'b')).toEqual({ id: 'b', x: kind === 'horizontal' ? 12 : 0, y: kind === 'horizontal' ? 0 : 12 });
  } finally { session.destroy(); }
});

it('allows a reachable hinged bar to move its far end further than the dragged point', async () => {
  let sketch = importedSketch([{ id: 'hinge', x: 0, y: 0 }, { id: 'near', x: 1, y: 0 }, { id: 'far', x: 10, y: 0 }],
    [{ kind: 'line', id: 'bar', p1: 'hinge', p2: 'far' }, { kind: 'line', id: 'arm', p1: 'hinge', p2: 'near' }]);
  for (const constraint of [{ kind: 'fix', point: 'hinge' }, { kind: 'distance', a: 'hinge', b: 'near', value: 1, driving: true },
    { kind: 'distance', a: 'hinge', b: 'far', value: 10, driving: true }, { kind: 'parallel', a: 'arm', b: 'bar' }] as model.NewConstraint[]) {
    sketch = run(sketch, { kind: 'addConstraint', constraint });
  }
  const session = await createSession(sketch, { references: [] });
  try {
    await session.beginDrag(['near']);
    expect((await session.dragTo({ pointId: 'near', x: 0, y: 1 })).stopped).toBe(false);
    expect((await session.endDrag()).status).toBe('ok');
    expect(point(session.sketch, 'near').y).toBeCloseTo(1, 6); expect(point(session.sketch, 'far').y).toBeCloseTo(10, 6);
  } finally { session.destroy(); }
});

it.each([{ decay: .6, passes: 1 }, { decay: .1, passes: 6 }])('bounds native refinement when error decays by $decay per pass', async ({ decay, passes }) => {
  const solver = await createSolver('slvs');
  solver.load({ entities: [{ kind: 'point', id: 'p', x: 0, y: 0 }], constraints: [] });
  const native = capture.slvs!, add = native.addPoint2D.bind(native); let param = 0;
  const handle = vi.spyOn(native, 'addPoint2D').mockImplementation((...args) => { const p = add(...args); param = p.param[0]; return p; });
  solver.beginDrag(['p']); handle.mockRestore();
  const original = native.solveSketch.bind(native); let error = 10;
  const solve = vi.spyOn(native, 'solveSketch').mockImplementation((...args) => {
    const result = original(...args); error *= decay; native.setParamValue(param, 10 - error); return result;
  });
  try { solver.dragTo([{ pointId: 'p', x: 10, y: 0 }]); expect(solve).toHaveBeenCalledTimes(passes); }
  finally { solve.mockRestore(); solver.destroy(); }
});

it.each(['solveFull', 'tryAddConstraint'] as const)('waits for active drag work before refusing %s', async operation => {
  const sketch = line(), session = await createSession(sketch, { references: [] }), events: string[] = [];
  try {
    await session.beginDrag(['b']);
    const drag = session.dragTo({ pointId: 'b', x: 30, y: 0 }).then(() => events.push('drag'));
    const other = (operation === 'solveFull' ? session.solveFull() : session.tryAddConstraint(sketch, { kind: 'horizontal', a: 'a', b: 'b' }))
      .catch(() => events.push('refused'));
    await Promise.all([drag, other]); expect(events).toEqual(['drag', 'refused']);
    expect(point(session.sketch, 'b').x).toBe(30); await session.endDrag();
  } finally { session.destroy(); }
});
