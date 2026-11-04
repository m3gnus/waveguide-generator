// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { arcFilletCorner } from './arcFilletTestSupport';
import { arcGeometry, curveEnds, distance } from './geometry';
import { History } from './history';
import { validateSketch, type Arc, type Line, type Sketch } from './model';
import { applyCommand } from './ops';
import { toSolverSketch } from './session';
import { residual } from './solver/corpus';
import { createSolver } from './solver/types';
import { filletPreview } from './topology';

const cases = [
  ['line', 'internal'], ['line', 'external'], ['internal', 'internal'],
  ['internal', 'external'], ['external', 'internal'], ['external', 'external'],
] as const;
function fillet(s: Sketch, point = 'corner', radius = 2) {
  const result = applyCommand(s, { kind: 'fillet', point, radius });
  if ('error' in result) throw new Error(result.error);
  expect(validateSketch(result.sketch)).toEqual({ ok: true });
  return result;
}
function assertGeometry(before: Sketch, after: Sketch, radius = 2) {
  const arc = after.entities[1] as Arc, g = arcGeometry(after, arc);
  expect(g.radius).toBeCloseTo(radius, 9); expect(g.endRadius).toBeCloseTo(radius, 9);
  expect(Math.abs(g.sweep)).toBeGreaterThan(0); expect(Math.abs(g.sweep)).toBeLessThan(Math.PI);
  for (const old of before.entities) {
    const e = after.entities.find(e => e.id === old.id) as Line | Arc;
    const oldEnds = curveEnds(before, old as Line | Arc), ends = curveEnds(after, e);
    const joined = ends.find(p => p.id === arc.start || p.id === arc.end)!;
    expect(joined).toBeDefined();
    expect(ends.find(p => p.id !== joined.id)).toEqual(oldEnds.find(p => p.id !== 'corner' && p.id !== 'alias'));
    if (e.kind === 'arc' && old.kind === 'arc') {
      const original = arcGeometry(before, old), shortened = arcGeometry(after, e);
      expect(shortened.center).toEqual(original.center); expect(shortened.radius).toBeCloseTo(original.radius, 9);
      expect(shortened.endRadius).toBeCloseTo(original.radius, 9); expect(e.ccw).toBe(old.ccw);
      expect(Math.abs(shortened.sweep)).toBeLessThan(Math.abs(original.sweep));
      // Circular radii at contact must be collinear.
      expect(Math.abs((joined.x - shortened.center.x) * (joined.y - g.center.y)
        - (joined.y - shortened.center.y) * (joined.x - g.center.x))).toBeLessThan(1e-7);
    } else if (e.kind === 'line') {
      const [a, b] = ends;
      expect(Math.abs((b.x - a.x) * (joined.x - g.center.x) + (b.y - a.y) * (joined.y - g.center.y))).toBeLessThan(1e-7);
    }
  }
  const first = after.entities[0] as Line | Arc, last = after.entities[2] as Arc;
  expect(first.kind === 'line' ? first.p2 : first.end).toBe(arc.start);
  expect(last.start).toBe(arc.end);
}

