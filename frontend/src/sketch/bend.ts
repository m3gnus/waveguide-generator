import type { Sketch } from './model';
import type { DragTarget, XY } from './solver/types';
import { arcGeometry, arcThrough, curveEnds, distance } from './geometry';

export const arcBendClampMessage = "An arc can't bend past its chord · drag back";

export function arcBendTargets(s: Sketch, arcId: string, cursor: XY, mode: 'bend' | 'radius'): DragTarget[] {
  return arcBendPlan(s, arcId, cursor, mode).targets;
}

export function arcBendPlan(s: Sketch, arcId: string, cursor: XY, mode: 'bend' | 'radius'): { targets: DragTarget[]; clamped: boolean } {
  const arc = s.entities.find(e => e.id === arcId);
  if (arc?.kind !== 'arc' || arc.reference) throw new Error('Bending needs an authored arc. Select an arc body.');
  if (!Number.isFinite(cursor.x + cursor.y)) throw new Error('Bending needs a finite cursor position. Move the pointer onto the canvas.');
  const [start, end] = curveEnds(s, arc), g = arcGeometry(s, arc);
  const target = (pointId: string, xy: XY): DragTarget => ({ pointId, x: xy.x, y: xy.y });
  if (mode === 'radius') {
    const r = distance(g.center, cursor);
    if (r <= 1e-9 || g.radius <= 1e-9 || g.endRadius <= 1e-9) throw new Error('Arc radius must be positive. Move the pointer away from its centre.');
    const radial = (p: XY) => ({ x: g.center.x + (p.x - g.center.x) * r / distance(g.center, p), y: g.center.y + (p.y - g.center.y) * r / distance(g.center, p) });
    return { targets: [target(arc.center, g.center), target(arc.start, radial(start)), target(arc.end, radial(end))], clamped: false };
  }
  const length = distance(start, end);
  if (length <= 1e-9) throw new Error('Arc endpoints coincide. Separate them before bending.');
  const mid = { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 }, normal = { x: -(end.y - start.y) / length, y: (end.x - start.x) / length };
  // A directed arc lies to the right of its chord when CCW, including major arcs.
  const side = arc.ccw ? -1 : 1, sagitta = side * ((cursor.x - mid.x) * normal.x + (cursor.y - mid.y) * normal.y);
  const minimum = Math.max(1e-3, length * 1e-3);
  const through = sagitta >= minimum ? cursor : { x: mid.x + side * minimum * normal.x, y: mid.y + side * minimum * normal.y };
  const center = arcThrough(start, end, through).center;
  return { targets: [target(arc.center, center), target(arc.start, start), target(arc.end, end)], clamped: sagitta < minimum };
}
