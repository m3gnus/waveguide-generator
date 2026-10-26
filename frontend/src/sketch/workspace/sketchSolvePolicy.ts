import type { Constraint, Sketch } from '../model';
import type { SketchSession } from '../session';
import { displayName } from '../displayName';

/** Carry the rejected candidate too: it is rolled back and has no live glyph to name. */
export function constraintError(sketch: Sketch, result: { reason?: string; conflicting?: string[]; redundant?: string[]; constraints?: Constraint[]; hasRedundancy?: boolean }) {
  const ids = [...result.conflicting ?? [], ...result.redundant ?? []];
  const constraints = result.constraints ?? sketch.constraints.filter(c => ids.includes(c.id));
  const named = { ...sketch, constraints: [...sketch.constraints.filter(c => !constraints.some(v => v.id === c.id)), ...constraints] };
  if (!ids.length && !result.hasRedundancy) return new Error(`Could not solve this change${result.reason ? `: ${result.reason}` : '.'}`);
  const reason = result.reason ?? 'The solver reports conflicting or redundant constraints.';
  const error = new Error(`Conflicts with ${constraints.map(c => displayName(named, c.id)).join('; ') || 'the sketch'}: ${reason}`);
  return Object.assign(error, { conflicting: constraints.map(c => c.id), constraints,
    overconstrained: true });
}

export function isConstraintConflict(reason: unknown): reason is Error & { overconstrained: true; conflicting: string[]; constraints: Constraint[] } {
  return !!reason && typeof reason === 'object' && 'overconstrained' in reason && reason.overconstrained === true;
}

export interface Authoring<T> { sketch: Sketch; meta: T }
/** Shared full-solve policy; opening diagnosis still leaves imported bytes untouched. */
export async function diagnoseSketch<T>(session: SketchSession, state: Authoring<T>) {
  session.sketch = state.sketch;
  const diagnosis = await session.solveFull();
  if (diagnosis.status !== 'ok') throw constraintError(state.sketch, diagnosis);
  return { state: { ...state, sketch: session.sketch }, diagnosis };
}
