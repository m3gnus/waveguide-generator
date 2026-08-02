import test from 'node:test';
import assert from 'node:assert/strict';

import {
  createFreeformScrubState,
  sampleFreeformGridRing,
} from '../src/ui/freeformCrossSectionInset.js';

function point(radiusH, radiusV, angle, z) {
  return [radiusH * Math.cos(angle), radiusV * Math.sin(angle), z];
}

function syntheticGrid({ angles, rings, quadrants = 1234, fullCircle = true, nested = false }) {
  const rows = angles.map((angle) => rings.map((ring) => point(ring.h, ring.v, angle, ring.z)));
  return {
    angle_list: angles,
    grid_n_phi: angles.length,
    grid_n_length: rings.length - 1,
    inner_points: nested ? rows : rows.flat(2),
    quadrants,
    full_circle: fullCircle,
  };
}

test('sampleFreeformGridRing selects and interpolates authoritative axial rings', () => {
  const grid = syntheticGrid({
    angles: [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2],
    rings: [
      { h: 10, v: 5, z: 0 },
      { h: 30, v: 15, z: 40 },
      { h: 50, v: 25, z: 80 },
    ],
    nested: true,
  });

  const ring = sampleFreeformGridRing(grid, 0.25);
  assert.equal(ring.lowerIndex, 0);
  assert.equal(ring.upperIndex, 1);
  assert.equal(ring.mix, 0.5);
  assert.equal(ring.depthMm, 20);
  assert.equal(ring.radiusH, 20);
  assert.equal(ring.radiusV, 10);
  assert.equal(ring.points.length, 4);
});

test('sampleFreeformGridRing mirrors a quadrant-reduced sampled outline', () => {
  const grid = syntheticGrid({
    angles: [0, Math.PI / 4, Math.PI / 2],
    rings: [
      { h: 10, v: 5, z: 0 },
      { h: 20, v: 15, z: 60 },
    ],
    quadrants: 1,
    fullCircle: false,
  });

  const ring = sampleFreeformGridRing(grid, 0.5);
  assert.equal(ring.depthMm, 30);
  assert.equal(ring.radiusH, 15);
  assert.equal(ring.radiusV, 10);
  assert.equal(ring.points.length, 8);
  for (const [x, y] of ring.points) {
    assert.ok(ring.points.some(([otherX, otherY]) => otherX === -x && otherY === y));
    assert.ok(ring.points.some(([otherX, otherY]) => otherX === x && otherY === -y));
  }
});

test('FREEFORM scrub state synchronizes subscribers in both directions', () => {
  const state = createFreeformScrubState(0.2);
  const received = [];
  state.subscribe((t, source) => received.push({ t, source }));

  state.set(0.4, 'inset');
  state.set(0.75, 'editor');

  assert.equal(state.get(), 0.75);
  assert.deepEqual(received, [
    { t: 0.4, source: 'inset' },
    { t: 0.75, source: 'editor' },
  ]);
});
