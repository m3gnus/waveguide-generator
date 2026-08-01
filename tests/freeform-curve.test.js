import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildFreeformDisplayCurve,
  computeInflectionSpans,
} from '../src/geometry/freeformCurve.js';

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

test('FREEFORM display curve strength scales the automatic tangent speed', () => {
  const maximumChordDeviation = (strength) => {
    const curve = buildFreeformDisplayCurve({
      points: [
        [0, 12.7],
        [50, 30, 0, strength],
        [100, 50],
      ],
      throatAngleDeg: 15.5,
      mouthAngleDeg: 30,
      sampleCount: 1001,
    });
    return Math.max(
      ...curve.map((point) => {
        const start = point[0] <= 50 ? [0, 12.7] : [50, 30];
        const end = point[0] <= 50 ? [50, 30] : [100, 50];
        const dz = end[0] - start[0];
        const dr = end[1] - start[1];
        return (
          Math.abs((point[0] - start[0]) * dr - (point[1] - start[1]) * dz) / Math.hypot(dz, dr)
        );
      })
    );
  };

  const deviations = [0.5, 1, 2].map(maximumChordDeviation);
  assert.ok(deviations[0] < deviations[1]);
  assert.ok(deviations[1] < deviations[2]);
});

test('FREEFORM endpoint rows override block angles and tangent scales', () => {
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

test('FREEFORM inflection spans report a significant sampled S-curve', () => {
  const curve = buildFreeformDisplayCurve({
    points: [
      [0, 12.7],
      [50, 35, 40],
      [100, 55],
    ],
    throatAngleDeg: 15.5,
    mouthAngleDeg: 25,
  });

  const spans = computeInflectionSpans(curve);

  assert.equal(spans.length, 1);
  assert.equal(spans[0].zStartMm, 50);
  assert.ok(spans[0].zEndMm > 75 && spans[0].zEndMm < 85);
  assert.ok(spans[0].tangentDropDeg > 20 && spans[0].tangentDropDeg < 30);
});

test('FREEFORM inflection spans ignore a clean one-way cone', () => {
  const angleDeg = 20;
  const curve = buildFreeformDisplayCurve({
    points: [
      [0, 12.7],
      [100, 12.7 + 100 * Math.tan((angleDeg * Math.PI) / 180)],
    ],
    throatAngleDeg: angleDeg,
    mouthAngleDeg: angleDeg,
  });

  assert.deepEqual(computeInflectionSpans(curve), []);
});
