import { arcValues, entity, positiveAngle } from './common';
import type { Arc, Constraint, ConstraintKind, Entity, Point, Sketch, XY } from './types';
export const point = (id: string, x: number, y: number, fixed = false): Point => ({ kind: 'point', id, x, y, fixed });
export const line = (id: string, p1: string, p2: string): Entity => ({ kind: 'line', id, p1, p2 });
export const arc = (id: string, center: string, start: string, end: string): Arc => ({ kind: 'arc', id, center, start, end, direction: 'ccw' });
export const dim = (id: string, kind: 'distance' | 'distanceX' | 'distanceY' | 'angle', a: string, b: string, value: number): Constraint =>
  ({ id, kind, a, b, value, driving: true });
export interface Case {
  name: string; sketch: Sketch; expected: { dof?: number; valid: boolean; conflicting: string[];
    redundant?: string[]; partiallyRedundant?: string[]; note: string };
}
/** Exact initial geometry, independent of random numbers. x = radial r, y = axial z. */
export function contourChain(n: number): Case {
  if (!Number.isInteger(n) || n < 4 || n % 4) throw new Error('n must be a positive multiple of four');
  const entities: Entity[] = [point('origin', 0, 0, true)], constraints: Constraint[] = [];
  let x = 0, y = 0, heading = -0.7, previous = '';
  const length = 200 / n, turn = 1.2 / n;
  for (let i = 0; i < n; i++) {
    const start = i === 0 ? 'pole' : `start${i}`, end = i === n - 1 ? 'rim' : `end${i}`, seg = `seg${i}`;
    entities.push(point(start, x, y));
    if (i) constraints.push({ id: `joint${i}`, kind: 'coincident', a: previous, b: start });
    if (i % 2 === 0) {
      x += length * Math.cos(heading); y += length * Math.sin(heading);
      entities.push(point(end, x, y), line(seg, start, end));
      if (i % 4 === 0) constraints.push(dim(`length${i}`, 'distance', start, end, length));
    } else {
      const radius = length / turn, cx = x - radius * Math.sin(heading), cy = y + radius * Math.cos(heading);
      heading += turn; x = cx + radius * Math.sin(heading); y = cy - radius * Math.cos(heading);
      entities.push(point(`center${i}`, cx, cy), point(end, x, y), arc(seg, `center${i}`, start, end));
      if (i % 4 === 1) constraints.push({ id: `radius${i}`, kind: 'radius', arc: seg, value: radius, driving: true });
    }
    if (i) constraints.push({ id: `tangent${i}`, kind: 'tangent', a: `seg${i - 1}`, b: seg });
    previous = end;
  }
  // Translate all geometry except the reference origin to put the rim on z=0.
  for (const e of entities) if (e.kind === 'point' && e.id !== 'origin') e.y -= y;
  constraints.push(dim('pole-axis', 'distanceX', 'origin', 'pole', 0), dim('rim-baffle', 'distanceY', 'origin', 'rim', 0));
  return { name: `contour-${n}`, sketch: { entities, constraints }, expected: { dof: n + 1, valid: true, conflicting: [],
    note: `5n point variables - n/2 arc rules - 2(n-1) coincidence - (n-1) tangency - n/2 dimensions - 2 axis locks = n+1.` } };
}
/** All segment sizes and all but three arc sweeps dimensioned: four local DOF. */
export function contourChainConstrained(n: number): Case {
  const sample = contourChain(n), { sketch } = sample;
  const pts = new Map(sketch.entities.filter((e): e is Point => e.kind === 'point').map(p => [p.id, p]));
  let chords = Math.max(0, n / 2 - 3);
  for (const e of sketch.entities) {
    if (e.kind === 'line' && !sketch.constraints.some(c => c.id === `length${e.id.slice(3)}`)) {
      const a = pts.get(e.p1)!, b = pts.get(e.p2)!;
      sketch.constraints.push(dim(`size-${e.id}`, 'distance', e.p1, e.p2, Math.hypot(b.x - a.x, b.y - a.y)));
    }
    if (e.kind === 'arc') {
      if (!sketch.constraints.some(c => c.kind === 'radius' && c.arc === e.id)) {
        sketch.constraints.push({ id: `size-${e.id}`, kind: 'radius', arc: e.id, value: arcValues(sketch, e).radius, driving: true });
      }
      if (chords-- > 0) {
        const a = pts.get(e.start)!, b = pts.get(e.end)!;
        sketch.constraints.push(dim(`chord-${e.id}`, 'distance', e.start, e.end, Math.hypot(b.x - a.x, b.y - a.y)));
      }
    }
  }
  sample.name = `contour-constrained-${n}`;
  sample.expected.dof = Math.min(4, n / 2 + 1);
  sample.expected.note = 'Each additional length/radius fixes one size; each chord fixes one nonzero arc sweep locally. Three undimensioned sweeps plus overall heading/pole height minus rim-baffle lock leave four DOF.';
  return sample;
}
const baseLine = (): Sketch => ({ entities: [point('a', 0, 0), point('b', 10, 0), line('l', 'a', 'b')], constraints: [] });
function diagnostic(name: string, sketch: Sketch, dof: number | undefined, valid: boolean, conflicting: string[], note: string,
  extra: Partial<Case['expected']> = {}): Case {
  return { name, sketch, expected: { dof, valid, conflicting, note, ...extra } };
}
export const diagnosticCases: Case[] = [
  diagnostic('free line', baseLine(), 4, true, [], 'Two independent free points.'),
  diagnostic('duplicate constraint', { ...baseLine(), constraints: [{ id: 'h1', kind: 'horizontal', line: 'l' },
    { id: 'h2', kind: 'horizontal', line: 'l' }] }, 3, true, [], 'One scalar equation duplicated.', { redundant: ['h1', 'h2'] }),
  diagnostic('horizontal + vertical collapse', { ...baseLine(), constraints: [{ id: 'h', kind: 'horizontal', line: 'l' },
    { id: 'v', kind: 'vertical', line: 'l' }] }, 2, true, [], 'Both equations hold at collapsed endpoints; geometrically degenerate.'),
  diagnostic('conflicting dimensions', { ...baseLine(), constraints: [dim('d10', 'distance', 'a', 'b', 10), dim('d20', 'distance', 'a', 'b', 20)] },
    3, false, ['d10', 'd20'], 'The same distance cannot be 10 and 20.'),
  diagnostic('two independent conflicts', { entities: [...baseLine().entities, point('c', 0, 20), point('d', 10, 20)],
    constraints: [dim('ab10', 'distance', 'a', 'b', 10), dim('ab20', 'distance', 'a', 'b', 20),
      dim('cd10', 'distance', 'c', 'd', 10), dim('cd20', 'distance', 'c', 'd', 20)] }, 6, false,
    ['ab10', 'ab20', 'cd10', 'cd20'], 'Two disconnected inconsistent one-dimensional distance equations.'),
  diagnostic('partial redundancy', { entities: [point('a', 0, 0, true), point('b', 0, 0), line('l', 'a', 'b')],
    constraints: [{ id: 'join', kind: 'coincident', a: 'a', b: 'b' }, { id: 'h', kind: 'horizontal', line: 'l' }] },
    0, true, [], 'Coincidence supplies x and y; horizontal repeats one of them.', { partiallyRedundant: ['h', 'join'] }),
  diagnostic('overdimensioned triangle', { entities: [point('a', 0, 0), point('b', 3, 0), point('c', 0, 4)],
    constraints: [dim('ab', 'distance', 'a', 'b', 3), dim('ac', 'distance', 'a', 'c', 4), dim('bc', 'distance', 'b', 'c', 5),
      dim('ab-again', 'distance', 'a', 'b', 3)] }, 3, true, [], 'Rigid 3-4-5 triangle retains three rigid-motion DOF.', { redundant: ['ab', 'ab-again'] }),
  diagnostic('fully constrained rectangle', { entities: [point('a', 0, 0, true), point('b', 10, 0), point('c', 10, 5), point('d', 0, 5),
    line('ab', 'a', 'b'), line('bc', 'b', 'c'), line('cd', 'c', 'd'), line('da', 'd', 'a')],
    constraints: [{ id: 'h1', kind: 'horizontal', line: 'ab' }, { id: 'h2', kind: 'horizontal', line: 'cd' },
      { id: 'v1', kind: 'vertical', line: 'bc' }, { id: 'v2', kind: 'vertical', line: 'da' },
      dim('width', 'distance', 'a', 'b', 10), dim('height', 'distance', 'a', 'd', 5)] }, 0, true, [], 'Six free coordinates, six independent equations.'),
  diagnostic('point on fixed arc', { entities: [point('c', 0, 0, true), point('s', 10, 0, true), point('e', 0, 10, true),
    point('p', 6, 6), arc('arc', 'c', 's', 'e')], constraints: [{ id: 'on', kind: 'pointOnArc', point: 'p', arc: 'arc' }] },
    1, true, [], 'Only the angular position of the point is free, within the first quadrant.'),
  diagnostic('zero length line', { entities: [point('a', 0, 0), point('b', 0, 0), line('l', 'a', 'b')], constraints: [] },
    4, true, [], 'No equations; four algebraic DOF even though the rendered segment is singular.'),
];
export interface BranchCase extends Case { targets: { pointId: string; x: number; y: number }[] }
export const branchCases: BranchCase[] = [0.0001, Math.PI - 0.0001, Math.PI + 0.0001].map(sweep => ({
  name: `CCW sweep ${sweep}`, sketch: { entities: [point('c', 0, 0, true), point('s', 10, 0, true),
    point('e', 10 * Math.cos(sweep), 10 * Math.sin(sweep)), arc('arc', 'c', 's', 'e')], constraints: [] },
  expected: { dof: 1, valid: true, conflicting: [], note: 'Fixed center and start set the radius; the end has one angular DOF.' },
  targets: [0.01, 0.02, 0.03].map(delta => ({ pointId: 'e', x: 10 * Math.cos(sweep + delta), y: 10 * Math.sin(sweep + delta) })),
}));
branchCases.push({ name: 'tangent arc crosses line and 100 mm cursor jump', sketch: {
  entities: [point('a', -10, 0, true), point('s', 0, 0, true), point('c', 0, 10), point('e', 10, 10),
    line('l', 'a', 's'), arc('arc', 'c', 's', 'e')], constraints: [{ id: 't', kind: 'tangent', a: 'l', b: 'arc' },
    { id: 'r', kind: 'radius', arc: 'arc', value: 10, driving: true }] },
  expected: { dof: 1, valid: true, conflicting: [], note: 'Tangent/radius fix center locally; endpoint rotates on the circle. Native branch flips are measured.' },
  targets: [{ pointId: 'e', x: 10, y: -10 }, { pointId: 'e', x: 110, y: -10 }, { pointId: 'e', x: 10, y: 10 }],
});
/** Small, perturbed systems: solving must actually change the residual. */
export function constraintCase(kind: ConstraintKind, variant = ''): Sketch {
  if (kind === 'pointLineDistance' && variant) {
    const y = (variant.includes('opposite') ? -1 : 1) * (variant.includes('fixed') ? 3 : 4), reversed = variant.includes('reversed');
    return { entities: [point('a', 0, 0, true), point('b', 10, 0, true), point('p', 4, y, variant.includes('fixed')),
      line('l', reversed ? 'b' : 'a', reversed ? 'a' : 'b')],
      constraints: [{ id: 'test', kind, point: 'p', line: 'l', value: 3, driving: true }] };
  }
  if (kind === 'angle' && variant) {
    const negative = variant.includes('negative'), mirror = variant.includes('mirrored'), reversed = variant.includes('reversed');
    const value = (negative ? -1 : 1) * Math.PI / 3;
    const heading = value + (reversed ? Math.PI : 0), seed = mirror ? -value : heading + 0.1;
    return { entities: [point('a', 0, 0, true), point('b', 10, 0, true), point('c', 0, 5, true),
      point('d', 10 * Math.cos(seed), 5 + 10 * Math.sin(seed)), line('l', reversed ? 'b' : 'a', reversed ? 'a' : 'b'), line('m', 'c', 'd')],
      constraints: [dim('test', kind, 'l', 'm', value)] };
  }
  const entities: Entity[] = [point('a', 0, 0, true), point('b', 10, 2), point('c', 0, 5, true), point('d', 9, 8),
    line('l', 'a', 'b'), line('m', 'c', 'd')];
  let c: Constraint;
  switch (kind) {
    case 'horizontal': case 'vertical': c = variant === 'pair' ? { id: 'test', kind, a: 'a', b: 'b' } : { id: 'test', kind, line: 'l' }; break;
    case 'coincident': c = { id: 'test', kind, a: 'a', b: 'b' }; break;
    case 'parallel': case 'perpendicular': case 'equal': c = { id: 'test', kind, a: 'l', b: 'm' }; break;
    case 'midpoint': case 'pointOnLine': c = { id: 'test', kind, point: 'd', line: 'l' }; break;
    case 'fix': c = { id: 'test', kind, point: 'b' }; break;
    case 'distance': case 'distanceX': case 'distanceY': c = dim('test', kind, 'a', 'b', variant === 'negative' ? -7 : 7); break;
    case 'pointLineDistance': c = { id: 'test', kind, point: 'd', line: 'l', value: 3, driving: true }; break;
    case 'angle': c = dim('test', kind, 'l', 'm', Math.PI / 3); break;
    case 'tangent': return { entities: [point('a', -10, 0, true), point('s', 0, 0, true), point('c', 1, 10), point('e', 10, 11),
      line('l', 'a', 's'), arc('arc', 'c', 's', 'e')], constraints: [{ id: 'test', kind, a: 'l', b: 'arc' }] };
    case 'pointOnArc': case 'radius': return { entities: [point('c', 0, 0, true), point('s', 10, 0), point('e', 0, 10),
      point('p', 6, 6), arc('arc', 'c', 's', 'e')], constraints: [kind === 'radius'
      ? { id: 'test', kind, arc: 'arc', value: 7, driving: true } : { id: 'test', kind, point: 'p', arc: 'arc' }] };
  }
  return { entities, constraints: [c] };
}
/** Independent geometric residuals, including arc membership and implicit equal-radius rule. */
export function residual(s: Sketch, c: Constraint, pts: Record<string, XY>): number {
  const p = (id: string) => pts[id], v = (id: string) => { const l = entity(s, id, 'line'); return sub(p(l.p2), p(l.p1)); };
  const norm = (v: XY) => Math.hypot(v.x, v.y), cross = (a: XY, b: XY) => a.x * b.y - a.y * b.x;
  const dist = (a: string, b: string) => norm(sub(p(a), p(b)));
  const radius = (id: string) => { const a = entity(s, id, 'arc'); return dist(a.center, a.start); };
  const lineDistance = (point: string, l: string) => { const e = entity(s, l, 'line'); return Math.abs(cross(v(l), sub(p(point), p(e.p1)))) / norm(v(l)); };
  switch (c.kind) {
    case 'coincident': return dist(c.a, c.b);
    case 'horizontal': case 'vertical': { const d = 'line' in c ? v(c.line) : sub(p(c.b), p(c.a)); return Math.abs(c.kind === 'horizontal' ? d.y : d.x); }
    case 'parallel': return Math.abs(cross(v(c.a), v(c.b))) / (norm(v(c.a)) * norm(v(c.b)));
    case 'perpendicular': { const a = v(c.a), b = v(c.b); return Math.abs(a.x * b.x + a.y * b.y) / (norm(a) * norm(b)); }
    case 'equal': return Math.abs(entityKind(s, c.a) === 'line' ? norm(v(c.a)) - norm(v(c.b)) : radius(c.a) - radius(c.b));
    case 'midpoint': { const l = entity(s, c.line, 'line'); return norm(sub(p(c.point), { x: (p(l.p1).x + p(l.p2).x) / 2, y: (p(l.p1).y + p(l.p2).y) / 2 })); }
    case 'pointOnLine': return lineDistance(c.point, c.line);
    case 'pointOnArc': { const a = entity(s, c.arc, 'arc'), center = p(a.center), start = sub(p(a.start), center), end = sub(p(a.end), center), q = sub(p(c.point), center);
      const angle = (v: XY) => Math.atan2(v.y, v.x), sweep = positiveAngle(angle(end) - angle(start)), t = positiveAngle(angle(q) - angle(start));
      return Math.max(Math.abs(norm(q) - norm(start)), t > sweep + 1e-7 ? Math.min(t - sweep, 2 * Math.PI - t) : 0); }
    case 'fix': { const a = entity(s, c.point, 'point'); return norm(sub(p(c.point), a)); }
    case 'distance': return Math.abs(dist(c.a, c.b) - c.value);
    case 'distanceX': return Math.abs(p(c.b).x - p(c.a).x - c.value);
    case 'distanceY': return Math.abs(p(c.b).y - p(c.a).y - c.value);
    case 'pointLineDistance': return Math.abs(lineDistance(c.point, c.line) - c.value);
    case 'angle': { const a = v(c.a), b = v(c.b); return Math.abs(Math.atan2(Math.sin(Math.atan2(cross(a, b), a.x * b.x + a.y * b.y) - c.value),
      Math.cos(Math.atan2(cross(a, b), a.x * b.x + a.y * b.y) - c.value))); }
    case 'radius': return Math.abs(radius(c.arc) - c.value);
    case 'tangent': {
      const a = s.entities.find(e => e.id === c.a), b = s.entities.find(e => e.id === c.b);
      if (a?.kind === 'arc' && b?.kind === 'arc') {
        const d = dist(a.center, b.center), r1 = radius(a.id), r2 = radius(b.id);
        return Math.min(Math.abs(d - r1 - r2), Math.abs(d - Math.abs(r1 - r2)));
      }
      const curve = entity(s, a?.kind === 'arc' ? c.a : c.b, 'arc');
      return Math.abs(lineDistance(curve.center, a?.kind === 'line' ? c.a : c.b) - radius(curve.id));
    }
  }
}
function entityKind(s: Sketch, id: string) { return s.entities.find(e => e.id === id)?.kind; }
function sub(a: XY, b: XY): XY { return { x: a.x - b.x, y: a.y - b.y }; }
export function arcResidual(_s: Sketch, a: Arc, pts: Record<string, XY>) {
  return Math.abs(Math.hypot(pts[a.start].x - pts[a.center].x, pts[a.start].y - pts[a.center].y)
    - Math.hypot(pts[a.end].x - pts[a.center].x, pts[a.end].y - pts[a.center].y));
}
/** Initial analytic geometry values, useful to consumers constructing their own fixtures. */
export { arcValues };
