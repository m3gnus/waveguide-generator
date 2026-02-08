import test from 'node:test';
import assert from 'node:assert/strict';

import { exportProfilesCSV } from '../src/export/profiles.js';

test('CSV profile export closes each loop and applies 1/10 scale', () => {
  const params = { angularSegments: 4, lengthSegments: 0 };
  const vertices = [
    10, 0, 0,
    0, 0, 10,
    -10, 0, 0,
    0, 0, -10
  ];

  const csv = exportProfilesCSV(vertices, params);
  const lines = csv.trim().split('\r\n');
  assert.equal(lines.length, 5);
  assert.equal(lines[0], '1.000000;0.000000;0.000000');
  assert.equal(lines[4], lines[0]);
});
