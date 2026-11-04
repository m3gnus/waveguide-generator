import { coincidentPoints } from './coincidence';
import type { Arc, Constraint, Line, Point, Sketch } from './model';
export const tau = 2 * Math.PI;
export const positiveAngle = (a: number) => ((a % tau) + tau) % tau;
export const distance = (a: { x: number; y: number }, b: { x: number; y: number }) => Math.hypot(a.x - b.x, a.y - b.y);
type XY = { x: number; y: number };
export function arcThrough(a: XY, b: XY, p: XY) {
  if (![a.x, a.y, b.x, b.y, p.x, p.y].every(Number.isFinite)) throw new Error('Arc points must be finite.');
  const bx = b.x - a.x, by = b.y - a.y, px = p.x - a.x, py = p.y - a.y, d = 2 * (bx * py - by * px);
  if (Math.abs(d) <= 1e-12) throw new Error('Three-point arc is collinear.');
  const bb = bx * bx + by * by, pp = px * px + py * py;
  return { center: { x: a.x + (bb * py - pp * by) / d, y: a.y + (bx * pp - px * bb) / d }, ccw: d < 0 };
}
/** A tangent start is an open, directed chain end, including coincident aliases. */
export function tangentIncoming(s: Sketch, id: string): Line | Arc {
  const equivalent = coincidentPoints(s, id);
  const curves = s.entities.filter((e): e is Line | Arc => !e.construction && !e.reference && (e.kind === 'line' || e.kind === 'arc'));
  const incoming = curves.filter(e => equivalent.has(e.kind === 'line' ? e.p2 : e.end));
  if (!incoming.length) throw new Error(curves.some(e => e.kind === 'arc' && equivalent.has(e.center))
    ? 'Arc centres cannot start a tangent arc.' : `${id === 'pole' ? 'The pole' : 'This point'} has no incoming curve. Choose an open chain end.`);
  if (incoming.length !== 1 || curves.some(e => equivalent.has(e.kind === 'line' ? e.p1 : e.start))) throw new Error('This join is not an open chain end.');
  return incoming[0];
}
export function tangentArc(s: Sketch, from: string, b: XY) {
  const e = tangentIncoming(s, from), point = (id: string) => s.points.find(p => p.id === id)!;
  const a = point(from), p = point(e.kind === 'line' ? e.p1 : e.center);
  const tx = e.kind === 'line' ? a.x - p.x : (e.ccw ? -1 : 1) * (a.y - p.y);
  const ty = e.kind === 'line' ? a.y - p.y : (e.ccw ? 1 : -1) * (a.x - p.x);
  const dx = b.x - a.x, dy = b.y - a.y, denom = 2 * (-ty * dx + tx * dy);
  if (Math.abs(denom) <= 1e-12 || Math.hypot(tx, ty) <= 1e-12) throw new Error('Tangent arc is degenerate.');
  const k = (dx * dx + dy * dy) / denom;
  return { center: { x: a.x - k * ty, y: a.y + k * tx }, ccw: k > 0, incoming: e.id };
}
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

export function projectToLine(p: XY, a: XY, b: XY): XY {
  const dx = b.x - a.x, dy = b.y - a.y, t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1);
  return { x: a.x + t * dx, y: a.y + t * dy };
}
/** Bounded line projection, shared by sketch geometry and sampled polylines. */
export function projectToSegment(p: XY, a: XY, b: XY): XY {
  const dx = b.x - a.x, dy = b.y - a.y;
  const t = Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy) || 0));
  return { x: a.x + t * dx, y: a.y + t * dy };
}
export function projectToEntity(sketch: Sketch, entityId: string, x: number, y: number): { x: number; y: number } {
  const e = sketch.entities.find(e => e.id === entityId), cursor = { x, y };
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  if (!Number.isFinite(x + y)) throw new Error('Projection needs finite coordinates.');
  if (e?.kind === 'line') {
    return projectToSegment(cursor, point(e.p1), point(e.p2));
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
      if (c.supplementary) value = Math.PI - value;
    } else if (c.kind === 'pointLineDistance') {
      const [a, b] = line(c.line), q = p.get(c.point)!;
      value = Math.abs((q.x - a.x) * (b.y - a.y) - (q.y - a.y) * (b.x - a.x)) / distance(a, b);
    }
    return Number.isFinite(value) ? { ...c, value } : c;
  }) };
}

