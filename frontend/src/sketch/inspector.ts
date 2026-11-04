import { constraintRefs, entityPoints, type Constraint, type Sketch } from './model';
import { displayName } from './displayName';
import { displayValue } from './dimensionValue';
import { expressionDependents, resolveExpressions } from './expressions';
import { formatValue } from './formatValue';
import type { SolveResult } from './solver/types';

export interface ConstraintRow {
  id: string; kind: Constraint['kind']; name: string; refs: { id: string; name: string }[];
  value?: string; driving?: boolean; expression?: string; diameter?: boolean;
  error?: string;
  status: 'broken expression' | 'ok' | 'conflicting' | 'redundant' | 'reference' | 'notAnalysed'; dependents: string[];
}
export function inspectConstraints(sketch: Sketch, diagnosis?: Pick<SolveResult, 'conflicting' | 'redundant' | 'partiallyRedundant'>, selection?: readonly string[]): ConstraintRow[] {
  const errors = new Map(resolveExpressions(sketch).errors.map(e => [e.id, e.message]));
  const selected = selection === undefined ? undefined : new Set(selection);
  if (selected) for (const e of sketch.entities) {
    if (selection!.includes(e.id)) entityPoints(e).forEach(id => selected.add(id));
    if (entityPoints(e).some(id => selection!.includes(id))) selected.add(e.id);
  }
  return sketch.constraints.filter(c => !selected || selected.has(c.id) || constraintRefs(c).some(id => selected.has(id)))
    .sort((a, b) => Number('value' in b) - Number('value' in a)).map(c => ({
      id: c.id, kind: c.kind, name: displayName(sketch, c.id), refs: constraintRefs(c).map(id => ({ id, name: displayName(sketch, id) })),
      ...('value' in c ? { value: `${c.diameter ? 'Ø' : ''}${formatValue(displayValue(c))}${c.kind === 'angle' ? '°' : ' mm'}`, driving: c.driving } : {}),
      ...(c.expression === undefined ? {} : { expression: c.expression }), ...(c.diameter ? { diameter: true } : {}),
      ...(errors.has(c.id) ? { error: errors.get(c.id) } : {}),
      status: errors.has(c.id) ? 'broken expression' : diagnosis?.conflicting?.includes(c.id) ? 'conflicting' : diagnosis?.redundant?.includes(c.id) || diagnosis?.partiallyRedundant?.includes(c.id) ? 'redundant'
        : 'driving' in c && !c.driving ? 'reference' : diagnosis ? 'ok' : 'notAnalysed',
      dependents: expressionDependents(sketch, c.id),
    }));
}
