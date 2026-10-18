import type { Reference } from './adapter';
import type { Sketch } from './model';
import { distance } from './geometry';
import { gridStep } from './view';
import type { XY } from './solver/types';
export type SnapConstraint = { kind: 'coincident' | 'horizontal' | 'vertical'; point: string } | { kind: 'pointOnLine'; line: string };
export interface SnapResult extends XY {
  glyph: 'endpoint' | 'center' | 'axis' | 'baffle' | 'horizontal' | 'vertical' | 'grid';
  constraint?: SnapConstraint;
}
export function findSnap(sketch: Sketch, references: Reference[], cursorWorld: XY, pxToWorld: number,
  previous?: SnapResult | null, options: { suppress?: boolean; lastPoint?: string } = {}): SnapResult | null {
  if (options.suppress) return null;
  if (!(pxToWorld > 0) || !Number.isFinite(pxToWorld)) throw new Error('Pixel size must be positive and finite.');
  const cursor = cursorWorld;
  if (previous) {
    const held = { ...previous }, c = held.constraint;
    const point = c && 'point' in c ? sketch.points.find(p => p.id === c.point) : undefined;
    const exists = c ? 'point' in c ? !!point : references.some(r => r.id === c.line) : true;
    if (point) {
      if (held.glyph !== 'horizontal') held.x = point.x;
      if (held.glyph !== 'vertical') held.y = point.y;
    }
    if (held.glyph === 'axis' || held.glyph === 'vertical') held.y = cursor.y;
    if (held.glyph === 'baffle' || held.glyph === 'horizontal') held.x = cursor.x;
    if (exists && distance(cursor, held) <= 12 * pxToWorld) return held;
  }
  const geometry = sketch.entities.filter(e => !e.construction);
  const ends = new Set(geometry.flatMap(e => e.kind === 'line' ? [e.p1, e.p2] : e.kind === 'arc' ? [e.start, e.end] : []));
  const centers = new Set(geometry.flatMap(e => e.kind === 'arc' ? [e.center] : []));
  const candidates: SnapResult[][] = [
    sketch.points.filter(p => ends.has(p.id)).map(p => ({ x: p.x, y: p.y, glyph: 'endpoint', constraint: { kind: 'coincident', point: p.id } })),
    sketch.points.filter(p => centers.has(p.id)).map(p => ({ x: p.x, y: p.y, glyph: 'center', constraint: { kind: 'coincident', point: p.id } })),
    references.filter(r => r.kind === 'axis').map(r => ({ x: 0, y: cursor.y, glyph: 'axis', constraint: { kind: 'pointOnLine', line: r.id } })),
    references.filter(r => r.kind === 'baffle').map(r => ({ x: cursor.x, y: 0, glyph: 'baffle', constraint: { kind: 'pointOnLine', line: r.id } })),
  ];
  const starts = new Set(geometry.flatMap(e => e.kind === 'line' ? [e.p1] : e.kind === 'arc' ? [e.start] : []));
  const lastId = options.lastPoint ?? geometry.flatMap(e => e.kind === 'line' ? [e.p2] : e.kind === 'arc' ? [e.end] : []).filter(id => !starts.has(id)).at(-1);
  const last = sketch.points.find(p => p.id === lastId);
  if (last) candidates.push([
    { x: cursor.x, y: last.y, glyph: 'horizontal', constraint: { kind: 'horizontal', point: last.id } },
    { x: last.x, y: cursor.y, glyph: 'vertical', constraint: { kind: 'vertical', point: last.id } },
  ]);
  const step = gridStep(1 / pxToWorld);
  candidates.push([{ x: Math.round(cursor.x / step) * step, y: Math.round(cursor.y / step) * step, glyph: 'grid' }]);
  for (const group of candidates) {
    const nearest = group.filter(p => distance(cursor, p) <= 8 * pxToWorld).sort((a, b) => distance(cursor, a) - distance(cursor, b))[0];
    if (nearest) return nearest;
  }
  return null;
}
