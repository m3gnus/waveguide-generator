import test from 'node:test';
import assert from 'node:assert/strict';

import { buildFreeformDisplayCurve } from '../src/geometry/freeformCurve.js';

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
