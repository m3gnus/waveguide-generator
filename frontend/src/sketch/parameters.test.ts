import { describe, expect, it } from 'vitest';
import { applyCommand, importedSketch } from './ops';
import { emptySketch, parseSketch, toJSON, validateSketch, type Sketch } from './model';
import type { Command } from './commands';
import { displayName } from './displayName';
import { expressionDependencies, expressionDependents, formatReference, resolveExpressions } from './expressions';
import { serializeSourceAuthoring, parseSourceAuthoring } from './adapters/sourceAuthoring';
import { lower, seed } from './adapters/sourceContour';
import { INITIAL_SOURCE } from '../design/sourceContourEditing';

function fixture() {
  const s = importedSketch([{ id: 'a', x: 0, y: 0 }, { id: 'b', x: 10, y: 0 }], [{ id: 'line', kind: 'line', p1: 'a', p2: 'b' }]);
  s.constraints = [{ id: 'width', kind: 'distance', a: 'a', b: 'b', value: 10, driving: true, label: 'Width' }];
  return s;
}
function edit(s: Sketch, command: Command): Sketch {
  const result = applyCommand(s, command); if ('error' in result) throw new Error(result.error); return result.sketch;
}
const add = (s: Sketch, name = 'pitch', expression = '2', unit: 'mm' | 'deg' | 'none' = 'mm') => edit(s, { kind: 'addParameter', name, expression, unit });

describe('named parameters', () => {
  it('preserves old sketch bytes and omits the field after removing the last parameter', () => {
    const s = emptySketch(), bytes = toJSON(s);
    expect(parseSketch(bytes)).toEqual({ ok: true, sketch: s });
    expect(toJSON(edit(add(s), { kind: 'removeParameter', name: 'pitch' }))).toBe(bytes);
    expect(validateSketch(s)).toEqual({ ok: true });
    expect(toJSON({ ...s, parameters: [] })).toBe(bytes);
  });
  it('round trips parameters in the authoring envelope without changing lowered document bytes', () => {
    const state = seed(INITIAL_SOURCE), before = JSON.stringify(lower(state.sketch, state.meta));
    state.sketch = add(state.sketch, 'spacing', '2 cm');
    const envelope = JSON.parse(JSON.stringify(serializeSourceAuthoring(state)));
    const restored = parseSourceAuthoring(envelope, INITIAL_SOURCE)!;
    expect(restored.sketch.parameters).toEqual([{ name: 'spacing', expression: '2 cm', value: 20, unit: 'mm' }]);
    expect(JSON.stringify(lower(restored.sketch, restored.meta))).toBe(before);
  });
  it.each(['bad name', '2bad', '', 'Width', 'width'])('rejects invalid or colliding name %j', name => {
    expect(applyCommand(fixture(), { kind: 'addParameter', name, expression: '0', unit: 'none' })).toHaveProperty('error');
  });
  it('validates imported fields and duplicate names without changing old validation', () => {
    const s = add(fixture());
    expect(parseSketch(toJSON(s))).toEqual({ ok: true, sketch: s });
    for (const parameters of [null, {}, [{ ...s.parameters![0], unit: 'cm' }], [{ ...s.parameters![0], value: Infinity }], [...s.parameters!, ...s.parameters!]])
      expect(validateSketch({ ...s, parameters }).ok).toBe(false);
    expect(applyCommand(s, { kind: 'addParameter', name: 'pitch', expression: '1', unit: 'mm' })).toHaveProperty('error');
    expect(add(s, 'Pitch').parameters).toHaveLength(2);
    expect(applyCommand(s, { kind: 'setConstraintLabel', id: 'width', label: 'pitch' })).toHaveProperty('error');
  });
  it('resolves parameters and dimensions together in dependency order', () => {
    let s = add(fixture(), 'base', 'width / 2');
    s = add(s, 'spacing', 'base + 1 cm');
    s = edit(s, { kind: 'addConstraint', constraint: { kind: 'distanceX', a: 'a', b: 'b', value: 15, driving: true, label: 'Result', expression: 'spacing * 2' } });
    expect(s.parameters?.map(p => p.value)).toEqual([5, 15]);
    expect(s.constraints.at(-1)).toMatchObject({ value: 30 });
    expect(expressionDependencies(s, 'spacing')).toEqual(['base']);
    expect(expressionDependents(s, 'base')).toEqual(['spacing']);
    expect(expressionDependents(s, 'spacing')).toEqual([s.constraints.at(-1)!.id]);
    expect(expressionDependents(s, 'width')).toEqual(['base']);
    expect(applyCommand(s, { kind: 'setDriving', id: 'width', driving: false })).toHaveProperty('error', expect.stringContaining('base'));
  });
  it('reports mixed cycles by name and refuses edits atomically', () => {
    const s = add(fixture(), 'pitch', 'Width / 2'), before = toJSON(s);
    expect(applyCommand(s, { kind: 'setDimensionExpression', id: 'width', expression: 'pitch' })).toHaveProperty('error', expect.stringMatching(/cycle.*Width.*pitch|cycle.*pitch.*Width/));
    expect(toJSON(s)).toBe(before);
    const t = add(s, 'other', 'pitch');
    expect(applyCommand(t, { kind: 'setParameter', name: 'pitch', expression: 'other' })).toHaveProperty('error', expect.stringContaining('pitch → other → pitch'));
  });
  it.each([['mm', 'angle'], ['deg', 'distance']] as const)('refuses %s parameters in %s dimensions with a fix', (unit, kind) => {
    const s = add(fixture(), 'pitch', '30', unit);
    s.constraints[0] = { id: 'width', kind, a: kind === 'angle' ? 'line' : 'a', b: kind === 'angle' ? 'line' : 'b', driving: true, value: 1, expression: 'pitch' };
    expect(resolveExpressions(s).errors[0].message).toMatch(/pitch.*Change the parameter unit/);
  });
  it('allows scalar parameters, angle parameters in trigonometric functions and degree conversion', () => {
    let s = add(fixture(), 'theta', '30', 'deg');
    s = add(s, 'factor', '2', 'none');
    s = edit(s, { kind: 'setDimensionExpression', id: 'width', expression: 'factor * sin(theta)' });
    expect(s.constraints[0]).toMatchObject({ value: expect.closeTo(1) });
    s.constraints[0] = { id: 'width', kind: 'angle', a: 'line', b: 'line', driving: true, value: 0, expression: 'theta * factor' };
    expect(resolveExpressions(s).sketch.constraints[0]).toMatchObject({ value: expect.closeTo(Math.PI / 3) });
  });
  it('rewrites every parameter token on rename, leaves substrings alone, and refuses used deletion', () => {
    let s = add(fixture()); s = add(s, 'pitch2', 'pitch + [pitch]');
    s = edit(s, { kind: 'setDimensionExpression', id: 'width', expression: 'pitch2 + pitch' });
    s = edit(s, { kind: 'renameParameter', from: 'pitch', to: 'spacing' });
    expect(s.parameters?.[1].expression).toBe('spacing + spacing');
    expect(s.constraints[0].expression).toBe('pitch2 + spacing');
    expect(formatReference(s, 'spacing')).toBe('spacing');
    expect(applyCommand(s, { kind: 'removeParameter', name: 'spacing' })).toHaveProperty('error', expect.stringContaining('Width, pitch2'));
    expect(applyCommand(s, { kind: 'renameParameter', from: 'spacing', to: 'Width' })).toHaveProperty('error');
  });
  it('rewrites parameter references when a dimension is renamed or switches diameter, and freezes removed inputs', () => {
    let s = fixture(); s.constraints[0].kind = 'distanceX';
    s.points[0].x = 0;
    s.entities.push({ id: 'axis', kind: 'line', p1: 'a', p2: 'b', reference: true, construction: true });
    s = add(s, 'pitch', 'Width * 2');
    s = edit(s, { kind: 'setConstraintLabel', id: 'width', label: 'New width' });
    expect(s.parameters?.[0].expression).toBe('[New width] * 2');
    s = edit(s, { kind: 'removeConstraint', id: 'width' });
    expect(s.parameters?.[0]).toMatchObject({ expression: '(10) * 2', value: 20 });
  });
});

