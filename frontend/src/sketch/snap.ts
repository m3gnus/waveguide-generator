import type { Reference } from './adapter';
import type { Sketch } from './model';
import { internalHelper } from './dimensionGraphics';
import { contextData } from './context';
import { distance } from './geometry';
import { gridStep } from './view';
import type { XY } from './solver/types';
export type SnapConstraint = { kind: 'coincident' | 'horizontal' | 'vertical'; point: string } | { kind: 'pointOnLine'; line: string };
export interface SnapResult extends XY {
  glyph: 'endpoint' | 'center' | 'axis' | 'baffle' | 'limit' | 'horizontal' | 'vertical' | 'grid';
  constraint?: SnapConstraint;
}
export function findSnap(sketch: Sketch, references: Reference[], cursorWorld: XY, pxToWorld: number,
  previous?: SnapResult | null, options: { suppress?: boolean; lastPoint?: string; exclude?: string[] } = {}): SnapResult | null {
  if (options.suppress) return null;
  if (!(pxToWorld > 0) || !Number.isFinite(pxToWorld)) throw new Error('Pixel size must be positive and finite.');
  const cursor = cursorWorld, exclude = new Set(options.exclude ?? []);
  let heldSnap: SnapResult | undefined;
  if (previous) {
    const held = { ...previous }, c = held.constraint;
    const limit = held.glyph === 'limit' ? references.find(r => r.kind === 'limit') : undefined;
    const point = c && 'point' in c ? sketch.points.find(p => p.id === c.point) : undefined;
    const exists = held.glyph === 'limit' ? !!limit : c ? 'point' in c ? !!point && !exclude.has(c.point) : references.some(r => r.id === c.line) : true;
    if (limit?.kind === 'limit') held.x = limit.snapRadius ?? limit.radius;
    if (point) {
      if (held.glyph !== 'horizontal') held.x = point.x;
      if (held.glyph !== 'vertical') held.y = point.y;
    }
    if (held.glyph === 'axis' || held.glyph === 'vertical' || held.glyph === 'limit') held.y = cursor.y;
    if (held.glyph === 'baffle' || held.glyph === 'horizontal') held.x = cursor.x;
    if (exists && distance(cursor, held) <= 12 * pxToWorld) heldSnap = held;
  }
  const geometry = sketch.entities.filter(e => !e.reference && !internalHelper(e) && !contextData(e) && !references.some(r => r.id === e.id));
  const ends = new Set(geometry.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.start, e.end] : []));
  const centers = new Set(geometry.flatMap(e => e.kind === 'arc' ? [e.center] : []));
  const candidates: SnapResult[][] = [
    sketch.points.filter(p => ends.has(p.id) && !exclude.has(p.id)).map(p => ({ x: p.x, y: p.y, glyph: 'endpoint', constraint: { kind: 'coincident', point: p.id } })),
    sketch.points.filter(p => centers.has(p.id) && !exclude.has(p.id)).map(p => ({ x: p.x, y: p.y, glyph: 'center', constraint: { kind: 'coincident', point: p.id } })),
    references.filter(r => r.kind === 'axis').map(r => ({ x: 0, y: cursor.y, glyph: 'axis', constraint: { kind: 'pointOnLine', line: r.id } })),
    references.flatMap(r => r.kind === 'limit' ? [{ x: r.snapRadius ?? r.radius, y: cursor.y, glyph: 'limit' as const }] : []),
    references.filter(r => r.kind === 'baffle').map(r => ({ x: cursor.x, y: 0, glyph: 'baffle', constraint: { kind: 'pointOnLine', line: r.id } })),
  ];
  const starts = new Set(geometry.flatMap(e => e.kind === 'line' ? [e.p1] : e.kind === 'arc' ? [e.start] : []));
  const lastId = options.lastPoint ?? geometry.flatMap(e => e.kind === 'line' ? [e.p2] : e.kind === 'arc' ? [e.end] : []).filter(id => !starts.has(id)).at(-1);
  const last = sketch.points.find(p => p.id === lastId);
  if (last && !exclude.has(last.id)) candidates.push([
    { x: cursor.x, y: last.y, glyph: 'horizontal', constraint: { kind: 'horizontal', point: last.id } },
    { x: last.x, y: cursor.y, glyph: 'vertical', constraint: { kind: 'vertical', point: last.id } },
  ]);
  const step = gridStep(1 / pxToWorld);
  candidates.push([{ x: Math.round(cursor.x / step) * step, y: Math.round(cursor.y / step) * step, glyph: 'grid' }]);
  const priority = (p: SnapResult) => ['endpoint', 'center', 'axis', 'limit', 'baffle', 'horizontal', 'grid'].indexOf(p.glyph === 'vertical' ? 'horizontal' : p.glyph);
  for (const group of candidates) {
    const nearest = group.filter(p => distance(cursor, p) <= 8 * pxToWorld).sort((a, b) => distance(cursor, a) - distance(cursor, b))[0];
    if (nearest) {
      if (heldSnap && priority(heldSnap) <= priority(nearest)
        && (heldSnap.glyph !== 'grid' || distance(cursor, heldSnap) <= distance(cursor, nearest))) return heldSnap;
      return nearest;
    }
  }
  return heldSnap ?? null;
}

/** Soft limits snap only near the rim; a further drag remains free to cross. */
export function snapLimitTarget(references: Reference[], point: string, target: XY, tolerance: number): XY {
  const limit = references.find(r => r.kind === 'limit' && r.point === point);
  if (limit?.kind !== 'limit') return target;
  const radius = limit.snapRadius ?? limit.radius;
  return Math.abs(target.x - radius) <= tolerance ? { ...target, x: radius } : target;
}
