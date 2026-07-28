import test from 'node:test';
import assert from 'node:assert/strict';

import { applySmoothing } from '../src/results/smoothing.js';

test('applySmoothing ignores nullable samples instead of averaging them as zero', () => {
  const smoothed = applySmoothing([100, 125, 160], [90, null, 96], '1/1');

  assert.equal(smoothed.length, 3);
  assert.equal(
    smoothed.every((value) => value === null || Number.isFinite(value)),
    true
  );
  assert.ok(
    smoothed[1] > 80,
    `expected nullable sample to be interpolated from neighbors, got ${smoothed[1]}`
  );
});

test('applySmoothing preserves nullable samples when no finite neighbor is in range', () => {
  const smoothed = applySmoothing([100, 200, 400], [90, null, 96], '1/1');

  assert.equal(smoothed[1], null);
});

test('applySmoothing smooths a two-sample fractional-octave response', () => {
  const smoothed = applySmoothing([100, 110], [0, 10], '1/1');

  assert.ok(smoothed[0] > 0 && smoothed[0] < 5);
  assert.ok(smoothed[1] > 5 && smoothed[1] < 10);
});

test('applySmoothing keeps ERB smoothing inside the linear-Hz bandwidth', () => {
  const smoothed = applySmoothing([100, 119, 200], [0, 100, 0], 'erb');

  assert.equal(smoothed[0], 0);
});
