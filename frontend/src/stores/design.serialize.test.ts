import { beforeEach, expect, test } from 'vitest';
import { designForFamily, encodeQuadrants, resetDesignStore, serializeDesign, useDesignStore } from './design';

beforeEach(() => resetDesignStore());

test('new designs use ATH\'s verified 5 mm wall default', () => {
  for (const family of ['R-OSSE', 'OSSE', 'ICW', 'FREEFORM'] as const) {
    const design = designForFamily(family);
    expect(design.mesh.wall_thickness).toBe(5);
    expect((serializeDesign(design).mesh as Record<string, unknown>).wall_thickness).toBe(5);
  }
});

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

test('FREEFORM payload uses ellipse at the throat and omits removed tangent controls', () => {
  const payload = serializeDesign(designForFamily('FREEFORM'));
  expect((payload.cross_sections as { shape: string }[])[0].shape).toBe('ellipse');
  expect(payload).not.toHaveProperty('overshoot_policy');
  expect(payload.profile_h).not.toHaveProperty('throat_tangent_scale');
  expect(payload.profile_h).not.toHaveProperty('mouth_tangent_scale');
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

test('serializes raw ATH expressions and four-value enclosure tuples without leaking the sidecar', () => {
  useDesignStore.getState().updateExpression('R', { value: null, raw: '70 * (1 + cos(p))' });
  useDesignStore.getState().updateExpression('enclosure.front_resolution', { value: null, raw: '25,25,20,20' });
  const payload = serializeDesign(useDesignStore.getState().design);
  expect(payload.R).toEqual({ value: null, raw: '70 * (1 + cos(p))' });
  expect((payload.enclosure as Record<string, unknown>).front_resolution).toEqual({ value: null, raw: '25,25,20,20' });
  expect(payload).not.toHaveProperty('_expressions');
});

test.each([
  { value: 12.7, raw: '6.35*2' },
  { value: null, raw: '10 + 2*p' },
])('serializes OSSE r0 raw spelling into schema wire for CFG round-trip', (expression) => {
  useDesignStore.getState().setFamily('OSSE');
  useDesignStore.getState().updateExpression('r0', expression);
  expect(serializeDesign(useDesignStore.getState().design).r0).toEqual(expression);
});

test('FREEFORM length is independent while mouth radius still targets the last imported point', () => {
  useDesignStore.getState().setFamily('FREEFORM');
  useDesignStore.getState().updateValue('profile_h.points', [{ t: 0, r: 12 }, { t: 1 / 3, r: 50 }, { t: 1, r: 140 }]);
  useDesignStore.getState().updateValue('length', 180);
  useDesignStore.getState().updateValue('profile_h.points.$last.r', 170);
  expect(useDesignStore.getState().design.length).toBe(180);
  expect(useDesignStore.getState().design.profile_h!.points).toEqual([{ t: 0, r: 12 }, { t: 1 / 3, r: 50 }, { t: 1, r: 170 }]);
});

test('structured and quadrant edits clear stale imported expression sidecars', () => {
  const design = designForFamily('FREEFORM');
  design._expressions = {
    'profile_h.points.1.r': { value: 140, raw: 'mouthRadius()' },
    'mesh.quadrants': { value: 14, raw: '14' },
  };
  useDesignStore.getState().loadDesign(design);
  useDesignStore.getState().updateValue('profile_h.points', [{ t: 0, r: 12.7 }, { t: 1, r: 150 }]);
  useDesignStore.getState().setQuadrants([1, 2]);
  expect(useDesignStore.getState().design._expressions).toBeUndefined();
  const payload = serializeDesign(useDesignStore.getState().design);
  expect(((payload.profile_h as { points: { r: number }[] }).points[1].r)).toBe(150);
  expect((payload.mesh as Record<string, unknown>).quadrants).toBe(12);
});

test('serializes absent paths as null, strips the sidecar, and emits an edited value', () => {
  const design = designForFamily('R-OSSE');
  design._absent = ['morph.target_width'];
  useDesignStore.getState().loadDesign(design);
  const absentPayload = serializeDesign(useDesignStore.getState().design);
  expect((absentPayload.morph as Record<string, unknown>).target_width).toBeNull();
  expect(absentPayload).not.toHaveProperty('_absent');

  useDesignStore.getState().updateValue('morph.target_width', 320);
  expect(useDesignStore.getState().design._absent ?? []).not.toContain('morph.target_width');
  expect((serializeDesign(useDesignStore.getState().design).morph as Record<string, unknown>).target_width).toBe(320);
});

test('round-trips absent wall thickness as null and preserves an explicit zero', () => {
  const design = designForFamily('R-OSSE');
  design._absent = ['mesh.wall_thickness'];
  useDesignStore.getState().loadDesign(design);
  expect((serializeDesign(useDesignStore.getState().design).mesh as Record<string, unknown>).wall_thickness).toBeNull();

  useDesignStore.getState().updateValue('mesh.wall_thickness', 0);
  expect(useDesignStore.getState().design._absent ?? []).not.toContain('mesh.wall_thickness');
  expect((serializeDesign(useDesignStore.getState().design).mesh as Record<string, unknown>).wall_thickness).toBe(0);
});

test('clears concrete absent paths when a $last array path is edited', () => {
  const design = designForFamily('FREEFORM');
  design._absent = ['profile_h.points.1.r'];
  useDesignStore.getState().loadDesign(design);
  useDesignStore.getState().updateValue('profile_h.points.$last.r', 175);
  expect(useDesignStore.getState().design._absent).toBeUndefined();
  expect(((serializeDesign(useDesignStore.getState().design).profile_h as { points: { r: number }[] }).points[1].r)).toBe(175);
});
