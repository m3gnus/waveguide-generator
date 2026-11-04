import { describe, expect, it } from 'vitest';
import { importedSketch, applyCommand } from './ops';
import { parseExpression, resolveExpressions, expressionDependencies, expressionDependents } from './expressions';
import { displayValue, internalValue } from './dimensionValue';
import type { Constraint, Sketch } from './model';
import type { Command } from './commands';

export function expressionSketch(): Sketch {
  const s = importedSketch([{ id: 'p1', x: 0, y: 0 }, { id: 'p2', x: 10, y: 0 }, { id: 'p3', x: 0, y: 5 }],
    [{ kind: 'line', id: 'l4', p1: 'p1', p2: 'p2' }, { kind: 'line', id: 'l5', p1: 'p1', p2: 'p3' }]);
  s.constraints = [
    { id: 'c6', kind: 'distance', a: 'p1', b: 'p2', value: 10, driving: true, label: 'Throat radius' },
    { id: 'c7', kind: 'distance', a: 'p1', b: 'p3', value: 5, driving: true, label: 'Rim radius' },
  ]; s.nextId = 8; return s;
}
function value(src: string): number {
  const s = expressionSketch(); s.constraints[1] = { ...s.constraints[1], expression: src };
  const result = resolveExpressions(s); expect(result.errors).toEqual([]);
  return (result.sketch.constraints[1] as Constraint & { value: number }).value;
}
function edit(s: Sketch, c: Command) { const result = applyCommand(s, c); if ('error' in result) throw new Error(result.error); return result.sketch; }
describe('dimension expressions', () => {
  it.each([
    ['2 + 3 * 4', 14], ['2^3^2', 512], ['-2^2 + 5', 1], ['2^-2', .25], ['(2 + 3) * 4', 20], ['1,5 cm', 15],
    ['1 m + 2 in + 3 mm', 1053.8], ['sin(30 deg) + cos(60°)', 1], ['atan(1) + asin(1) + acos(0)', 225],
    ['sqrt(1,44) + abs(-2) + round(2.4)', 5.2], ['min(3, 4, 2) + max(2,3)', 5], ['min(1,5; 2,5)', 1.5], ['pi * 2', Math.PI * 2],
  ])('evaluates %s in display units', (src, expected) => expect(value(src)).toBeCloseTo(expected, 9));
  it.each(['', 'sqrt(-1)', '1 / 0', 'unknown(2)', '(2', '2 garbage', 'min()', 'sin(1,2,3)', '1,234', 'x'.repeat(201)])('rejects %s', src => {
    const s = expressionSketch(); s.constraints[1] = { ...s.constraints[1], expression: src || ' ' };
    expect(resolveExpressions(s).errors.length).toBeGreaterThan(0);
  });
  it('resolves references in dependency order, and lists immediate dependencies/dependents', () => {
    const s = expressionSketch(); s.constraints.reverse(); s.constraints[0] = { ...s.constraints[0], expression: '[Throat radius] * 2' };
    expect(resolveExpressions(s).sketch.constraints[0]).toMatchObject({ value: 20 });
    expect(expressionDependencies(s, 'c7')).toEqual(['c6']); expect(expressionDependents(s, 'c6')).toEqual(['c7']);
    expect(value('c6 + [Throat radius]')).toBe(20);
  });
  it('names unknown, ambiguous, non-driving references and cycles', () => {
    const s = expressionSketch();
    s.constraints[1] = { ...s.constraints[1], expression: '[Missing]' };
    expect(resolveExpressions(s).errors[0].message).toContain('Missing');
    s.constraints[0] = { ...s.constraints[0], label: 'Rim radius' }; s.constraints[1].expression = '[Rim radius]';
    expect(resolveExpressions(s).errors[0].message).toContain('Ambiguous');
    s.constraints[0] = { ...s.constraints[0], driving: false } as Constraint; s.constraints[1].expression = 'c6';
    expect(resolveExpressions(s).errors[0].message).toContain('non-driving');
    s.constraints[0] = { ...s.constraints[0], driving: true, expression: 'c7' } as Constraint;
    expect(resolveExpressions(s).errors[0].message).toContain('cycle');
  });
  it('converts angle and diameter values in one boundary', () => {
    const c = { kind: 'angle', value: Math.PI / 2 } as Constraint & { value: number };
    expect(displayValue(c)).toBeCloseTo(90); expect(internalValue(c, 90)).toBeCloseTo(Math.PI / 2);
    const r = { kind: 'radius', value: 5, diameter: true } as Constraint & { value: number };
    expect(displayValue(r)).toBe(10); expect(internalValue(r, 10)).toBe(5);
    const s = expressionSketch(); s.constraints[0] = { ...s.constraints[0], kind: 'angle', a: 'l4', b: 'l5', value: Math.PI / 2, driving: true, expression: 'sin(30) * 180' };
    expect(resolveExpressions(s).sketch.constraints[0]).toMatchObject({ value: expect.closeTo(Math.PI / 2) });
  });
  it('commands recompute, rename token references, clear literal edits and reject cycles atomically', () => {
    let s = expressionSketch(); s.constraints[0].label = 'Throat';
    s = edit(s, { kind: 'setDimensionExpression', id: 'c7', expression: 'Throat + [Throat] + c6' });
    expect(s.constraints[1]).toMatchObject({ value: 30 });
    s = edit(s, { kind: 'setConstraintLabel', id: 'c6', label: 'New throat' });
    expect(s.constraints[1].expression).toBe('[New throat] + [New throat] + c6');
    s = edit(s, { kind: 'setDimension', id: 'c6', value: 20 }); expect(s.constraints[1]).toMatchObject({ value: 60 });
    const result = applyCommand(s, { kind: 'setDimensionExpression', id: 'c6', expression: 'c7' }); expect(result).toHaveProperty('error', expect.stringContaining('cycle'));
    s = edit(s, { kind: 'setDimensionExpression', id: 'c7', expression: '' }); expect(s.constraints[1]).not.toHaveProperty('expression'); expect(s.constraints[1]).toMatchObject({ value: 60 });
    s = edit(s, { kind: 'setDimensionExpression', id: 'c7', expression: '1,5' }); expect(s.constraints[1]).toMatchObject({ value: 1.5 }); expect(s.constraints[1]).not.toHaveProperty('expression');
    s = edit(s, { kind: 'setDimensionExpression', id: 'c7', expression: 'c6' });
    s = edit(s, { kind: 'setDimension', id: 'c7', value: 12 }); expect(s.constraints[1]).not.toHaveProperty('expression');
  });
  it.each(['removeConstraint', 'deleteEntity', 'deletePoint'] as const)('freezes dependent values and reports expression loss through %s', kind => {
    const s = edit(expressionSketch(), { kind: 'setDimensionExpression', id: 'c7', expression: 'c6*2' });
    if (kind === 'deletePoint') { s.points.push({ id: 'p8', x: 20, y: 0 }); s.entities.push({ id: 'l9', kind: 'line', p1: 'p2', p2: 'p8' }); s.nextId = 10; }
    const result = applyCommand(s, { kind, id: kind === 'removeConstraint' ? 'c6' : kind === 'deleteEntity' ? 'l4' : 'p2' });
    if ('error' in result) throw new Error(result.error);
    expect(result.sketch.constraints.find(c => c.id === 'c7')).toMatchObject({ value: 20 });
    expect(result.sketch.constraints.find(c => c.id === 'c7')).not.toHaveProperty('expression');
    expect(result.notice).toContain('Delete kept Rim radius at its last value because Throat radius was removed; its expression was cleared');
  });
  it('guards driving dependencies and drops an expression on switching its own driving state', () => {
    const s = edit(expressionSketch(), { kind: 'setDimensionExpression', id: 'c7', expression: 'c6*2' });
    expect(applyCommand(s, { kind: 'setDriving', id: 'c6', driving: false })).toHaveProperty('error', expect.stringContaining('Rim radius'));
    const next = edit(s, { kind: 'setDriving', id: 'c7', driving: false }); expect(next.constraints[1]).not.toHaveProperty('expression');
    expect(applyCommand(next, { kind: 'setDimensionExpression', id: 'c7', expression: '1' })).toHaveProperty('error', expect.stringContaining('driving'));
  });
  it('parses without evaluating code or permitting accidental grouping', () => {
    expect(parseExpression('window.location')).toMatchObject({ ok: true });
    expect(parseExpression('alert("x")')).toMatchObject({ ok: false }); expect(parseExpression('2 000')).toMatchObject({ ok: false });
  });
});

it('keeps numeric IDs unambiguous even when another dimension uses that text as its label', () => {
  const s = expressionSketch(); s.constraints[1] = { ...s.constraints[1], label: 'c6', expression: 'c6*2' };
  expect(resolveExpressions(s).errors).toEqual([]); expect(resolveExpressions(s).sketch.constraints[1]).toMatchObject({ value: 20 });
});