it('keeps mathematical pi compatible and uses the reference encoder for a parameter named pi', () => {
  let s = add(fixture(), 'pitch', '2');
  s = edit(s, { kind: 'setDimensionExpression', id: 'width', expression: 'pitch + pi' });
  s = edit(s, { kind: 'renameParameter', from: 'pitch', to: 'pi' });
  expect(s.constraints[0].expression).toBe('[pi] + pi');
  expect(s.constraints[0]).toMatchObject({ value: 2 + Math.PI });
});

it('deletes a dimension referenced alongside a parameter without confusing parameter users with deleted dimensions', () => {
  let s = add(fixture());
  s = edit(s, { kind: 'addConstraint', constraint: { kind: 'distanceX', a: 'a', b: 'b', driving: true, value: 12, expression: 'Width + pitch' } });
  const result = applyCommand(s, { kind: 'removeConstraint', id: 'width' });
  expect(result).not.toHaveProperty('error');
  if ('error' in result) throw new Error(result.error);
  expect(result.notice).toContain('because Width was removed');
  expect(result.sketch.constraints[0]).toMatchObject({ value: 12 });
  expect(result.sketch.constraints[0]).not.toHaveProperty('expression');
});

it('keeps geometry names in their own namespace when a parameter has the same name as a point or entity', () => {
  const s = add(fixture(), 'a');
  expect(displayName(s, 'a')).toBe('Point 1');
  const withEntityName = add(s, 'line');
  expect(displayName(withEntityName, 'line')).toBe('Line 1');
});

it('keeps relation IDs separate from the parameter namespace during resolution and deletion', () => {
  const initial = fixture(); initial.constraints.push({ id: 'align', kind: 'horizontal', line: 'line' });
  let s = add(initial, 'align', '5');
  expect(s.constraints[1]).toMatchObject({ id: 'align', kind: 'horizontal' });
  s = edit(s, { kind: 'setDimensionExpression', id: 'width', expression: 'align' });
  s = edit(s, { kind: 'removeConstraint', id: 'align' });
  expect(s.constraints[0]).toMatchObject({ expression: 'align', value: 5 });
  expect(s.parameters?.[0]).toMatchObject({ name: 'align', value: 5 });
});
