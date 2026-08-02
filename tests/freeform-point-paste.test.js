import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { exportProfilesCSV } from '../src/export/profiles.js';
import {
  parseFreeformPointPaste,
  prepareFreeformPointPastePatch,
} from '../src/modules/design/freeformPointPaste.js';
import { AppState } from '../src/state.js';

test('FREEFORM point paste parses 2-column whitespace, comma, and tab rows in mm', () => {
  for (const text of ['10 20\n30 40', '10,20\n30,40', '10\t20\n30\t40']) {
    const parsed = parseFreeformPointPaste(text);
    assert.equal(parsed.ok, true);
    assert.equal(parsed.format, 'two-column');
    assert.deepEqual(
      parsed.points.map(({ z, r }) => [z, r]),
      [
        [10, 20],
        [30, 40],
      ]
    );
  }
});

test('FREEFORM point paste parses 4-column CAD anchor rows', () => {
  const parsed = parseFreeformPointPaste('z r angleDeg strength\n20 35 22 1.4\n70,90,-12,0.8');
  assert.equal(parsed.ok, true);
  assert.equal(parsed.format, 'four-column');
  assert.deepEqual(parsed.points, [
    { z: 20, r: 35, angleDeg: 22, strength: 1.4 },
    { z: 70, r: 90, angleDeg: -12, strength: 0.8 },
  ]);
});

test('FREEFORM compact profile CSV skips its header, converts cm to mm, and maps H/V', () => {
  const parsed = parseFreeformPointPaste('# z_cm;r_h_cm;r_v_cm\n0;1.27;1.27\n5;4.2;3.6\n12;14;10', {
    plane: 'V',
  });
  assert.equal(parsed.ok, true);
  assert.equal(parsed.format, 'profile-csv');
  assert.equal(parsed.layout, 'compact-hv');
  assert.equal(parsed.supportsBoth, true);
  assert.deepEqual(
    parsed.points.map(({ z, r }) => [z, r]),
    [
      [0, 12.7],
      [50, 36],
      [120, 100],
    ]
  );
  assert.deepEqual(
    parsed.pointsByPlane.H.map(({ z, r }) => [z, r]),
    [
      [0, 12.7],
      [50, 42],
      [120, 140],
    ]
  );
});

test('FREEFORM point paste reports the first malformed row clearly', () => {
  const parsed = parseFreeformPointPaste('# comment\nz r\n10 20\n30 garbage');
  assert.equal(parsed.ok, false);
  assert.equal(parsed.rowCount, 1);
  assert.match(parsed.error, /^Line 4: column 2 must be a finite number\.$/);

  const garbage = parseFreeformPointPaste('not useful garbage');
  assert.equal(garbage.ok, false);
  assert.match(garbage.error, /^Line 1:/);

  const missing = parseFreeformPointPaste('1;;3');
  assert.equal(missing.ok, false);
  assert.equal(missing.error, 'Line 1: column 2 must be a finite number.');
});

test('FREEFORM point paste decimates over-limit interiors with the conversion decimator', () => {
  const text = Array.from({ length: 70 }, (_, index) => {
    const z = index + 1;
    return `${z} ${20 + Math.sin(index / 3) * 5}`;
  }).join('\n');
  const parsed = parseFreeformPointPaste(text);
  const prepared = prepareFreeformPointPastePatch(parsed, getDefaults('FREEFORM'), { plane: 'H' });
  assert.equal(prepared.patch.interiorH.length, 62);
  assert.equal(prepared.reports[0].original, 70);
  assert.equal(prepared.message, 'H: kept 62 of 70');
});

test('FREEFORM point paste replaces one plane and updates throat/mouth in one undo step', () => {
  const state = new AppState();
  state.loadState(
    {
      type: 'FREEFORM',
      params: {
        ...getDefaults('FREEFORM'),
        interiorH: [[30, 45]],
        interiorV: [[60, 70]],
      },
    },
    'point-paste-test'
  );
  state.undoStack = [];
  state.redoStack = [];
  const before = JSON.parse(JSON.stringify(state.get()));
  const parsed = parseFreeformPointPaste('0 13\n25 40\n80 95\n120 150');
  const prepared = prepareFreeformPointPastePatch(parsed, state.get().params, { plane: 'H' });
  state.update(prepared.patch);

  assert.equal(state.undoStack.length, 1);
  assert.equal(state.get().params.throatRadius, 13);
  assert.equal(state.get().params.mouthRadiusH, 150);
  assert.deepEqual(state.get().params.interiorH, [
    { z: 25, r: 40, angleDeg: null, strength: null },
    { z: 80, r: 95, angleDeg: null, strength: null },
  ]);
  assert.deepEqual(state.get().params.interiorV, before.params.interiorV);

  state.undo();
  assert.deepEqual(state.get(), before);
});

test('profile CSV export header round-trips H/V profiles through paste import', () => {
  const angularSegments = 4;
  const lengthSegments = 2;
  const depths = [0, 50, 100];
  const radiiH = [12.7, 55.123456, 140.654321];
  const radiiV = [12.7, 43.765432, 100.2468];
  const vertices = [];
  for (let j = 0; j <= lengthSegments; j += 1) {
    vertices.push(
      radiiH[j],
      depths[j],
      0,
      0,
      depths[j],
      radiiV[j],
      -radiiH[j],
      depths[j],
      0,
      0,
      depths[j],
      -radiiV[j]
    );
  }

  const csv = exportProfilesCSV(vertices, { angularSegments, lengthSegments });
  assert.equal(csv.startsWith('# x_cm;y_cm;z_cm\r\n'), true);
  const parsed = parseFreeformPointPaste(csv);
  assert.equal(parsed.ok, true);
  assert.equal(parsed.layout, 'fusion-angular');
  const prepared = prepareFreeformPointPastePatch(
    parsed,
    { ...getDefaults('FREEFORM'), length: 100 },
    { plane: 'H', applyBoth: true }
  );

  assert.ok(Math.abs(prepared.patch.throatRadius - radiiH[0]) <= 1e-5);
  assert.ok(Math.abs(prepared.patch.mouthRadiusH - radiiH[2]) <= 1e-5);
  assert.ok(Math.abs(prepared.patch.mouthRadiusV - radiiV[2]) <= 1e-5);
  assert.ok(Math.abs(prepared.patch.interiorH[0].r - radiiH[1]) <= 1e-5);
  assert.ok(Math.abs(prepared.patch.interiorV[0].r - radiiV[1]) <= 1e-5);
});
