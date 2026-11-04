import { coincidentPoints } from './coincidence';
import type { Arc, Line, Sketch } from './model';
import type { XY } from './solver/types';
import { arcGeometry, curveEnds, curveIntersections, curveParameter, distance, positiveAngle, projectToEntity, tau } from './geometry';

function curve(s: Sketch, id: string): Line | Arc {
  const e = s.entities.find(e => e.id === id);
  if (!e || e.kind !== 'line' && e.kind !== 'arc') throw new Error('This tool needs a line or arc. Select a curve.');
  if (e.reference) throw new Error('Reference curves cannot be edited. Select an authored line or arc.');
  return e;
}
export function filletGeometry(s: Sketch, point: string, radius: number) {
  if (!Number.isFinite(radius) || radius <= 0) throw new Error('Fillet radius must be positive. Enter a radius greater than zero.');
  const corner = s.points.find(p => p.id === point), cornerPoints = coincidentPoints(s, point);
  const curves = s.entities.filter((e): e is Line | Arc => (e.kind === 'line' && [e.p1, e.p2].some(id => cornerPoints.has(id))) || (e.kind === 'arc' && [e.start, e.end].some(id => cornerPoints.has(id))));
  if (!corner || curves.length !== 2 || curves.some(e => e.reference) || !!curves[0].construction !== !!curves[1].construction)
    throw new Error('Fillet needs a corner shared by exactly two curves with matching construction flags. Select the point where two lines meet.');
  if (curves.some(e => e.kind !== 'line')) throw new Error('Fillet currently supports line–line corners. Select the point where two lines meet.');
  const [a, b] = curves as [Line, Line], far = (e: Line) => s.points.find(p => p.id === (cornerPoints.has(e.p1) ? e.p2 : e.p1))!;
  const p = far(a), q = far(b), la = distance(corner, p), lb = distance(corner, q);
  if (Math.min(la, lb) <= 1e-9) throw new Error('Fillet needs two nonzero lines. Move the other endpoints away from the corner.');
  const u = { x: (p.x - corner.x) / la, y: (p.y - corner.y) / la }, v = { x: (q.x - corner.x) / lb, y: (q.y - corner.y) / lb };
  const angle = Math.acos(Math.max(-1, Math.min(1, u.x * v.x + u.y * v.y)));
  if (Math.abs(u.x * v.y - u.y * v.x) <= 1e-10) throw new Error('Collinear lines cannot form a fillet. Select a corner with a nonzero turn.');
  const offset = radius / Math.tan(angle / 2), max = Math.min(la, lb) * Math.tan(angle / 2);
  if (offset >= Math.min(la, lb) - 1e-9) throw new Error(`Fillet radius does not fit. Enter a radius smaller than ${max.toFixed(4)} mm.`);
  const start = { x: corner.x + offset * u.x, y: corner.y + offset * u.y }, end = { x: corner.x + offset * v.x, y: corner.y + offset * v.y };
  const k = radius / Math.sin(angle / 2) / Math.hypot(u.x + v.x, u.y + v.y);
  const center = { x: corner.x + (u.x + v.x) * k, y: corner.y + (u.y + v.y) * k };
  const ccw = (start.x - center.x) * (end.y - center.y) - (start.y - center.y) * (end.x - center.x) > 0;
  return { center, start, end, ccw, cornerPoints, curves: [a, b] as [Line, Line] };
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
  for (const cutter of s.entities) if (cutter.id !== id && (cutter.kind === 'line' || cutter.kind === 'arc')) {
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
  if (s.entities.some(other => other.id !== id && (other.kind === 'line' ? [other.p1, other.p2] : other.kind === 'arc' ? [other.start, other.end, other.center] : []).includes(from.id)))
    throw new Error('The endpoint is shared with another curve. Select a free endpoint to extend.');
  const candidates: { to: XY; cutter: Line | Arc; travel: number }[] = [];
  for (const cutter of s.entities) if (cutter.id !== id && (cutter.kind === 'line' || cutter.kind === 'arc')) {
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
