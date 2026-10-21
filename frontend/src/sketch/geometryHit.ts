import type { Reference } from './adapter';
import { entityPoints, type Entity, type Sketch } from './model';
import { distance, projectToEntity, projectToLine } from './geometry';
import { internalHelper } from './dimensionGraphics';
import { referencePointIds } from './context';
import type { XY } from './solver/types';

/** Points owned by context/helpers and centres hidden by the canvas cannot be picked. */
export function excludedGeometryPoints(sketch: Sketch, hidden: string[] = [], references: Reference[] = []) {
  return new Set([...referencePointIds(sketch, references), ...sketch.entities.filter(internalHelper).flatMap(entityPoints), ...hidden]);
}
export function geometryCandidates(sketch: Sketch, at: XY, hidden: string[] = [], references: Reference[] = [],
  include: (entity: Entity) => boolean = e => !e.reference) {
  const excluded = excludedGeometryPoints(sketch, hidden, references);
  const hits = sketch.points.filter(p => !excluded.has(p.id)).map(p => ({ kind: 'point', id: p.id, gap: distance(p, at) }));
  for (const e of sketch.entities) {
    if (internalHelper(e) || !include(e) || (e.kind !== 'line' && e.kind !== 'arc')) continue;
    const reference = references.find(r => r.id === e.id);
    const projected = e.kind === 'line' && (reference?.kind === 'axis' || reference?.kind === 'baffle')
      ? projectToLine(at, sketch.points.find(p => p.id === e.p1)!, sketch.points.find(p => p.id === e.p2)!)
      : projectToEntity(sketch, e.id, at.x, at.y);
    hits.push({ kind: e.kind, id: e.id, gap: distance(at, projected) });
  }
  return hits;
}

/** Shared hit policy: points/markers, curves, then keep-out bands; nearest within each class. */
export function rankedGeometryHits<T extends { kind: string; role?: string; gap: number }>(hits: T[], tolerance: number): T[] {
  const priority = (hit: T) => hit.role === 'keepout' ? 2 : hit.kind === 'point' ? 0 : 1;
  return hits.filter(hit => hit.gap <= tolerance).sort((a, b) => priority(a) - priority(b) || a.gap - b.gap);
}

export function geometryHit<T extends { kind: string; role?: string; gap: number }>(hits: T[], tolerance: number): T | undefined {
  return rankedGeometryHits(hits, tolerance)[0];
}
