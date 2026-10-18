import type { Arc, Point, Sketch } from './model';
export const tau = 2 * Math.PI;
export const positiveAngle = (a: number) => ((a % tau) + tau) % tau;
export const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
export function arcGeometry(s: Sketch, a: Arc) {
  const c = s.points.find(p => p.id === a.center)!, p = s.points.find(p => p.id === a.start)!, q = s.points.find(p => p.id === a.end)!;
  const start = Math.atan2(p.y - c.y, p.x - c.x), end = Math.atan2(q.y - c.y, q.x - c.x);
  const sweep = positiveAngle(end - start) - (a.ccw ? 0 : tau);
  return { center: c, radius: distance(c, p), endRadius: distance(c, q), start, sweep };
}
export function onArc(s: Sketch, a: Arc, p: Omit<Point, 'id'>, interior = false) {
  const g = arcGeometry(s, a), angle = Math.atan2(p.y - g.center.y, p.x - g.center.x);
  const along = positiveAngle(a.ccw ? angle - g.start : g.start - angle), span = Math.abs(g.sweep);
  return Math.abs(distance(g.center, p) - g.radius) <= 1e-9 * Math.max(1, g.radius)
    && along <= span + 1e-12 && (!interior || along > 1e-12 && along < span - 1e-12);
}
