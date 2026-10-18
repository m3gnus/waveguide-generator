import type { Arc, Point, Sketch } from './model';
export const tau = 2 * Math.PI;
export const positiveAngle = (a: number) => ((a % tau) + tau) % tau;
export const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
type XY = { x: number; y: number };
export type Locus = { kind: 'line'; origin: XY; direction: XY }
  | { kind: 'circle'; center: XY; radius: number } | { kind: 'point'; point: XY };
function nearestOnLocus(p: XY, locus: Locus): XY {
  if (locus.kind === 'point') return { ...locus.point };
  if (locus.kind === 'circle') {
    const d = distance(p, locus.center), scale = locus.radius / (d || 1);
    return { x: locus.center.x + (d ? p.x - locus.center.x : 1) * scale, y: locus.center.y + (p.y - locus.center.y) * scale };
  }
  const { origin: a, direction: d } = locus, length2 = d.x * d.x + d.y * d.y;
  if (length2 === 0) return p;
  const t = ((p.x - a.x) * d.x + (p.y - a.y) * d.y) / length2;
  return { x: a.x + t * d.x, y: a.y + t * d.y };
}
export function intersectLoci(a: Locus, b: Locus, cursor: XY): XY[] {
  if (a.kind === 'point') return distance(a.point, nearestOnLocus(a.point, b)) <= 1e-9 ? [{ ...a.point }] : [];
  if (b.kind === 'point') return intersectLoci(b, a, cursor);
  if (a.kind === 'line') {
    const length = Math.hypot(a.direction.x, a.direction.y); if (!length) return [];
    const d = { x: a.direction.x / length, y: a.direction.y / length };
    if (b.kind === 'line') {
      const bl = Math.hypot(b.direction.x, b.direction.y); if (!bl) return [];
      const e = { x: b.direction.x / bl, y: b.direction.y / bl }, cross = d.x * e.y - d.y * e.x;
      if (Math.abs(cross) <= 1e-12) return distance(b.origin, nearestOnLocus(b.origin, a)) <= 1e-9 ? [nearestOnLocus(cursor, a)] : [];
      const t = ((b.origin.x - a.origin.x) * e.y - (b.origin.y - a.origin.y) * e.x) / cross;
      return [{ x: a.origin.x + t * d.x, y: a.origin.y + t * d.y }];
    }
    const foot = nearestOnLocus(b.center, a), h2 = b.radius * b.radius - distance(foot, b.center) ** 2;
    if (h2 < -1e-12 * Math.max(1, b.radius ** 2)) return [];
    const h = Math.sqrt(Math.max(0, h2));
    return [1, -1].map(sign => ({ x: foot.x + sign * h * d.x, y: foot.y + sign * h * d.y }));
  }
  if (b.kind === 'line') return intersectLoci(b, a, cursor);
  const d = distance(a.center, b.center);
  if (d <= 1e-12) return Math.abs(a.radius - b.radius) <= 1e-12 ? [nearestOnLocus(cursor, a)] : [];
  if (d > a.radius + b.radius + 1e-12 || d < Math.abs(a.radius - b.radius) - 1e-12) return [];
  const along = (a.radius ** 2 - b.radius ** 2 + d * d) / (2 * d), h = Math.sqrt(Math.max(0, a.radius ** 2 - along ** 2));
  const dx = (b.center.x - a.center.x) / d, dy = (b.center.y - a.center.y) / d;
  return [1, -1].map(sign => ({ x: a.center.x + along * dx - sign * h * dy, y: a.center.y + along * dy + sign * h * dx }));
}
export function projectToLoci(cursor: XY, loci: Locus[]): XY {
  if (loci.length === 1) return nearestOnLocus(cursor, loci[0]);
  if (loci.length !== 2) return cursor;
  const intersections = intersectLoci(loci[0], loci[1], cursor);
  return intersections.sort((a, b) => distance(cursor, a) - distance(cursor, b))[0] ?? cursor;
}
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
