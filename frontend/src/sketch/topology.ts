import { internalHelper } from './dimensionGraphics';
import { coincidentPoints } from './coincidence';
import type { Arc, Line, Sketch } from './model';
import type { XY } from './solver/types';
import { arcGeometry, curveContains, curveEnds, curveIntersections, curveParameter, distance, intersectLoci, positiveAngle, projectToEntity, tau, type Locus } from './geometry';

function curve(s: Sketch, id: string): Line | Arc {
  const e = s.entities.find(e => e.id === id);
  if (!e || e.kind !== 'line' && e.kind !== 'arc') throw new Error('This tool needs a line or arc. Select a curve.');
  if (e.reference) throw new Error('Reference curves cannot be edited. Select an authored line or arc.');
  return e;
}
export function filletGeometry(s: Sketch, point: string, radius: number) {
  if (!Number.isFinite(radius) || radius <= 0) throw new Error('Fillet radius must be positive. Enter a radius greater than zero.');
  const corner = s.points.find(p => p.id === point), cornerPoints = coincidentPoints(s, point);
  const curves = s.entities.filter((e): e is Line | Arc => !internalHelper(e) && ((e.kind === 'line' && [e.p1, e.p2].some(id => cornerPoints.has(id))) || (e.kind === 'arc' && [e.start, e.end].some(id => cornerPoints.has(id)))));
  if (!corner || curves.length !== 2 || curves.some(e => e.reference) || !!curves[0].construction !== !!curves[1].construction)
    throw new Error('Fillet needs a corner shared by exactly two curves with matching construction flags. Select the point where two lines or arcs meet.');
  // A directed chain may wrap across the entity array's seam. Start at its incoming curve.
  if (cornerPoints.has(curves[0].kind === 'line' ? curves[0].p1 : curves[0].start)
    && cornerPoints.has(curves[1].kind === 'line' ? curves[1].p2 : curves[1].end)) curves.reverse();
  // Offset both supports toward the wedge between the two outgoing tangents.
  // Their intersections are fillet centres for lines and circles alike.
  const supports = curves.map(e => {
    const ends = curveEnds(s, e), atStart = cornerPoints.has(ends[0].id);
    if (atStart && cornerPoints.has(ends[1].id)) throw new Error('Fillet needs two open curve ends. Select a corner without a closed curve.');
    const near = ends[atStart ? 0 : 1], far = ends[atStart ? 1 : 0];
    if (e.kind === 'line') {
      const length = distance(near, far);
      if (length <= 1e-9) throw new Error('Fillet needs nonzero curves. Move the other endpoints away from the corner.');
      return { entity: e, near, far, atStart, length, u: { x: (far.x - near.x) / length, y: (far.y - near.y) / length } };
    }
    const g = arcGeometry(s, e), direction = e.ccw === atStart ? 1 : -1;
    if (g.radius <= 1e-9 || Math.abs(g.endRadius - g.radius) > 1e-7 || Math.abs(g.sweep) <= 1e-9)
      throw new Error('Fillet needs a valid nonzero arc. Adjust its endpoints onto its circle.');
    return { entity: e, near, far, atStart, length: g.radius * Math.abs(g.sweep), g,
      u: { x: -direction * (near.y - g.center.y) / g.radius, y: direction * (near.x - g.center.x) / g.radius } };
  });
  const [a, b] = supports, cross = a.u.x * b.u.y - a.u.y * b.u.x;
  if (Math.abs(cross) <= 1e-10) {
    if (!a.g && !b.g) throw new Error('Collinear lines cannot form a fillet. Select a corner with a nonzero turn.');
    if (a.u.x * b.u.x + a.u.y * b.u.y < 0) throw new Error('These curves already meet smoothly; nothing to round.');
    throw new Error('Collinear or tangent curves cannot form a fillet. Select a corner with a nonzero turn.');
  }
  const offsets = supports.map((support, i) => {
    const side = Math.sign(cross) * (i === 0 ? 1 : -1);
    const n = { x: -support.u.y * side, y: support.u.x * side };
    if (support.g) {
      const g = support.g, radial = { x: (support.near.x - g.center.x) / g.radius, y: (support.near.y - g.center.y) / g.radius };
      const signedRadius = g.radius + radius * (n.x * radial.x + n.y * radial.y);
      return { locus: { kind: 'circle', center: g.center, radius: Math.abs(signedRadius) } as Locus, signedRadius };
    }
    return { locus: { kind: 'line', origin: { x: support.near.x + radius * n.x, y: support.near.y + radius * n.y }, direction: support.u } as Locus };
  });
  const candidates = intersectLoci(offsets[0].locus, offsets[1].locus, corner).flatMap(center => {
    const contacts = supports.map((support, i) => {
      if (support.g) {
        const signedRadius = offsets[i].signedRadius!;
        if (Math.abs(signedRadius) <= 1e-9) return undefined;
        const k = support.g.radius / signedRadius;
        return { x: support.g.center.x + (center.x - support.g.center.x) * k, y: support.g.center.y + (center.y - support.g.center.y) * k };
      }
      const along = (center.x - support.near.x) * support.u.x + (center.y - support.near.y) * support.u.y;
      return { x: support.near.x + along * support.u.x, y: support.near.y + along * support.u.y };
    });
    if (contacts.some((p, i) => !p || !curveContains(s, supports[i].entity, p)
      || distance(p, supports[i].near) <= 1e-9 || distance(p, supports[i].far) <= 1e-9)) return [];
    const [start, end] = contacts as [XY, XY], ccw = cross < 0;
    const sweep = positiveAngle((ccw ? 1 : -1) * (Math.atan2(end.y - center.y, end.x - center.x) - Math.atan2(start.y - center.y, start.x - center.x)));
    // Reject the other intersection's looping/complementary arc.
    if (sweep <= 1e-9 || sweep > Math.PI + 1e-9) return [];
    const travel = contacts.reduce((sum, p, i) => {
      const t = curveParameter(s, supports[i].entity, p!);
      return sum + (supports[i].atStart ? t : 1 - t) * supports[i].length;
    }, 0);
    return [{ center, start, end, ccw, travel }];
  }).sort((a, b) => a.travel - b.travel);
  const result = candidates[0];
  if (!result) {
    const max = !a.g && !b.g ? Math.min(a.length, b.length) * Math.tan(Math.acos(Math.max(-1, Math.min(1, a.u.x * b.u.x + a.u.y * b.u.y))) / 2) : undefined;
    throw new Error(max === undefined ? 'Fillet radius does not fit these curve spans. Enter a smaller radius or lengthen the curves.'
      : `Fillet radius does not fit. Enter a radius smaller than ${max.toFixed(4)} mm.`);
  }
  return { ...result, cornerPoints, curves: curves as [Line | Arc, Line | Arc] };
}
export function filletPreview(s: Sketch, point: string, radius: number): { center: XY; start: XY; end: XY; ccw: boolean } | { error: string } {
  try { const { center, start, end, ccw } = filletGeometry(s, point, radius); return { center, start, end, ccw }; }
  catch (e) { return { error: (e as Error).message }; }
}
export interface Cut { point: XY; t: number; cutter: Line | Arc }
export function trimGeometry(s: Sketch, id: string, at: XY, unbounded: string[] = []) {
  const e = curve(s, id);
  if (!Number.isFinite(at.x + at.y)) throw new Error('Trim needs a finite click position. Click a curve.');
  const cuts: Cut[] = [];
  for (const cutter of s.entities) if (cutter.id !== id && !internalHelper(cutter) && (cutter.kind === 'line' || cutter.kind === 'arc')) {
    for (const point of curveIntersections(s, e, cutter, false, !!cutter.reference && unbounded.includes(cutter.id))) {
      const t = curveParameter(s, e, point);
      if (t > 1e-9 && t < 1 - 1e-9 && !cuts.some(c => distance(c.point, point) <= 1e-8)) cuts.push({ point, t, cutter });
    }
  }
  cuts.sort((a, b) => a.t - b.t);
  const t = curveParameter(s, e, projectToEntity(s, e.id, at.x, at.y));
  const low = cuts.filter(c => c.t <= t).at(-1), high = cuts.find(c => c.t > t), [a, b] = curveEnds(s, e);
  return { entity: e, low, high, start: low?.point ?? a, end: high?.point ?? b };
}
export type TrimPreview = { kind: 'line'; a: XY; b: XY } | { kind: 'arc'; center: XY; start: XY; end: XY; ccw: boolean };
export function trimPreview(s: Sketch, id: string, at: XY, unbounded: string[] = []): TrimPreview | { error: string } {
  try {
    const { entity: e, start, end } = trimGeometry(s, id, at, unbounded);
    return e.kind === 'line' ? { kind: 'line', a: start, b: end } : { kind: 'arc', center: arcGeometry(s, e).center, start, end, ccw: e.ccw };
  } catch (e) { return { error: (e as Error).message }; }
}
export function extendGeometry(s: Sketch, id: string, at: XY, unbounded: string[] = []) {
  const e = curve(s, id), [a, b] = curveEnds(s, e);
  if (!Number.isFinite(at.x + at.y)) throw new Error('Extend needs a finite click position. Click an endpoint.');
  const start = distance(at, a) <= distance(at, b), from = start ? a : b;
  if (s.entities.some(other => other.id !== id && !internalHelper(other) && (other.kind === 'line' ? [other.p1, other.p2] : other.kind === 'arc' ? [other.start, other.end, other.center] : []).includes(from.id)))
    throw new Error('The endpoint is shared with another curve. Select a free endpoint to extend.');
  const candidates: { to: XY; cutter: Line | Arc; travel: number }[] = [];
  for (const cutter of s.entities) if (cutter.id !== id && !internalHelper(cutter) && (cutter.kind === 'line' || cutter.kind === 'arc')) {
    for (const to of curveIntersections(s, e, cutter, true, !!cutter.reference && unbounded.includes(cutter.id))) {
      let travel: number;
      if (e.kind === 'line') { const t = curveParameter(s, e, to); travel = start ? -t : t - 1; }
      else {
        const g = arcGeometry(s, e), angle = Math.atan2(to.y - g.center.y, to.x - g.center.x), origin = Math.atan2(from.y - g.center.y, from.x - g.center.x);
        travel = positiveAngle((e.ccw !== start ? 1 : -1) * (angle - origin));
        if (travel >= tau - Math.abs(g.sweep) - 1e-9) continue;
      }
      if (travel > 1e-9) candidates.push({ to, cutter, travel });
    }
  }
  candidates.sort((a, b) => a.travel - b.travel); const hit = candidates[0];
  if (!hit) throw new Error('No curve is hit beyond this endpoint. Add a cutter or choose the other end.');
  return { entity: e, from, start, ...hit };
}
export function extendPreview(s: Sketch, id: string, at: XY, unbounded: string[] = []): ({ from: XY; to: XY } & ({ kind: 'line' } | { kind: 'arc'; center: XY; ccw: boolean })) | { error: string } {
  try { const g = extendGeometry(s, id, at, unbounded); return g.entity.kind === 'line' ? { kind: 'line', from: g.from, to: g.to }
    : { kind: 'arc', from: g.from, to: g.to, center: arcGeometry(s, g.entity).center, ccw: g.start ? !g.entity.ccw : g.entity.ccw }; }
  catch (e) { return { error: (e as Error).message }; }
}
