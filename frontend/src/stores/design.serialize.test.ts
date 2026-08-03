import { beforeEach, expect, test } from 'vitest';
import { designForFamily, encodeQuadrants, resetDesignStore, serializeDesign } from './design';

beforeEach(() => resetDesignStore());

test('R-OSSE payload drops the OSSE-only guiding_curve and all UI mirrors', () => {
  const payload = serializeDesign(designForFamily('R-OSSE'));
  expect(payload).not.toHaveProperty('guiding_curve');
  expect(payload).not.toHaveProperty('quadrants');
  const enclosure = payload.enclosure as Record<string, unknown>;
  expect(enclosure).not.toHaveProperty('baffle_margin');
  expect(enclosure).toHaveProperty('space_l');
  expect((payload.mesh as Record<string, unknown>).quadrants).toBeTypeOf('number');
});

test('OSSE payload keeps guiding_curve (schema accepts it there)', () => {
  const payload = serializeDesign(designForFamily('OSSE'));
  expect(payload).toHaveProperty('guiding_curve');
});

test('ICW and FREEFORM payloads also drop guiding_curve', () => {
  for (const family of ['ICW', 'FREEFORM'] as const) {
    expect(serializeDesign(designForFamily(family))).not.toHaveProperty('guiding_curve');
  }
});

test.each([
  [[1], 1],
  [[1, 2], 12],
  [[1, 4], 14],
  [[4, 2, 1, 3], 1234],
] as const)('serializes ATH quadrant digits %j as %i', (quadrants, encoded) => {
  const design = designForFamily('R-OSSE');
  design.quadrants = [...quadrants];
  expect(encodeQuadrants(quadrants)).toBe(encoded);
  expect((serializeDesign(design).mesh as Record<string, unknown>).quadrants).toBe(encoded);
});

test('preserves every authoritative enclosure field and drops only the UI mirror', () => {
  const design = designForFamily('R-OSSE');
  design.enclosure = {
    depth: 300, edge_radius: 19, edge_type: 2,
    space_l: 11, space_t: 12, space_r: 13, space_b: 14,
    front_resolution: 7, back_resolution: 9, baffle_margin: 999,
  };
  expect(serializeDesign(design).enclosure).toEqual({
    depth: 300, edge_radius: 19, edge_type: 2,
    space_l: 11, space_t: 12, space_r: 13, space_b: 14,
    front_resolution: 7, back_resolution: 9,
  });
});
