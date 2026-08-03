import { beforeEach, expect, test } from 'vitest';
import { designForFamily, resetDesignStore, serializeDesign } from './design';

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
