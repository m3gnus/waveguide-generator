import type { NewConstraint, Sketch } from '../model';
import { arcGeometry, distance } from '../geometry';
export function dimensionFor(sketch: Sketch, selection: string[], axis?: 'x' | 'y'): NewConstraint | undefined {
  const points = selection.map(id => sketch.points.find(p => p.id === id));
  if (selection.length === 2 && points.every(p => p)) return { kind: axis === 'x' ? 'distanceX' : axis === 'y' ? 'distanceY' : 'distance', a: points[0]!.id, b: points[1]!.id, value: axis === 'x' ? points[1]!.x - points[0]!.x : axis === 'y' ? points[1]!.y - points[0]!.y : distance(points[0]!, points[1]!), driving: true };
  const entities = selection.map(id => sketch.entities.find(e => e.id === id));
  const [a, b] = entities;
  if (selection.length === 1 && a?.kind === 'arc') return { kind: 'radius', arc: a.id, value: arcGeometry(sketch, a).radius, driving: true };
  if (selection.length === 1 && a?.kind === 'line') return dimensionFor(sketch, [a.p1, a.p2], axis);
  if (selection.length === 2 && a?.kind === 'line' && b?.kind === 'line') {
    const point = (id: string) => sketch.points.find(p => p.id === id)!;
    const p = point(a.p1), q = point(a.p2), r = point(b.p1), s = point(b.p2);
    const ax = q.x - p.x, ay = q.y - p.y, bx = s.x - r.x, by = s.y - r.y;
    return { kind: 'angle', a: a.id, b: b.id, value: Math.atan2(ax * by - ay * bx, ax * bx + ay * by), driving: true };
  }
}
