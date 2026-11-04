import { parseDecimalInput } from '../design/decimalInput';
import type { Constraint, Sketch } from './model';
import type { Command } from './commands';
import { applyCommand } from './ops';
import { formatReference } from './expressions';
import { displayValue, internalValue } from './dimensionValue';

/** Accept pasted, unbracketed labels too; saved expressions use the parser's bracket syntax. */
function bracketNames(sketch: Sketch, text: string): string {
  const names = [...new Set(sketch.constraints.filter(c => 'value' in c && c.driving)
    .map(c => c.label?.trim()).filter((name): name is string => !!name && /\s/.test(name)))].sort((a, b) => b.length - a.length);
  if (!names.length) return text;
  const escaped = names.map(name => name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|');
  const pattern = new RegExp(`(^|[+*/^(,;\\-]\\s*)(${escaped})(?=\\s*(?:$|[+*/^),;\\-]))`, 'g');
  return text.split(/(\[[^\]]*\])/).map(part => part.startsWith('[') ? part : part.replace(pattern, (_match, prefix: string, name: string) => {
    const matches = sketch.constraints.filter(c => 'value' in c && c.driving && c.label?.trim() === name);
    return prefix + (matches.length === 1 ? formatReference(sketch, matches[0].id) : name);
  })).join('');
}

/** Validation and live readout share the exact command used on submission. No preview is persisted. */
export function dimensionEntry(sketch: Sketch, dimension: Constraint & { value: number }, text: string):
  { command: Command; value: number } | { error: string } {
  const parsed = !text.trim() && dimension.expression ? { kind: 'expression' as const, raw: '' } : parseDecimalInput(text, { allowExpression: true });
  if (parsed.kind === 'error') return { error: parsed.message };
  const command: Command = parsed.kind === 'number'
    ? { kind: 'setDimension', id: dimension.id, value: internalValue(dimension, parsed.value) }
    : { kind: 'setDimensionExpression', id: dimension.id, expression: bracketNames(sketch, text.trim()) };
  const result = applyCommand(sketch, command);
  if ('error' in result) return { error: result.error };
  const next = result.sketch.constraints.find(c => c.id === dimension.id)!;
  return 'value' in next ? { command, value: displayValue(next) } : { error: 'Select a dimension to edit its value.' };
}
