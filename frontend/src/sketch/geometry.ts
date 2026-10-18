import type { Arc, Point, Sketch } from './model';
export const tau = 2 * Math.PI;
export const positiveAngle = (a: number) => ((a % tau) + tau) % tau;
export const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
export function arcGeometry(s: Sketch, a: Arc, points?: Map<string, Point>) {
  const get = (id: string) => points ? points.get(id)! : s.points.find(p => p.id === id)!;
  const c = get(a.center), p = get(a.start), q = get(a.end);
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

export function projectToEntity(sketch: Sketch, entityId: string, x: number, y: number): { x: number; y: number } {
  const e = sketch.entities.find(e => e.id === entityId), cursor = { x, y };
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  if (!Number.isFinite(x + y)) throw new Error('Projection needs finite coordinates.');
  if (e?.kind === 'line') {
    const a = point(e.p1), b = point(e.p2), dx = b.x - a.x, dy = b.y - a.y;
    const t = Math.max(0, Math.min(1, ((x - a.x) * dx + (y - a.y) * dy) / (dx * dx + dy * dy) || 0));
    return { x: a.x + t * dx, y: a.y + t * dy };
  }
  if (e?.kind === 'arc') {
    const g = arcGeometry(sketch, e), angle = Math.atan2(y - g.center.y, x - g.center.x);
    const along = positiveAngle(e.ccw ? angle - g.start : g.start - angle);
    if (along <= Math.abs(g.sweep)) return { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
    const a = point(e.start), b = point(e.end), nearest = distance(cursor, a) <= distance(cursor, b) ? a : b;
    return { x: nearest.x, y: nearest.y };
  }
  throw new Error('Projection needs a line or arc.');
}
export function refreshReferenceDimensions(s: Sketch): Sketch {
  const p = new Map(s.points.map(p => [p.id, p]));
  const line = (id: string) => { const e = s.entities.find(e => e.id === id); if (e?.kind !== 'line') throw new Error('Expected line.'); return [p.get(e.p1)!, p.get(e.p2)!]; };
  return { ...s, constraints: s.constraints.map(c => {
    if (!('value' in c) || c.driving) return c;
    let value = c.value;
    if (c.kind === 'distance' || c.kind === 'distanceX' || c.kind === 'distanceY') {
      const a = p.get(c.a)!, b = p.get(c.b)!;
      value = c.kind === 'distance' ? distance(a, b) : c.kind === 'distanceX' ? b.x - a.x : b.y - a.y;
    } else if (c.kind === 'radius') {
      const a = s.entities.find(e => e.id === c.arc); if (a?.kind === 'arc') value = arcGeometry(s, a, p).radius;
    } else if (c.kind === 'angle') {
      const [a, b] = line(c.a), [d, e] = line(c.b), ax = b.x - a.x, ay = b.y - a.y, bx = e.x - d.x, by = e.y - d.y;
      value = Math.atan2(ax * by - ay * bx, ax * bx + ay * by);
    } else if (c.kind === 'pointLineDistance') {
      const [a, b] = line(c.line), q = p.get(c.point)!;
      value = Math.abs((q.x - a.x) * (b.y - a.y) - (q.y - a.y) * (b.x - a.x)) / distance(a, b);
    }
    return Number.isFinite(value) ? { ...c, value } : c;
  }) };
}
