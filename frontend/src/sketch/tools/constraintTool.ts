import type { Reference } from '../adapter';
import type { NewConstraint, Sketch } from '../model';
import type { XY } from '../solver/types';
import { distance, projectToLine, projectToEntity } from '../geometry';
import { geometryHit } from '../geometryHit';
import { dimensionHelper } from '../dimensionGraphics';
import { constraintFor, pickPatterns, rules, type SelectionConstraint } from './constraints';

export interface ConstraintToolState { kinds: SelectionConstraint[]; ids: string[] }
export const constraintTool = (kinds: SelectionConstraint[]): ConstraintToolState => ({ kinds, ids: [] });
export function acceptsConstraintPick(sketch: Sketch, state: ConstraintToolState, id: string): boolean {
  return state.kinds.some(kind => pickPatterns(sketch, [...state.ids, id], kind).length > 0);
}
/** Invalid picks leave state untouched. A completed pattern resets picks, retaining the tool. */
export function pickConstraint(sketch: Sketch, state: ConstraintToolState, id: string): { state: ConstraintToolState; constraint?: NewConstraint } {
  if (!acceptsConstraintPick(sketch, state, id)) return { state };
  const ids = [...state.ids, id];
  const constraint = state.kinds.map(kind => constraintFor(sketch, ids, kind)).find(Boolean);
  return constraint ? { state: constraintTool(state.kinds), constraint } : { state: { ...state, ids } };
}
export function escapeConstraint(state: ConstraintToolState): ConstraintToolState | undefined {
  return state.ids.length ? constraintTool(state.kinds) : undefined;
}
export function constraintHint(sketch: Sketch, state: ConstraintToolState): string {
  const targets = new Set(state.kinds.flatMap(kind => pickPatterns(sketch, state.ids, kind).map(pattern =>
    pattern.length === 1 ? `a ${pattern[0]}` : `the ${state.ids.length ? 'second' : 'first'} ${pattern[state.ids.length]}`)));
  return `${rules[state.kinds[0]].label}: select ${[...targets].join(' or ')}`;
}
/** Filter before shared hit testing, so an invalid foreground target cannot mask a valid one. */
export function hitConstraint(sketch: Sketch, references: Reference[], state: ConstraintToolState, at: XY, tolerance: number, hidden: string[] = []): string | undefined {
  const hits: { kind: string; id: string; gap: number }[] = sketch.points.filter(p => !hidden.includes(p.id) && acceptsConstraintPick(sketch, state, p.id))
    .map(p => ({ kind: 'point', id: p.id, gap: distance(p, at) }));
  for (const e of sketch.entities) {
    if (dimensionHelper(e) || !acceptsConstraintPick(sketch, state, e.id)) continue;
    const reference = references.find(r => r.id === e.id);
    let projected: XY;
    if (e.kind === 'line' && (reference?.kind === 'axis' || reference?.kind === 'baffle')) {
      const a = sketch.points.find(p => p.id === e.p1)!, b = sketch.points.find(p => p.id === e.p2)!;
      projected = projectToLine(at, a, b);
    } else projected = projectToEntity(sketch, e.id, at.x, at.y);
    hits.push({ kind: e.kind, id: e.id, gap: distance(projected, at) });
  }
  return geometryHit(hits, tolerance)?.id;
}
