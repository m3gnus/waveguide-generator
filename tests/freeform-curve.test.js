import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { buildFreeformDisplayCurve } from '../src/geometry/freeformCurve.js';

const authoritativeFixture = JSON.parse(
  readFileSync(new URL('./fixtures/freeform-authoritative.json', import.meta.url), 'utf8')
);

function derivativeAngle(points, atEnd = false) {
  const selected = atEnd ? points.slice(-4) : points.slice(0, 4);
  const values = atEnd ? selected.reverse() : selected;
  // Four equally spaced samples of a cubic give its endpoint derivative exactly.
  const dz = (-11 * values[0][0] + 18 * values[1][0] - 9 * values[2][0] + 2 * values[3][0]) / 6;
  const dr = (-11 * values[0][1] + 18 * values[1][1] - 9 * values[2][1] + 2 * values[3][1]) / 6;
  const angle = (Math.atan2(dr, dz) * 180) / Math.PI;
  return atEnd ? angle - 180 * Math.sign(angle || 1) : angle;
}

function includesPoint(points, expected, tolerance = 1e-10) {
  return points.some(
    ([z, radius]) =>
      Math.abs(z - expected[0]) <= tolerance && Math.abs(radius - expected[1]) <= tolerance
  );
}

test('FREEFORM display curve passes through two anchors and honors endpoint directions', () => {
  const curve = buildFreeformDisplayCurve({
    points: [
      [0, 12.7],
      [120, 140],
    ],
    throatAngleDeg: 15,
    mouthAngleDeg: 60,
    throatTangentScale: 1.2,
    mouthTangentScale: 0.8,
  });

  assert.ok(includesPoint(curve, [0, 12.7]));
  assert.ok(includesPoint(curve, [120, 140]));
  assert.ok(Math.abs(derivativeAngle(curve) - 15) < 1e-6);
  assert.ok(Math.abs(derivativeAngle(curve, true) - 60) < 1e-6);
});

test('FREEFORM display curve passes exactly through interior anchors', () => {
  const anchors = [
    [0, 12.7],
    [28, 24],
    [67, 72],
    [93, 105],
    [120, 140],
  ];
  const curve = buildFreeformDisplayCurve({
    points: anchors,
    throatAngleDeg: -12,
    mouthAngleDeg: 42,
    throatTangentScale: 0.7,
    mouthTangentScale: 1.4,
  });

  for (const anchor of anchors) assert.ok(includesPoint(curve, anchor));
  assert.ok(Math.abs(derivativeAngle(curve) + 12) < 1e-6);
  assert.ok(Math.abs(derivativeAngle(curve, true) - 42) < 1e-6);
});

test('FREEFORM display curve honors an interior per-anchor tangent direction', () => {
  const curve = buildFreeformDisplayCurve({
    points: [
      [0, 12.7],
      [50, 30, 35],
      [100, 50],
    ],
    throatAngleDeg: 15.5,
    mouthAngleDeg: 30,
    sampleCount: 10_001,
  });
  const anchorIndex = curve.findIndex(([z, radius]) => z === 50 && radius === 30);
  const before = curve[anchorIndex - 1];
  const after = curve[anchorIndex + 1];
  const sampledAngle = (Math.atan2(after[1] - before[1], after[0] - before[0]) * 180) / Math.PI;

  assert.ok(Math.abs(sampledAngle - 35) < 0.01);
});

test('FREEFORM display curve ignores removed per-anchor tangent strength', () => {
  const base = buildFreeformDisplayCurve({
    points: [[0, 12.7], [50, 30, 0], [100, 50]],
    throatAngleDeg: 15.5,
    mouthAngleDeg: 30,
  });
  const legacy = buildFreeformDisplayCurve({
    points: [[0, 12.7], [50, 30, 0, 2.5], [100, 50]],
    throatAngleDeg: 15.5,
    mouthAngleDeg: 30,
  });
  assert.deepEqual(legacy, base);
});

test('FREEFORM endpoint rows override block angles while removed scales are ignored', () => {
  const curve = buildFreeformDisplayCurve({
    points: [
      [0, 12.7, 10],
      [50, 30],
      [100, 50, 25, 0.5],
    ],
    throatAngleDeg: -20,
    mouthAngleDeg: -40,
    throatTangentScale: 3,
    mouthTangentScale: 3,
  });

  assert.ok(Math.abs(derivativeAngle(curve) - 10) < 1e-6);
  assert.ok(Math.abs(derivativeAngle(curve, true) - 25) < 1e-6);
});

test('FREEFORM optimistic preview stays within 0.1 mm of mesher curveSamples fixture', () => {
  const { params, freeform } = authoritativeFixture;
  const interpolateRadius = (curve, z) => {
    for (let index = 0; index < curve.length - 1; index += 1) {
      const left = curve[index];
      const right = curve[index + 1];
      if (z < left[0] || z > right[0]) continue;
      const fraction = (z - left[0]) / (right[0] - left[0]);
      return left[1] + fraction * (right[1] - left[1]);
    }
    return curve.at(-1)[1];
  };

  for (const plane of ['H', 'V']) {
    const preview = buildFreeformDisplayCurve({
      points: [
        [0, params.throatRadius],
        ...params[`interior${plane}`],
        [params.length, params[`mouthRadius${plane}`]],
      ],
      throatAngleDeg: params.throatAngle,
      mouthAngleDeg: params[`mouthAngle${plane}`],
      throatTangentScale: params[`throatTangentScale${plane}`],
      mouthTangentScale: params[`mouthTangentScale${plane}`],
      sampleCount: 192,
    });
    const exact = freeform.curveSamples[plane];
    const maxRadiusErrorMm = Math.max(
      ...preview.map(([z, radius]) => Math.abs(radius - interpolateRadius(exact, z)))
    );
    assert.ok(maxRadiusErrorMm < 0.1, `${plane} max |dr| was ${maxRadiusErrorMm} mm`);
  }
});
