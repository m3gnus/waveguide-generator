import assert from 'node:assert/strict';
import test from 'node:test';

import { firstLevelCrossing, sampleBalloonGrid } from '../src/ui/results/forwardBeamPanel.js';

test('sampleBalloonGrid interpolates theta and wraps azimuth', () => {
  const theta = [0, 10];
  const phi = [0, 90, 180, 270];
  const grid = [
    [0, 0, 0, 0],
    [-10, -20, -30, -40],
  ];

  assert.equal(sampleBalloonGrid(theta, phi, grid, 5, 45), -7.5);
  assert.equal(sampleBalloonGrid(theta, phi, grid, 10, 315), -25);
});

test('firstLevelCrossing returns the interpolated first outward crossing', () => {
  assert.equal(firstLevelCrossing([0, 5, 10, 15], [0, -3, -9, -4], -6), 7.5);
  assert.equal(firstLevelCrossing([0, 5, 10], [0, -2, -4], -6), null);
});
