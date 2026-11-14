import { isVirtualSharpHelper, virtualSharpConstraintIds } from './virtualSharp';
import { constraintName, displayName } from './displayName';
import { expressionDependencies } from './expressions';
import { constraintRefs, type Constraint, type Sketch } from './model';

/** One notice builder, used at the command boundary and by consumer controllers. */
export function commandNotice(before: Sketch, after: Sketch, action: string): string | undefined {
  const internal = virtualSharpConstraintIds(before);
  const removed = before.constraints.filter(c => !internal.has(c.id) && !after.constraints.some(n => n.id === c.id));
  const notices: string[] = [];
  const names: Record<string, string> = { fillet: 'Fillet', trim: 'Trim', extend: 'Extend', splitAt: 'Split',
    deleteEntity: 'Delete', deletePoint: 'Delete', removeConstraint: 'Delete' };
  const converted = action === 'convertEntity' && after.entities.find(e => before.entities.some(old => old.id === e.id && old.kind !== e.kind));
  const name = converted ? converted.kind === 'line' ? 'Make line' : 'Make arc' : names[action] ?? 'Edit';
  const dimensions = removed.filter(c => 'value' in c), relations = removed.filter(c => !('value' in c));
  if (removed.length) {
    const counts = [dimensions.length && `${dimensions.length} dimension${dimensions.length === 1 ? '' : 's'}`, relations.length && `${relations.length} relation${relations.length === 1 ? '' : 's'}`].filter(Boolean).join(' and ');
    // A relation is named with what it held, so "Fix" alone never leaves the user guessing which point.
    const centre = (id: string) => before.entities.find(e => e.kind === 'arc' && e.center === id && !isVirtualSharpHelper(e));
    const held = (id: string) => { const arc = centre(id); return arc ? `${displayName(before, arc.id)} centre` : displayName(before, id); };
    const relation = (c: Constraint) => `${constraintName(c)} (${constraintRefs(c).map(held).join(', ')})`;
    notices.push(`${name} removed ${counts}: ${[...dimensions.map(constraintName), ...relations.map(relation)].join(', ')}.`);
  }
  for (const c of before.constraints) if (removed.length && c.expression && after.constraints.some(n => n.id === c.id && !n.expression)) {
    const deleted = expressionDependencies(before, c.id).filter(id => removed.some(n => 'value' in n && n.id === id));
    if (deleted.length) notices.push(`${name} kept ${constraintName(c)} at its last value because ${deleted.map(id => constraintName(before.constraints.find(c => c.id === id)!)).join(', ')} ${deleted.length === 1 ? 'was' : 'were'} removed; its expression was cleared.`);
  }
  if (action === 'setConstraintLabel') {
    const rewritten = after.constraints.filter(c => c.expression && c.expression !== before.constraints.find(old => old.id === c.id)?.expression);
    if (rewritten.length) notices.push(`Updated expressions in ${rewritten.map(c => constraintName(c)).join(', ')} to use the new dimension name.`);
  }
  if (removed.length) notices.push(`Undo to keep ${removed.length === 1 ? 'it' : 'them'}.`);
  return notices.join(' ') || undefined;
}
