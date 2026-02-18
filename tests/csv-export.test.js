import test from 'node:test';
import assert from 'node:assert/strict';

import { exportProfilesCSV, exportSlicesCSV } from '../src/export/profiles.js';

test('CSV slices export closes each loop and applies 1/10 scale', () => {
  const params = { angularSegments: 4, lengthSegments: 0 };
  const vertices = [
    10, 0, 0,
    0, 0, 10,
    -10, 0, 0,
    0, 0, -10
  ];

  const csv = exportSlicesCSV(vertices, params);
  const lines = csv.trim().split('\r\n');
  assert.equal(lines.length, 5);
  assert.equal(lines[0], '1.000000;0.000000;0.000000');
  assert.equal(lines[4], lines[0]);
});

test('CSV profiles export iterates along length for each angular position', () => {
  const params = { angularSegments: 2, lengthSegments: 1 };
  // 2 angular segments x 2 length slices = 4 vertices
  const vertices = [
    // j=0: i=0, i=1
    10, 0, 0,
    0, 0, 10,
    // j=1: i=0, i=1
    20, 0, 0,
    0, 0, 20
  ];

  const csv = exportProfilesCSV(vertices, params);
  const sections = csv.trim().split('\r\n\r\n');
  assert.equal(sections.length, 2, 'should have 2 angular profiles');

  // Profile i=0: j=0 then j=1
  const profile0 = sections[0].split('\r\n');
  assert.equal(profile0.length, 2);
  assert.equal(profile0[0], '1.000000;0.000000;0.000000');
  assert.equal(profile0[1], '2.000000;0.000000;0.000000');

  // Profile i=1: j=0 then j=1
  const profile1 = sections[1].split('\r\n');
  assert.equal(profile1.length, 2);
  assert.equal(profile1[0], '0.000000;1.000000;0.000000');
  assert.equal(profile1[1], '0.000000;2.000000;0.000000');
});
