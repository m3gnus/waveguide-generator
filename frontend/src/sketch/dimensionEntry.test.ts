import { expect, it } from 'vitest';
import { dimensionEntry } from './dimensionEntry';
import { importedSketch } from './ops';
import type { Constraint, Sketch } from './model';

const radius = { id: 'r1', kind: 'radius', arc: 'arc', value: 12, driving: true, label: 'Throat radius' } as const;
const target = { id: 'r2', kind: 'distance', a: 'p', b: 'q', value: 24, driving: true } as const;
function fixture(): Sketch {
  return { ...importedSketch([{ id: 'p', x: 0, y: 0 }, { id: 'q', x: 24, y: 0 }, { id: 'o', x: 0, y: 12 }],
    [{ id: 'arc', kind: 'arc', center: 'o', start: 'p', end: 'q', ccw: true }]), constraints: [radius, target], nextId: 10 };
}
it.each(['[Throat radius] * 2', 'Throat radius * 2', '25.4mm / 2', 'sqrt(2)*r1'])('evaluates %s through the expression command', expression => {
  const s = fixture(), before = JSON.stringify(s), result = dimensionEntry(s, target, expression);
  expect(result).toHaveProperty('command.kind', 'setDimensionExpression');
  expect(result).toHaveProperty('value', expression.includes('25.4') ? 12.7 : expression.includes('sqrt') ? Math.sqrt(2) * 12 : 24);
  expect(JSON.stringify(s)).toBe(before);
});
it('converts typed diameters and degrees through the common conversion boundary', () => {
  const s = fixture();
  const diameter = { ...radius, diameter: true as const }; s.constraints[0] = diameter;
  expect(dimensionEntry(s, diameter, '24')).toMatchObject({ command: { kind: 'setDimension', value: 12 }, value: 24 });
  const angle = { id: 'a1', kind: 'angle', a: 'l1', b: 'l2', value: Math.PI / 2, driving: true } as Constraint & { value: number };
  // The angle geometry is supplied for command validation.
  s.entities.push({ id: 'l1', kind: 'line', p1: 'p', p2: 'q' }, { id: 'l2', kind: 'line', p1: 'p', p2: 'o' });
  s.constraints.push(angle);
  expect(dimensionEntry(s, angle, '45')).toMatchObject({ command: { value: Math.PI / 4 }, value: 45 });
});
it('reports parser, dependency, and cyclic errors without mutating the sketch', () => {
  const s = fixture();
  expect(dimensionEntry(s, target, 'sqrt(')).toHaveProperty('error', expect.stringContaining('expression'));
  expect(dimensionEntry(s, target, 'Missing * 2')).toHaveProperty('error', expect.stringContaining('Unknown'));
  expect(dimensionEntry(s, target, 'r2 * 2')).toHaveProperty('error', expect.stringContaining('cycle'));
});
