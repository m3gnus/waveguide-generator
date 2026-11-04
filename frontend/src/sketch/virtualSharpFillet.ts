import { constraintRefs, entityPoints, type Arc, type Constraint, type NewConstraint, type Point, type Sketch } from './model';
import { curveContains } from './geometry';
import { filletGeometry } from './topology';
import type { SharpData } from './virtualSharp';

type FilletEdits = {
  allocate: (prefix: string) => string;
  addPoint: (x: number, y: number) => Point;
  addConstraint: (c: NewConstraint) => string;
  cleanConstraints: (remove: (c: Constraint) => boolean) => void;
};
export function applyFillet(s: Sketch, point: string, radius: number, edits: FilletEdits) {
  const { allocate, addPoint, addConstraint, cleanConstraints } = edits;
  const g = filletGeometry(s, point, radius), [a, b] = g.curves;
  const internalJoin = (c: Constraint) => c.kind === 'coincident' && g.cornerPoints.has(c.a) && g.cornerPoints.has(c.b);
  const keepSharp = s.constraints.some(c => !internalJoin(c) && constraintRefs(c).some(id => g.cornerPoints.has(id))
    && !(c.kind === 'pointOnArc' && [a.id, b.id].includes(c.arc)));
  const center = addPoint(g.center.x, g.center.y), start = addPoint(g.start.x, g.start.y), end = addPoint(g.end.x, g.end.y);
  const arc: Arc = { kind: 'arc', id: allocate('a'), center: center.id, start: start.id, end: end.id, ccw: g.ccw,
    ...(a.construction === undefined ? {} : { construction: a.construction }), ...(a.data === undefined ? {} : { data: a.data }) };
  const shorten = (e: typeof a, to: string) => e.kind === 'line'
    ? { ...e, p1: g.cornerPoints.has(e.p1) ? to : e.p1, p2: g.cornerPoints.has(e.p2) ? to : e.p2 }
    : { ...e, start: g.cornerPoints.has(e.start) ? to : e.start, end: g.cornerPoints.has(e.end) ? to : e.end };
  s.entities = s.entities.map(e => e.id === a.id ? shorten(a, start.id) : e.id === b.id ? shorten(b, end.id) : e);
  s.entities.splice(s.entities.findIndex(e => e.id === a.id) + 1, 0, arc);
  cleanConstraints(c => !keepSharp && constraintRefs(c).some(id => g.cornerPoints.has(id))
    || c.kind === 'tangent' && [a.id, b.id].includes(c.a) && [a.id, b.id].includes(c.b)
    || c.kind === 'pointOnArc' && [a.id, b.id].includes(c.arc)
      && !curveContains(s, s.entities.find(e => e.id === c.arc)! as Arc, s.points.find(p => p.id === c.point)!));
  if (keepSharp) for (const [support, contact] of [[a, start.id], [b, end.id]] as const) {
    const data: SharpData = { point, points: [...g.cornerPoints], support: support.id, contact };
    if (support.kind === 'line') {
      s.entities.push({ id: allocate('l'), kind: 'line', p1: point, p2: contact, construction: true, data: { virtualSharp: data } });
    } else {
      // Like the preset crown helper: the arc's implicit equal-radius equation
      // puts the sharp on the FULL circle, without finite pointOnArc membership.
      s.entities.push({ id: allocate('a'), kind: 'arc', center: support.center, start: point, end: contact,
        ccw: g.cornerPoints.has(support.start) ? support.ccw : !support.ccw, construction: true, data: { virtualSharp: data } });
    }
  }
  const retained = new Set([...s.entities.flatMap(entityPoints), ...s.constraints.flatMap(constraintRefs)]);
  s.points = s.points.filter(p => !g.cornerPoints.has(p.id) || retained.has(p.id));
  addConstraint({ kind: 'tangent', a: a.id, b: arc.id }); addConstraint({ kind: 'tangent', a: arc.id, b: b.id });
  addConstraint({ kind: 'radius', arc: arc.id, value: radius, driving: true });
}

