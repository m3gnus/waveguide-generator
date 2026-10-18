// @vitest-environment node
import { describe, expect, it, vi } from 'vitest';
import type { SlvsModule } from 'slvs';
import { applyCommand, importedSketch } from './ops';
import { numericSuffix, type Arc, type Entity, type Sketch } from './model';
import type { Command } from './commands';
import { branchCases } from './solver/corpus';
import { createSolver, type Sketch as SolverSketch } from './solver/types';
import { createSession, toSolverSketch } from './session';
import { arcGeometry } from './geometry';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';
import { seed } from './adapters/sourceContour';

const capture = vi.hoisted(() => ({ slvs: undefined as SlvsModule | undefined }));
vi.mock('slvs', async importOriginal => {
  const actual = await importOriginal<typeof import('slvs')>();
  return { ...actual, default: async (...args: unknown[]) => {
    capture.slvs = await (actual.default as (...a: unknown[]) => ReturnType<typeof actual.default>)(...args); return capture.slvs;
  } };
});
function run(s: Sketch, command: Command) { const r = applyCommand(s, command); if ('error' in r) throw new Error(r.error); return r.sketch; }
function fromSolver(input: SolverSketch): Sketch {
  let s = importedSketch(input.entities.flatMap(e => e.kind === 'point' ? [{ id: e.id, x: e.x, y: e.y }] : []),
    input.entities.flatMap((e): Entity[] => e.kind === 'point' ? [] : e.kind === 'arc' ? [{ kind: 'arc', id: e.id, center: e.center, start: e.start, end: e.end, ccw: true }] : [{ ...e }]));
  s = { ...s, constraints: input.constraints, nextId: Math.max(s.nextId, 1 + Math.max(0, ...input.constraints.map(c => numericSuffix(c.id)))) };
  for (const p of input.entities) if (p.kind === 'point' && p.fixed) s = run(s, { kind: 'addConstraint', constraint: { kind: 'fix', point: p.id } });
  return s;
}
const lineSketch = () => importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }], [{ kind: 'line', id: 'line', p1: 'a', p2: 'b' }]);
describe('solver session with real slvs', () => {
  it('converts clockwise arcs without changing their model order, and fixes reference points', async () => {
    const { sketch, meta } = seed(INITIAL_SOURCE);
    const doc = structuredClone(INITIAL_SOURCE); doc.contour.points[0].z_mm = 3;
    doc.contour.segments[0] = { ...doc.contour.segments[0], kind: 'arc', center_mm: [0, (9 - 64) / 6], direction: 'cw' };
    const imported = seed(doc), arc = imported.sketch.entities.find(e => e.kind === 'arc') as Arc;
    const converted = toSolverSketch(imported.sketch, imported.meta.references);
    expect(converted.entities.find(e => e.id === arc.id)).toMatchObject({ start: arc.end, end: arc.start, direction: 'ccw' });
    const session = await createSession(imported.sketch, { references: imported.meta.references });
    try {
      const result = await session.solveFull(); expect(result.status).toBe('ok');
      expect(result.sketch.entities.find(e => e.id === arc.id)).toEqual(arc);
      expect(result.dof).toBeGreaterThan(0); expect(result.pointDetermined).toBeDefined();
    } finally { session.destroy(); }
    const fixed = toSolverSketch(sketch, meta.references).entities.filter(e => e.kind === 'point' && e.fixed);
    expect(fixed).toHaveLength(4);
  });
  it('refuses conflicting constraints and restores the original snapshot', async () => {
    let sketch = lineSketch();
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } });
    const existing = sketch.constraints[0].id, session = await createSession(sketch, { references: [] });
    try {
      const refused = await session.tryAddConstraint(sketch, { kind: 'distance', a: 'a', b: 'b', value: 20, driving: true });
      expect(refused.ok).toBe(false); expect(refused.sketch).toBe(sketch);
      expect(!refused.ok && refused.conflicting).toContain(existing);
      expect((await session.solveFull()).status).toBe('ok');
      const invalid = await session.tryAddConstraint(sketch, { kind: 'tangent', a: 'line', b: 'line' });
      expect(invalid).toMatchObject({ ok: false, reason: expect.stringContaining('entity kinds') });
    } finally { session.destroy(); }
  });
  it('refuses a redundant dimension or keeps it explicitly as a reference', async () => {
    let sketch = lineSketch();
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } });
    const session = await createSession(sketch, { references: [] });
    try {
      const dimension = { kind: 'distance' as const, a: 'a', b: 'b', value: 10, driving: true };
      const refused = await session.tryAddConstraint(sketch, dimension); expect(refused.ok).toBe(false);
      expect(!refused.ok && refused.redundant.length).toBeGreaterThan(0);
      const reference = await session.tryAddConstraint(sketch, dimension, { ifRedundant: 'reference' });
      expect(reference.ok).toBe(true);
      if (reference.ok) {
        expect(reference.sketch.constraints.find(c => c.id === reference.id)).toMatchObject({ driving: false });
        expect(reference.diagnosis.constraintStates[reference.id]).toBe('ok');
      }
    } finally { session.destroy(); }
  });
  it('solves reachable multi-point drags in small steps and diagnoses only on release', async () => {
    const session = await createSession(lineSketch(), { references: [] });
    const native = vi.spyOn(capture.slvs!, 'solveSketch');
    try {
      await session.beginDrag(['a', 'b']);
      const result = await session.dragTo([{ pointId: 'b', x: 15, y: 4 }, { pointId: 'a', x: 5, y: 4 }]);
      expect(result.stopped).toBe(false); expect(result.steps).toBeGreaterThan(1);
      expect(session.sketch.points).toEqual([{ id: 'a', x: 5, y: 4 }, { id: 'b', x: 15, y: 4 }]);
      expect(native.mock.calls.every(args => args[1] === false)).toBe(true);
      const diagnosed = await session.endDrag(); expect(diagnosed.status).toBe('ok'); expect(diagnosed.dof).toBe(4);
      expect(native.mock.calls.some(args => args[1] === true)).toBe(true);
    } finally { native.mockRestore(); session.destroy(); }
  });
  it.each(branchCases)('keeps the moving branch: $name', async sample => {
    const sketch = fromSolver(sample.sketch), session = await createSession(sketch, { references: [] });
    const arc = sketch.entities.find(e => e.kind === 'arc') as Arc, initial = arcGeometry(sketch, arc).sweep;
    try {
      await session.beginDrag(['e']); let moved = 0, last = session.sketch.points.find(p => p.id === 'e')!;
      const safeSweep = initial < Math.PI && initial > Math.PI / 2 ? initial - .005 : initial + .005;
      const center = sketch.points.find(p => p.id === arc.center)!, radius = arcGeometry(sketch, arc).radius, start = arcGeometry(sketch, arc).start;
      const safeTarget = { pointId: 'e', x: center.x + radius * Math.cos(start + safeSweep), y: center.y + radius * Math.sin(start + safeSweep) };
      for (const target of [safeTarget, ...sample.targets]) {
        const result = await session.dragTo(target); expect(result.steps).toBeLessThanOrEqual(64);
        const sweep = arcGeometry(session.sketch, arc).sweep;
        expect(Math.sign(sweep)).toBe(Math.sign(initial)); expect((sweep - Math.PI) * (initial - Math.PI)).toBeGreaterThanOrEqual(0);
        const p = session.sketch.points.find(p => p.id === 'e')!; moved += Math.hypot(p.x - last.x, p.y - last.y); last = p;
      }
      expect(moved).toBeGreaterThan(1e-5); expect((await session.endDrag()).status).toBe('ok');
    } finally { session.destroy(); }
  });
  it('halts near the semicircle boundary without accepting a branch flip', async () => {
    const sample = branchCases[1], session = await createSession(fromSolver(sample.sketch), { references: [] });
    try {
      await session.beginDrag(['e']); const result = await session.dragTo(sample.targets[2]);
      const arc = session.sketch.entities.find(e => e.kind === 'arc') as Arc;
      expect(result.stopped).toBe(true); expect(arcGeometry(session.sketch, arc).sweep).toBeLessThanOrEqual(Math.PI);
      expect(session.sketch.points.find(p => p.id === 'e')!.y).toBeGreaterThanOrEqual(0);
      await session.endDrag();
    } finally { session.destroy(); }
  });
  it('rolls back an impossible drag to the last good position', async () => {
    let sketch = lineSketch(); sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point: 'a' } });
    const session = await createSession(sketch, { references: [] });
    try {
      await session.beginDrag(['a']); const result = await session.dragTo({ pointId: 'a', x: 50, y: 50 });
      expect(result.stopped).toBe(true); expect(Math.hypot(session.sketch.points[0].x, session.sketch.points[0].y)).toBeLessThan(1e-9);
      expect((await session.endDrag()).status).toBe('ok');
    } finally { session.destroy(); }
  });
  it('continues dimension edits and refuses a fold of an all-line linkage', async () => {
    let sketch = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'p', x: 3, y: 4 }, { id: 'b', x: 10, y: 0 }],
      [{ kind: 'line', id: 'left', p1: 'a', p2: 'p' }, { kind: 'line', id: 'right', p1: 'p', p2: 'b' }]);
    for (const point of ['a', 'b']) sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'fix', point } });
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'p', value: 5, driving: true } });
    sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distanceY', a: 'a', b: 'p', value: 4, driving: true } });
    const id = sketch.constraints.at(-1)!.id, session = await createSession(sketch, { references: [] });
    try {
      expect((await session.setDimensionContinuation(id, 3.5)).ok).toBe(true);
      const before = session.sketch, refused = await session.setDimensionContinuation(id, -4);
      expect(refused.ok).toBe(false); expect(refused.sketch).toBe(before);
      expect(session.sketch.points.find(p => p.id === 'p')!.y).toBeCloseTo(3.5, 8);
      expect((await session.setDimensionContinuation('missing', 1)).ok).toBe(false);
      expect((await session.setDimensionContinuation(id, NaN)).ok).toBe(false);
    } finally { session.destroy(); }
  });
  it.each(['full', 'drag', 'dimension'] as const)('recovers a native WASM trap once during %s with identical results', async mode => {
    let sketch = lineSketch(); sketch = run(sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } });
    const id = sketch.constraints[0].id, baseline = await createSession(sketch, { references: [] }), recovered = await createSession(sketch, { references: [] });
    const perform = async (s: typeof baseline) => {
      if (mode === 'full') return (await s.solveFull()).points;
      if (mode === 'dimension') { expect((await s.setDimensionContinuation(id, 12)).ok).toBe(true); return s.sketch.points; }
      await s.beginDrag(['b']); await s.dragTo({ pointId: 'b', x: 8, y: 6 }); const r = await s.endDrag(); return r.points;
    };
    let trap: ReturnType<typeof vi.spyOn> | undefined;
    try {
      const expected = await perform(baseline), previous = capture.slvs!;
      trap = vi.spyOn(previous, 'solveSketch').mockImplementationOnce(() => { throw new WebAssembly.RuntimeError('test trap'); });
      const actual = await perform(recovered);
      expect(actual).toEqual(expected); expect(recovered.stats.recoveries).toBe(1); expect(capture.slvs).not.toBe(previous);
      expect((await recovered.solveFull()).status).toBe('ok');
    } finally { trap?.mockRestore(); baseline.destroy(); recovered.destroy(); }
  });
  it('recovers a retired error from an adapter trapped by another owner', async () => {
    const session = await createSession(lineSketch(), { references: [] }), other = await createSolver('slvs'), native = capture.slvs!;
    const trap = vi.spyOn(native, 'solveSketch').mockImplementationOnce(() => { throw new WebAssembly.RuntimeError('test'); });
    try {
      other.load(toSolverSketch(lineSketch(), [])); expect(() => other.solve({ mode: 'full' })).toThrow(WebAssembly.RuntimeError);
      expect((await session.solveFull()).status).toBe('ok'); expect(session.stats.recoveries).toBe(1);
    } finally { trap.mockRestore(); other.destroy(); session.destroy(); }
  });
  it('retries a repeated trap only once and preserves the last good drag snapshot', async () => {
    const traps: ReturnType<typeof vi.spyOn>[] = [];
    const factory = vi.fn(async () => {
      const solver = await createSolver('slvs');
      traps.push(vi.spyOn(capture.slvs!, 'solveSketch').mockImplementation(() => { throw new WebAssembly.RuntimeError('persistent trap'); }));
      return solver;
    });
    const sketch = lineSketch(), session = await createSession(sketch, { solverFactory: factory, references: [] });
    try {
      await session.beginDrag(['b']);
      await expect(session.dragTo({ pointId: 'b', x: 11, y: 1 })).rejects.toThrow('persistent trap');
      expect(factory).toHaveBeenCalledTimes(2); expect(session.sketch).toBe(sketch);
    } finally { for (const trap of traps) trap.mockRestore(); session.destroy(); }
  });
  it('reports wall-clock solves over 250ms without adding a worker', async () => {
    const session = await createSession(lineSketch(), { references: [] }), original = capture.slvs!.solveSketch.bind(capture.slvs);
    let now = 0;
    const clock = vi.spyOn(performance, 'now').mockImplementation(() => now);
    const solve = vi.spyOn(capture.slvs!, 'solveSketch').mockImplementation((...args) => { const r = original(...args); now += 251; return r; });
    try { await session.solveFull(); expect(session.stats.slowSolves).toBe(1); expect(session.stats.maxSolveMs).toBeGreaterThanOrEqual(251); }
    finally { solve.mockRestore(); clock.mockRestore(); session.destroy(); }
  });
  it('rejects malformed lifecycle calls', async () => {
    const session = await createSession(lineSketch(), { references: [] });
    await expect(session.dragTo({ pointId: 'a', x: 0, y: 0 })).rejects.toThrow('beginDrag');
    await expect(session.endDrag()).rejects.toThrow(); await expect(session.beginDrag(['missing'])).rejects.toThrow();
    await session.beginDrag(['a']); await expect(session.beginDrag(['a'])).rejects.toThrow();
    await expect(session.solveFull()).rejects.toThrow(); await expect(session.dragTo({ pointId: 'b', x: 0, y: 0 })).rejects.toThrow();
    await session.endDrag(); session.destroy(); session.destroy(); await expect(session.solveFull()).rejects.toThrow('destroyed');
  });
});

