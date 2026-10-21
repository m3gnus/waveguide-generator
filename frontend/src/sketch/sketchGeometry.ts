import type { Sketch } from './model';
import { constraintRefs, entityPoints } from './model';
import { referencePointIds } from './context';
import type { Reference } from './adapter';
import { internalHelper } from './dimensionGraphics';

export function sketchGeometry(sketch: Sketch, references: Reference[], selection: string[], hoverId?: string,
  hoveredPoint?: string, hoveredArc?: string, acceptsPoint?: (id: string) => boolean) {
  const fixedPoints = referencePointIds(sketch, references);
  const arcs = sketch.entities.filter(e => e.kind === 'arc');
  const endpoints = new Set(sketch.entities.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.start, e.end] : []));
  const hiddenCentres = arcs.map(e => e.center).filter(id => !endpoints.has(id) && !selection.includes(id) && hoveredPoint !== id
    && !acceptsPoint?.(id)
    && !arcs.some(e => e.center === id && (selection.includes(e.id) || (hoveredArc === e.id || hoverId === e.id))));
  const helperPoints = new Set(sketch.entities.filter(internalHelper).flatMap(entityPoints).filter(id => !sketch.entities.some(e => !internalHelper(e) && entityPoints(e).includes(id))));
  const drawingSnapHidden = [...hiddenCentres.filter(id => !arcs.some(e => e.construction && e.center === id)), ...helperPoints];
  const visiblePoints = sketch.points.filter(p => !fixedPoints.has(p.id) && !hiddenCentres.includes(p.id) && !helperPoints.has(p.id));
  return { arcs, endpoints, hiddenCentres, helperPoints, drawingSnapHidden, visiblePoints };
}

export function selectedConstraintRefs(sketch: Sketch, ids: string[]) {
  return sketch.constraints.filter(c => ids.includes(c.id)).flatMap(constraintRefs);
}
