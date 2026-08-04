import test from 'node:test';
import assert from 'node:assert/strict';

import {
  fromWireFreeform,
  normalizeAnchor,
  normalizeAnchorList,
  normalizeStations,
  toWireFreeform,
} from '../src/config/freeformModel.js';

test('FREEFORM codec normalizes row/object anchors with one clamp, dedupe, and cap policy', () => {
  assert.deepEqual(normalizeAnchor([4, 8, 22, 1.4]), {
    z: 4,
    r: 8,
    angleDeg: 22,
    strength: null,
  });
  assert.deepEqual(normalizeAnchor({ z: 4, r: 8, strength: 2 }), {
    z: 4,
    r: 8,
    angleDeg: null,
    strength: null,
  });

  const input = [
    { z: 200, r: 200 },
    { z: -4, r: 10 },
    { z: 1, r: 11 },
    ...Array.from({ length: 70 }, (_, index) => ({ z: index + 2, r: index + 20 })),
  ];
  const normalized = normalizeAnchorList(input, { length: 100 });
  assert.equal(normalized.length, 62);
  assert.equal(normalized[0].z, 1);
  assert.equal(normalized[0].r, 10, 'the first row at a clamped duplicate z wins');
  assert.equal(normalized.at(-1).z, 62);
});

test('FREEFORM codec normalizes station endpoints, shapes, ordering, and cap', () => {
  const stations = normalizeStations([
    { t: 1.4, shape: 'rounded_rectangle', cornerRadiusMm: '12.5' },
    { t: 0.5, shape: 'superellipse', exponent: '4' },
    { t: -1, shape: 'ellipse' },
  ]);
  assert.deepEqual(stations, [
    { t: 0, shape: 'circle' },
    { t: 0.5, shape: 'superellipse', exponent: 4 },
    { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 12.5 },
  ]);

  const many = normalizeStations(
    Array.from({ length: 40 }, (_, index) => ({
      t: index / 39,
      shape: index === 0 ? 'circle' : 'ellipse',
    }))
  );
  assert.equal(many.length, 32);
  assert.equal(many.at(-1).t, 1);
});

test('FREEFORM codec drops legacy tangent speeds and round-trips current geometry', () => {
  const params = {
    length: 137.25,
    throatRadius: 12.7,
    throatAngle: 16.25,
    mouthRadiusH: 164.5,
    mouthAngleH: 68.5,
    interiorH: [[40, 58, 21, 1.35]],
    throatTangentScaleH: 1.1,
    mouthTangentScaleH: 0.85,
    mouthRadiusV: 108.75,
    mouthAngleV: 54.5,
    interiorV: [{ z: 78, r: 67, angleDeg: -7, strength: null }],
    throatTangentScaleV: 1.2,
    mouthTangentScaleV: 0.95,
    crossSections: [
      { t: 0, shape: 'circle' },
      { t: 0.45, shape: 'superellipse', exponent: 3.75 },
      { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 14.2 },
    ],
    overshootPolicy: 'allow',
    inflectionPolicy: 'reject',
  };

  const wire = toWireFreeform(params);
  assert.equal(Object.hasOwn(wire, 'overshoot_policy'), false);
  assert.equal(Object.hasOwn(wire.profile_h, 'throat_tangent_scale'), false);
  assert.deepEqual(wire.profile_h.points[1], [40, 58, 21]);
  assert.deepEqual(fromWireFreeform(wire), {
    length: params.length,
    throatRadius: params.throatRadius,
    throatAngle: params.throatAngle,
    mouthRadiusH: params.mouthRadiusH,
    mouthAngleH: params.mouthAngleH,
    interiorH: [{ z: 40, r: 58, angleDeg: 21, strength: null }],
    mouthRadiusV: params.mouthRadiusV,
    mouthAngleV: params.mouthAngleV,
    interiorV: [{ z: 78, r: 67, angleDeg: -7, strength: null }],
    crossSections: params.crossSections,
    inflectionPolicy: params.inflectionPolicy,
  });

  const legacyWire = {
    ...wire,
    profile_h: {
      ...wire.profile_h,
      points: [[0, 12.7], [40, 58, 21, 1.35], [137.25, 164.5]],
      throat_tangent_scale: 1.1,
      mouth_tangent_scale: 0.85,
    },
    overshoot_policy: 'allow',
  };
  const migrated = fromWireFreeform(legacyWire);
  assert.deepEqual(migrated.interiorH, [{ z: 40, r: 58, angleDeg: 21, strength: null }]);
  assert.equal(Object.hasOwn(migrated, 'throatTangentScaleH'), false);
  assert.equal(Object.hasOwn(migrated, 'overshootPolicy'), false);
});
