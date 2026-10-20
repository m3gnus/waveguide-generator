// @vitest-environment node
import { expect, it } from 'vitest';
import { geometryHit } from './geometryHit';

it('gives a nearby marker priority over a closer curve and band, then falls back by geometry class', () => {
  const point = { id: 'marker', kind: 'point', gap: 6 };
  const wall = { id: 'wall', kind: 'polyline', gap: 2 };
  const band = { id: 'band', kind: 'polyline', role: 'keepout', gap: 0 };
  expect(geometryHit([band, wall, point], 7)?.id).toBe('marker');
  expect(geometryHit([band, wall, point], 5)?.id).toBe('wall');
  expect(geometryHit([band, wall, point], 1)?.id).toBe('band');
  expect(geometryHit([{ ...point, gap: 8 }], 7)).toBeUndefined();
});

it('chooses the nearest hit within a geometry class', () => {
  expect(geometryHit([{ kind: 'point', gap: 4 }, { kind: 'point', gap: 1 }], 7)?.gap).toBe(1);
  expect(geometryHit([{ kind: 'segment', gap: 3 }, { kind: 'polyline', gap: 2 }], 7)?.gap).toBe(2);
});