it('caches native point determination and skips probing above 64 entities', async () => {
  const small = await createSession(lineSketch(), { references: [] }), native = vi.spyOn(capture.slvs!, 'solveSketch');
  try {
    expect((await small.solveFull()).pointDetermined).toEqual({ a: false, b: false });
    expect(native).toHaveBeenCalledTimes(3);
    await small.solveFull(); expect(native).toHaveBeenCalledTimes(4);
  } finally { native.mockRestore(); small.destroy(); }
  const large = await createSession(importedSketch(Array.from({ length: 65 }, (_, i) => ({ id: `p${i}`, x: i, y: 0 })), []), { references: [] });
  const probe = vi.spyOn(capture.slvs!, 'solveSketch');
  try { expect((await large.solveFull()).pointDetermined).toBeUndefined(); expect(probe).toHaveBeenCalledTimes(1); }
  finally { probe.mockRestore(); large.destroy(); }
});

describe('drag tolerance', () => {
  it('accepts frames within the UI tolerance and lands exactly on release', async () => {
    const s0 = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }],
      [{ kind: 'line', id: 'l', p1: 'a', p2: 'b' }] as Entity[]);
    const fixed = applyCommand(s0, { kind: 'addConstraint', constraint: { kind: 'fix', point: 'a' } } as Command);
    if ('error' in fixed) throw new Error(fixed.error);
    const dimension = applyCommand(fixed.sketch, { kind: 'addConstraint', constraint: { kind: 'distance', a: 'a', b: 'b', value: 10, driving: true } } as Command);
    if ('error' in dimension) throw new Error(dimension.error);
    const session = await createSession(dimension.sketch, { references: [], dragTolerance: 1e-3 });
    try {
      await session.beginDrag(['b']);
      const target = { pointId: 'b', x: 6, y: 8 };
      for (let f = 1; f <= 20; f++) await session.dragTo({ pointId: 'b', x: 10 + (target.x - 10) * f / 20, y: target.y * f / 20 });
      const frame = session.sketch.points.find(p => p.id === 'b')!;
      expect(Math.hypot(frame.x - 6, frame.y - 8)).toBeLessThanOrEqual(2e-3);
      expect((await session.endDrag()).status).toBe('ok');
      const released = session.sketch.points.find(p => p.id === 'b')!;
      expect(Math.hypot(released.x - 6, released.y - 8)).toBeLessThan(1e-6);
    } finally { session.destroy(); }
  });
});