describe('line–arc and arc–arc fillets', () => {
  it.each(cases)('rounds %s / %s, keeps supporting circles and matches preview', (a, b) => {
    const s = arcFilletCorner(a, b), saved = structuredClone(s), preview = filletPreview(s, 'corner', 2);
    const { sketch: after } = fillet(s), arc = after.entities[1] as Arc;
    expect(s).toEqual(saved); assertGeometry(s, after);
    const xy = (id: string) => { const { x, y } = after.points.find(p => p.id === id)!; return { x, y }; };
    expect(preview).toMatchObject({ center: xy(arc.center), start: xy(arc.start), end: xy(arc.end), ccw: arc.ccw });
    expect(after.points.some(p => p.id === 'corner')).toBe(false);
    expect(after.constraints.filter(c => c.kind === 'tangent')).toHaveLength(2);
    expect(after.constraints).toContainEqual(expect.objectContaining({ kind: 'radius', arc: arc.id, value: 2, driving: true }));
  });
  it.each(cases)('solves %s / %s in SolveSpace without drift', async (a, b) => {
    const after = fillet(arcFilletCorner(a, b)).sketch, raw = toSolverSketch(after, []), solver = await createSolver('slvs');
    try {
      solver.load(raw); const solved = solver.solve({ mode: 'full' }); expect(solved.status, solved.reason).toBe('ok');
      for (const p of after.points) expect(distance(p, solved.points[p.id])).toBeLessThan(1e-6);
      for (const c of raw.constraints) expect(residual(raw, c, solved.points)).toBeLessThan(1e-6);
    } finally { solver.destroy(); }
  });
  it.each(cases)('keeps reverse chain order and clockwise/counterclockwise spans for %s / %s', (a, b) => {
    const s = arcFilletCorner(a, b);
    s.entities = s.entities.reverse().map(e => e.kind === 'line' ? { ...e, p1: e.p2, p2: e.p1 }
      : e.kind === 'arc' ? { ...e, start: e.end, end: e.start, ccw: !e.ccw } : e);
    const after = fillet(s).sketch, arc = after.entities[1] as Arc;
    expect(after.entities.map(e => e.id)).toEqual(['second', arc.id, 'first']);
    for (const old of s.entities) if (old.kind === 'arc') {
      const e = after.entities.find(e => e.id === old.id) as Arc;
      expect(e.ccw).toBe(old.ccw); expect(arcGeometry(after, e).radius).toBeCloseTo(10, 9);
      expect(Math.abs(arcGeometry(after, e).sweep)).toBeLessThan(Math.PI / 2);
    }
  });
  it.each(cases)('places the fillet across a chain seam for %s / %s', (a, b) => {
    const s = arcFilletCorner(a, b); s.entities.reverse();
    const after = fillet(s).sketch, arc = after.entities[2] as Arc;
    const incoming = after.entities.find(e => e.id === 'first') as Line | Arc, outgoing = after.entities[0] as Arc;
    expect(after.entities.map(e => e.id)).toEqual(['second', 'first', arc.id]);
    expect(incoming.kind === 'line' ? incoming.p2 : incoming.end).toBe(arc.start); expect(outgoing.start).toBe(arc.end);
    expect(arcGeometry(after, arc).radius).toBeCloseTo(2, 9);
  });
  it('uses the nearer contacts when both intersection solutions lie within major arc spans', () => {
    const s = arcFilletCorner('internal', 'internal'), expected = filletPreview(s, 'corner', 2);
    s.points = s.points.map(p => p.id === 'farA' ? { ...p, y: -10 } : p.id === 'farB' ? { ...p, x: -10 } : p);
    expect(filletPreview(s, 'corner', 2)).toEqual(expected); assertGeometry(s, fillet(s).sketch);
  });
  it('drops point relations on removed arc spans, keeps radius dimensions and retains a still-used corner', () => {
    const s = arcFilletCorner('line');
    s.points.push({ id: 'lost', x: Math.sin(.1) * 10, y: 10 - Math.cos(.1) * 10 },
      { id: 'kept', x: Math.sin(1) * 10, y: 10 - Math.cos(1) * 10 }, { id: 'extra', x: 0, y: 10 * Math.SQRT2 });
    s.entities.push({ id: 'aroundCorner', kind: 'arc', center: 'corner', start: 'farB', end: 'extra', ccw: true, construction: true });
    s.constraints = [{ id: 'outside', kind: 'pointOnArc', point: 'lost', arc: 'second' },
      { id: 'inside', kind: 'pointOnArc', point: 'kept', arc: 'second' }, { id: 'size', kind: 'radius', arc: 'second', value: 10, driving: true }];
    const result = fillet(s);
    expect(result.removedConstraints).toEqual(['outside']); expect(result.notice).toContain('Point on arc');
    expect(result.sketch.constraints.some(c => c.id === 'inside')).toBe(true); expect(result.sketch.constraints.some(c => c.id === 'size')).toBe(true);
    expect(result.sketch.points.find(p => p.id === 'corner')).toEqual(s.points.find(p => p.id === 'corner'));
  });
  it('refuses closed arc ends without creating a collapsed span', () => {
    const s = arcFilletCorner('line'); (s.entities[1] as Arc).end = 'corner'; (s.entities[1] as Arc).ccw = false;
    expect(filletPreview(s, 'corner', 2)).toHaveProperty('error', expect.stringContaining('open curve ends'));
  });
  it.each(cases)('refuses too-large %s / %s atomically', (a, b) => {
    const s = arcFilletCorner(a, b);
    // External/external quarter arcs can fit arbitrarily large radii; use short finite spans.
    for (const e of s.entities) {
      if (e.kind === 'line') s.points.find(p => p.id === e.p1)!.y = 1;
      else if (e.kind === 'arc') {
        const g = arcGeometry(s, e), fromStart = e.start === 'corner';
        const near = s.points.find(p => p.id === (fromStart ? e.start : e.end))!;
        const far = s.points.find(p => p.id === (fromStart ? e.end : e.start))!;
        const angle = Math.atan2(near.y - g.center.y, near.x - g.center.x) + (e.ccw === fromStart ? 1 : -1) * .1;
        far.x = g.center.x + g.radius * Math.cos(angle); far.y = g.center.y + g.radius * Math.sin(angle);
      }
    }
    const saved = structuredClone(s);
    expect(filletPreview(s, 'corner', 100)).toHaveProperty('error', expect.stringContaining('smaller radius'));
    expect(applyCommand(s, { kind: 'fillet', point: 'corner', radius: 100 })).toHaveProperty('error'); expect(s).toEqual(saved);
  });
  it('accepts a large radius when both external contacts still fit finite spans', () => {
    const s = arcFilletCorner('external', 'external'); assertGeometry(s, fillet(s, 'corner', 100).sketch, 100);
  });
  it.each(['line', 'internal'] as const)('refuses already-smooth %s–arc joins with plain guidance', first => {
    const s = arcFilletCorner(first);
    s.points = s.points.map(p => p.id === 'centerB' ? { ...p, x: -10, y: 0 } : p.id === 'farB' ? { ...p, x: -10, y: -10 } : p);
    (s.entities[1] as Arc).ccw = false;
    expect(filletPreview(s, 'corner', 2)).toEqual({ error: 'These curves already meet smoothly; nothing to round.' });
  });
  it('keeps coincident corners and fixes, replacing only old tangency in one undo step', () => {
    const s = arcFilletCorner('line'); s.points.push({ id: 'alias', x: 0, y: 0 });
    (s.entities[1] as Arc).start = 'alias';
    s.constraints = [{ id: 'joined', kind: 'coincident', a: 'corner', b: 'alias' }, { id: 'fixed', kind: 'fix', point: 'corner' },
      { id: 'smooth', kind: 'tangent', a: 'first', b: 'second' }, { id: 'size', kind: 'radius', arc: 'second', value: 10, driving: true }];
    const result = fillet(s, 'alias'); assertGeometry(s, result.sketch);
    expect(result.removedConstraints).toEqual(['smooth']);
    expect(result.notice).toContain('Tangent');
    expect(result.sketch.constraints).toEqual(expect.arrayContaining(s.constraints.filter(c => c.id !== 'smooth')));
    expect(result.sketch.constraints.some(c => c.id === 'size')).toBe(true);
    const history = new History(s); history.begin('fillet'); history.update(result.sketch); history.commit();
    expect(history.undo()).toEqual(s); expect(history.redo()).toEqual(result.sketch);
  });
});
