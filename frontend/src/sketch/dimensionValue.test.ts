import { expect, it } from 'vitest';
import { dimensionLabel } from './dimensionGraphics';
import { displayValue, internalValue } from './dimensionValue';
import type { Constraint } from './model';

it('uses the same display conversion for radius/diameter canvas labels, angle labels and numeric entry', () => {
  const radius = { id: 'c1', kind: 'radius', arc: 'a1', value: 5, driving: true } as const;
  expect(dimensionLabel(radius)).toBe('R 5.00 mm');
  expect(dimensionLabel({ ...radius, diameter: true })).toBe('Ø 10.00 mm');
  expect(dimensionLabel({ ...radius, diameter: true, driving: false })).toBe('(Ø 10.00 mm)');
  const angle = { id: 'c2', kind: 'angle', a: 'l1', b: 'l2', value: Math.PI / 2, driving: true } as const;
  expect(dimensionLabel(angle)).toBe('90.00°');
  for (const c of [radius, { ...radius, diameter: true as const }, angle] satisfies (Constraint & { value: number })[]) expect(internalValue(c, displayValue(c))).toBeCloseTo(c.value, 12);
});
