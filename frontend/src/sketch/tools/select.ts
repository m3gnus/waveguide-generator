import { entityPoints, type Sketch } from '../model';
import { arcGeometry, onArc, projectToEntity } from '../geometry';
import { internalHelper } from '../dimensionGraphics';
import { referencePointIds } from '../context';
import type { XY } from '../solver/types';

const selectionExcluded = (sketch: Sketch, hidden: string[] = []) => new Set([...referencePointIds(sketch), ...sketch.entities.filter(internalHelper).flatMap(entityPoints), ...hidden]);

export interface Modifiers { shift?: boolean; toggle?: boolean }
export function combineSelection(selection: string[], ids: string[], modifiers: Modifiers = {}): string[] {
  if (modifiers.toggle) return [...selection.filter(id => !ids.includes(id)), ...ids.filter(id => !selection.includes(id))];
  return modifiers.shift ? [...new Set([...selection, ...ids])] : ids;
}
export interface SelectState {
  start: XY; current: XY; hit?: string; selection: string[]; modifiers: Modifiers;
  phase: 'pending' | 'marquee' | 'drag';
}
export function startSelect(start: XY, hit: string | undefined, selection: string[], modifiers: Modifiers): SelectState {
  return { start, current: start, hit, selection, modifiers, phase: 'pending' };
}
export function moveSelect(state: SelectState, current: XY): SelectState {
  return { ...state, current, phase: state.phase === 'pending' && Math.hypot(current.x - state.start.x, current.y - state.start.y) >= 4 ? state.hit ? 'drag' : 'marquee' : state.phase };
}
/** Preserve a group while dragging a member; a plain click resolves to just that member. */
export function finishSelect(state: SelectState, sketch: Sketch, start: XY, end: XY): string[] {
  if (state.phase === 'marquee') return combineSelection(state.selection, marqueeSelection(sketch, start, end), state.modifiers);
  if (state.phase === 'pending' && !state.modifiers.shift && !state.modifiers.toggle) return state.hit ? [state.hit] : [];
  return state.selection;
}
export interface Cycle { at: XY; time: number; ids: string[]; index: number }
export function cycleHit(previous: Cycle | undefined, at: XY, time: number, ids: string[]): Cycle {
  const repeat = previous && time >= previous.time && time - previous.time <= 600 && Math.hypot(at.x - previous.at.x, at.y - previous.at.y) <= 3 && ids.join() === previous.ids.join();
  return { at, time, ids, index: repeat ? (previous.index + 1) % Math.max(1, ids.length) : 0 };
}
/** Geometry candidates are ordered by kind, then distance, independently of SVG paint order. */
export function selectHits(sketch: Sketch, at: XY, tolerance: number, hidden: string[] = [], dimensions: string[] = []): string[] {
  const excluded = selectionExcluded(sketch, hidden);
  const points = sketch.points.filter(p => !excluded.has(p.id)).map(p => ({ id: p.id, gap: Math.hypot(p.x - at.x, p.y - at.y) }));
  const curves = sketch.entities.filter(e => !e.reference && !internalHelper(e) && (e.kind === 'line' || e.kind === 'arc')).map(e => {
    const p = projectToEntity(sketch, e.id, at.x, at.y); return { id: e.id, gap: Math.hypot(p.x - at.x, p.y - at.y) };
  });
  return [...points.filter(p => p.gap <= tolerance).sort((a,b) => a.gap-b.gap), ...curves.filter(p => p.gap <= tolerance).sort((a,b) => a.gap-b.gap)].map(p => p.id).concat(dimensions);
}
export function dragPointIds(sketch: Sketch, selection: string[]): string[] {
  const fixed = selectionExcluded(sketch);
  return [...new Set(selection.flatMap(id => {
    const entity = sketch.entities.find(e => e.id === id);
    return entity ? entity.reference || internalHelper(entity) ? [] : entityPoints(entity) : sketch.points.some(p => p.id === id) ? [id] : [];
  }))].filter(id => !fixed.has(id));
}
/** Exact finite line/arc intersections; arc extrema make a window require the entire curve. */
export function marqueeSelection(sketch: Sketch, start: XY, end: XY): string[] {
  const left = Math.min(start.x,end.x), right = Math.max(start.x,end.x), bottom = Math.min(start.y,end.y), top = Math.max(start.y,end.y);
  const inside = (p: XY) => p.x >= left && p.x <= right && p.y >= bottom && p.y <= top;
  const crossing = end.x < start.x;
  const fixed = selectionExcluded(sketch), point = (id: string) => sketch.points.find(p => p.id === id)!;
  const ids = sketch.points.filter(p => !fixed.has(p.id) && inside(p)).map(p => p.id);
  for (const e of sketch.entities) {
    if (e.reference || internalHelper(e)) continue;
    let contained = false, touches = false;
    if (e.kind === 'line') {
      const a = point(e.p1), b = point(e.p2);
      contained = inside(a) && inside(b); touches = inside(a) || inside(b);
      for (const x of [left,right]) { const t = (x-a.x)/(b.x-a.x); if (t >= 0 && t <= 1 && inside({x, y:a.y+t*(b.y-a.y)})) touches = true; }
      for (const y of [bottom,top]) { const t = (y-a.y)/(b.y-a.y); if (t >= 0 && t <= 1 && inside({y, x:a.x+t*(b.x-a.x)})) touches = true; }
    } else if (e.kind === 'arc') {
      const g = arcGeometry(sketch,e), extrema = [point(e.start),point(e.end)];
      for (const angle of [0,Math.PI/2,Math.PI,3*Math.PI/2]) { const p = {x:g.center.x+g.radius*Math.cos(angle),y:g.center.y+g.radius*Math.sin(angle)}; if (onArc(sketch,e,p)) extrema.push({id:'',...p}); }
      contained = extrema.every(inside); touches = extrema.some(inside);
      for (const x of [left,right]) {
        const d = g.radius**2-(x-g.center.x)**2;
        if (d >= 0) for (const sign of [-1,1]) { const p = {x,y:g.center.y+sign*Math.sqrt(d)}; if (inside(p) && onArc(sketch,e,p)) touches = true; }
      }
      for (const y of [bottom,top]) {
        const d = g.radius**2-(y-g.center.y)**2;
        if (d >= 0) for (const sign of [-1,1]) { const p = {y,x:g.center.x+sign*Math.sqrt(d)}; if (inside(p) && onArc(sketch,e,p)) touches = true; }
      }
    }
    if (crossing ? touches : contained) ids.push(e.id);
  }
  return ids;
}

export const select = (selection: string[], id: string, extend = false): string[] => combineSelection(selection, [id], { toggle: extend });

/** One pending target per animation frame. A flush on release preserves the final target. */
export function coalesceTargets<T>(schedule: (callback: () => void) => number, cancel: (id: number) => void, consume: (target: T) => void) {
  let frame: number | undefined, latest: T | undefined;
  const flush = () => {
    if (frame !== undefined) cancel(frame);
    frame = undefined;
    if (latest !== undefined) { const target = latest; latest = undefined; consume(target); }
  };
  return {
    push(target: T) { latest = target; frame ??= schedule(flush); },
    flush,
    cancel() { if (frame !== undefined) cancel(frame); frame = undefined; latest = undefined; },
  };
}