export function onSegment(a: XY, b: XY, p: XY, interior = false): boolean {
  const length = distance(a, b);
  return Math.abs(distance(a, p) + distance(p, b) - length) <= 1e-9 * Math.max(1, length)
    && (!interior || distance(a, p) > 1e-9 && distance(b, p) > 1e-9);
}
export function curveEnds(s: Sketch, e: Line | Arc): [Point, Point] {
  return [e.kind === 'line' ? e.p1 : e.start, e.kind === 'line' ? e.p2 : e.end].map(id => s.points.find(p => p.id === id)!) as [Point, Point];
}
export function curveLocus(s: Sketch, e: Line | Arc): Locus {
  const [a, b] = curveEnds(s, e);
  return e.kind === 'line' ? { kind: 'line', origin: a, direction: { x: b.x - a.x, y: b.y - a.y } }
    : { kind: 'circle', center: s.points.find(p => p.id === e.center)!, radius: distance(s.points.find(p => p.id === e.center)!, a) };
}
export function curveContains(s: Sketch, e: Line | Arc, p: XY): boolean {
  const [a, b] = curveEnds(s, e);
  return e.kind === 'line' ? onSegment(a, b, p) : onArc(s, e, p);
}
/** Parameter increases from the first endpoint to the second, along the arc's sweep. */
export function curveParameter(s: Sketch, e: Line | Arc, p: XY): number {
  const [a, b] = curveEnds(s, e);
  if (e.kind === 'line') return ((p.x - a.x) * (b.x - a.x) + (p.y - a.y) * (b.y - a.y)) / distance(a, b) ** 2;
  const g = arcGeometry(s, e), angle = Math.atan2(p.y - g.center.y, p.x - g.center.x);
  return positiveAngle(e.ccw ? angle - g.start : g.start - angle) / Math.abs(g.sweep);
}
/** Shared finite/supporting intersections for trim and extend. Coincident supports have no isolated cut. */
export function curveIntersections(s: Sketch, a: Line | Arc, b: Line | Arc, supportingA = false, supportingB = false): XY[] {
  const x = curveLocus(s, a), y = curveLocus(s, b);
  if (x.kind === 'line' && y.kind === 'line') {
    const cross = x.direction.x * y.direction.y - x.direction.y * y.direction.x;
    if (Math.abs(cross) <= 1e-12 * Math.hypot(x.direction.x, x.direction.y) * Math.hypot(y.direction.x, y.direction.y)) return [];
  }
  if (x.kind === 'circle' && y.kind === 'circle' && distance(x.center, y.center) <= 1e-12 && Math.abs(x.radius - y.radius) <= 1e-12) return [];
  const out: XY[] = [];
  for (const p of intersectLoci(x, y, curveEnds(s, a)[0])) if ((supportingA || curveContains(s, a, p)) && (supportingB || curveContains(s, b, p))
    && !out.some(q => distance(p, q) < 1e-8)) out.push(p);
  return out;
}


/** Point edits retain equations that still hold; dimension measurement has one owner above. */
export function constraintSurvivesPointMove(s: Sketch, c: Constraint): boolean {
  const p = (id: string) => s.points.find(p => p.id === id)!;
  const ends = (id: string): [Point, Point] => {
    const line = s.entities.find(e => e.id === id);
    if (line?.kind !== 'line') throw new Error('A point relation needs a line. Select an existing line.');
    return [p(line.p1), p(line.p2)];
  };
  const tolerance = 1e-7;
  if ('value' in c) {
    const measured = refreshReferenceDimensions({ ...s, constraints: [{ ...c, driving: false }] }).constraints[0];
    return 'value' in measured && Math.abs(measured.value - c.value) <= tolerance;
  }
  switch (c.kind) {
    case 'fix': return false;
    case 'coincident': return distance(p(c.a), p(c.b)) <= tolerance;
    case 'horizontal': case 'vertical': {
      const [a, b] = 'line' in c ? ends(c.line) : [p(c.a), p(c.b)];
      return Math.abs(c.kind === 'horizontal' ? a.y - b.y : a.x - b.x) <= tolerance;
    }
    case 'midpoint': {
      const [a, b] = ends(c.line); return distance(p(c.point), { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }) <= tolerance;
    }
    case 'pointOnLine': { const [a, b] = ends(c.line); return distance(p(c.point), projectToLine(p(c.point), a, b)) <= tolerance; }
    case 'pointOnArc': { const arc = s.entities.find(e => e.id === c.arc); return arc?.kind === 'arc' && onArc(s, arc, p(c.point)); }
    default: return true;
  }
}
