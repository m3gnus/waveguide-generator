import type { Reference } from '../adapter';
import type { NewConstraint, Point, Sketch } from '../model';
import type { XY } from '../solver/types';
import { arcGeometry, distance, projectToEntity } from '../geometry';
import { dimensionHelper } from '../dimensionGraphics';
import { geometryHit } from '../geometryHit';

export type DimensionPick = { kind: 'point'; point: Point; id: string }
  | { kind: 'line'; id: string; a: Point; b: Point; reference?: 'axis' | 'baffle' }
  | { kind: 'arc'; id: string; center: XY; radius: number };
export type CandidateDimension = Extract<NewConstraint, { value: number }>;
export function dimensionPicks(sketch: Sketch, ids: string[], references: Reference[] = []): DimensionPick[] {
  const point = (id: string) => sketch.points.find(p => p.id === id)!;
  return ids.flatMap((id): DimensionPick[] => {
    const p = sketch.points.find(p => p.id === id);
    if (p) return [{ kind: 'point', id, point: p }];
    const e = sketch.entities.find(e => e.id === id);
    const reference = references.find(r => r.id === id);
    if (!e || dimensionHelper(e) || reference?.kind === 'limit') return [];
    if (e.kind === 'line') return [{ kind: 'line', id, a: point(e.p1), b: point(e.p2), reference: reference?.kind === 'axis' || reference?.kind === 'baffle' ? reference.kind : undefined }];
    if (e.kind === 'arc') return [{ kind: 'arc', id, ...arcGeometry(sketch, e) }];
    return [];
  });
}
const projection = (p: XY, a: XY, b: XY): XY => {
  const dx = b.x - a.x, dy = b.y - a.y, t = ((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1);
  return { x: a.x + t * dx, y: a.y + t * dy };
};
function placement(kind: string, a: XY, b: XY, cursor: XY) {
  const dx = b.x - a.x, dy = b.y - a.y, x = cursor.x - (a.x + b.x) / 2, y = cursor.y - (a.y + b.y) / 2;
  if (kind === 'distanceX') return { offset: y, along: .5 + x / (dx || 1) };
  if (kind === 'distanceY') return { offset: x, along: .5 + y / (dy || 1) };
  const length = Math.hypot(dx, dy) || 1;
  return { offset: (-dy * x + dx * y) / length, along: .5 + (dx * x + dy * y) / (length * length) };
}
function betweenPoints(a: Point, b: Point, cursor: XY): CandidateDimension {
  // The band swept along the segment normal is aligned, including shallow lines.
  // Projected distances take over only beyond this band, in the axis strips.
  const dx = b.x - a.x, dy = b.y - a.y;
  const along = ((cursor.x - a.x) * dx + (cursor.y - a.y) * dy) / (dx * dx + dy * dy || 1);
  const inX = cursor.x >= Math.min(a.x, b.x) && cursor.x <= Math.max(a.x, b.x);
  const inY = cursor.y >= Math.min(a.y, b.y) && cursor.y <= Math.max(a.y, b.y);
  const normalBand = along >= 0 && along <= 1 && Math.abs(dx) > 1e-9 * Math.hypot(dx, dy) && Math.abs(dy) > 1e-9 * Math.hypot(dx, dy);
  const kind = normalBand ? 'distance' : inX && !inY ? 'distanceX' : inY && !inX ? 'distanceY' : 'distance';
  if (kind === 'distanceX' && b.x < a.x || kind === 'distanceY' && b.y < a.y) [a, b] = [b, a];
  return { kind, a: a.id, b: b.id, value: kind === 'distanceX' ? b.x - a.x : kind === 'distanceY' ? b.y - a.y : distance(a, b), driving: true, placement: placement(kind, a, b, cursor) };
}
/** Pure pick → candidate policy. Coordinates and placement are in world mm. */
export function candidateDimension(picks: DimensionPick[], cursor: XY): CandidateDimension | undefined {
  if (picks.length === 1) {
    const a = picks[0];
    if (a.kind === 'line' && !a.reference) return betweenPoints(a.a, a.b, cursor);
    if (a.kind === 'arc') return { kind: 'radius', arc: a.id, value: a.radius, driving: true,
      placement: { angle: Math.atan2(cursor.y - a.center.y, cursor.x - a.center.x), offset: distance(cursor, a.center) - a.radius } };
    return;
  }
  if (picks.length !== 2 || picks[0].id === picks[1].id) return;
  const [a, b] = picks;
  if (a.kind === 'point' && b.kind === 'point') return betweenPoints(a.point, b.point, cursor);
  const p = a.kind === 'point' ? a : b.kind === 'point' ? b : undefined;
  const line = a.kind === 'line' ? a : b.kind === 'line' ? b : undefined;
  if (p && line) {
    if (line.reference) {
      const kind = line.reference === 'axis' ? 'distanceX' : 'distanceY';
      const delta = kind === 'distanceX' ? p.point.x - line.a.x : p.point.y - line.a.y;
      const [a, b] = delta < 0 ? [p.point, line.a] : [line.a, p.point];
      return { kind, a: a.id, b: b.id, value: Math.abs(delta),
        label: line.reference === 'axis' ? 'r' : 'z', driving: true, placement: placement(kind, a, b, cursor) };
    }
    const foot = projection(p.point, line.a, line.b);
    if (distance(p.point, foot) <= 1e-9) return;
    return { kind: 'pointLineDistance', point: p.id, line: line.id, value: distance(p.point, foot), driving: true,
      placement: placement('distance', p.point, foot, cursor) };
  }
  if (a.kind !== 'line' || b.kind !== 'line' || a.reference || b.reference) return;
  const dx = a.b.x - a.a.x, dy = a.b.y - a.a.y, ex = b.b.x - b.a.x, ey = b.b.y - b.a.y;
  const cross = dx * ey - dy * ex;
  if (Math.abs(cross) <= 1e-9 * Math.hypot(dx, dy) * Math.hypot(ex, ey)) {
    const foot = projection(b.a, a.a, a.b);
    return { kind: 'pointLineDistance', point: b.a.id, line: a.id, value: distance(b.a, foot), driving: true,
      placement: placement('distance', b.a, foot, cursor) };
  }
  if (cross < 0) return candidateDimension([b, a], cursor);
  const value = Math.atan2(cross, dx * ex + dy * ey), start = Math.atan2(dy, dx);
  const t = ((b.a.x - a.a.x) * ey - (b.a.y - a.a.y) * ex) / cross;
  const center = { x: a.a.x + t * dx, y: a.a.y + t * dy };
  const delta = Math.atan2(cursor.y - center.y, cursor.x - center.x) - start;
  return { kind: 'angle', a: a.id, b: b.id, value, driving: true,
    placement: { offset: distance(cursor, center), along: Math.atan2(Math.sin(delta), Math.cos(delta)) / value } };
}
/** Geometry hit testing, including infinite axis/baffle lines. Points always win. */
export function hitDimension(sketch: Sketch, references: Reference[], cursor: XY, tolerance: number, hidden: string[] = []): string | undefined {
  const excluded = new Set([...hidden, ...sketch.entities.filter(e => e.reference || references.some(r => r.id === e.id) || dimensionHelper(e)).flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.center, e.start, e.end] : [])]);
  const points = sketch.points.filter(p => !excluded.has(p.id)).map(p => ({ kind: 'point', id: p.id, gap: distance(p, cursor) }));
  const curves = dimensionPicks(sketch, sketch.entities.map(e => e.id), references).map(p => {
    const at = p.kind === 'line' && p.reference ? projection(cursor, p.a, p.b) : projectToEntity(sketch, p.id, cursor.x, cursor.y);
    return { kind: p.kind, id: p.id, gap: distance(cursor, at) };
  });
  return geometryHit([...points, ...curves], tolerance)?.id;
}
