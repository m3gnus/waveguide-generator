import { constraintName } from './displayName';
import { expressionDependencies } from './expressions';
import type { Sketch } from './model';

/** One notice builder, used at the command boundary and by consumer controllers. */
export function commandNotice(before: Sketch, after: Sketch, action: string): string | undefined {
  const removed = before.constraints.filter(c => !after.constraints.some(n => n.id === c.id));
  const notices: string[] = [];
  const names: Record<string, string> = { fillet: 'Fillet', trim: 'Trim', extend: 'Extend', splitAt: 'Split',
    deleteEntity: 'Delete', deletePoint: 'Delete', removeConstraint: 'Delete' };
  const converted = action === 'convertEntity' && after.entities.find(e => before.entities.some(old => old.id === e.id && old.kind !== e.kind));
  const name = converted ? converted.kind === 'line' ? 'Make line' : 'Make arc' : names[action] ?? 'Edit';
  if (removed.length) {
    const dimensions = removed.filter(c => 'value' in c).length, relations = removed.length - dimensions;
    const counts = [dimensions && `${dimensions} dimension${dimensions === 1 ? '' : 's'}`, relations && `${relations} relation${relations === 1 ? '' : 's'}`].filter(Boolean).join(' and ');
    notices.push(`${name} removed ${counts}: ${removed.map(constraintName).join(', ')}.`);
  }
  for (const c of before.constraints) if (removed.length && c.expression && after.constraints.some(n => n.id === c.id && !n.expression)) {
    const deleted = expressionDependencies(before, c.id).filter(id => !after.constraints.some(n => n.id === id));
    if (deleted.length) notices.push(`${name} kept ${constraintName(c)} at its last value because ${deleted.map(id => constraintName(before.constraints.find(c => c.id === id)!)).join(', ')} ${deleted.length === 1 ? 'was' : 'were'} removed; its expression was cleared.`);
  }
  if (action === 'setConstraintLabel') {
    const rewritten = after.constraints.filter(c => c.expression && c.expression !== before.constraints.find(old => old.id === c.id)?.expression);
    if (rewritten.length) notices.push(`Updated expressions in ${rewritten.map(c => constraintName(c)).join(', ')} to use the new dimension name.`);
  }
  return notices.join(' ') || undefined;
}
