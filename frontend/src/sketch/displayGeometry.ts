import { entityPoints, type Sketch } from './model';
import { arcGeometry, onArc } from './geometry';
import { worldToScreen, type View, type Viewport } from './view';
import type { XY } from './solver/types';

export function contourScreenPoints(sketch: Sketch, view: View) {
  const xy = (id: string) => worldToScreen(view, sketch.points.find(p => p.id === id)!);
  const points = sketch.entities.filter(e => !e.construction).flatMap(e => e.kind === 'arc' ? [e.start, e.end] : entityPoints(e)).map(xy);
  for (const e of sketch.entities) if (e.kind === 'arc' && !e.construction) {
    const g = arcGeometry(sketch, e);
    for (const angle of [0, Math.PI / 2, Math.PI, 3 * Math.PI / 2]) {
      const p = { x: g.center.x + g.radius * Math.cos(angle), y: g.center.y + g.radius * Math.sin(angle) };
      if (onArc(sketch, e, p)) points.push(worldToScreen(view, p));
    }
  }
  return points;
}

export function contourDimensionBounds(points: XY[], size: Viewport) {
  return { height: size.height, left: Math.min(size.width, ...points.map(p => p.x)), right: Math.max(0, ...points.map(p => p.x)), bottom: Math.max(0, ...points.map(p => p.y)), top: Math.min(size.height, ...points.map(p => p.y)) };
}
