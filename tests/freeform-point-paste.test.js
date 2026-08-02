import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { exportProfilesCSV, exportSlicesCSV } from '../src/export/profiles.js';
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

test('FREEFORM point paste matches mesher numeric domains and 3-column rows', () => {
  assert.match(parseFreeformPointPaste('0 -5').error, /radius must be greater than 0/);
  assert.match(parseFreeformPointPaste('-10 20').error, /z must be at least 0/);

  const weak = parseFreeformPointPaste('20 35 22 0.05');
  assert.equal(weak.ok, true);
  assert.equal(weak.points[0].strength, 0.05);

  const three = parseFreeformPointPaste('20 35 22');
  assert.equal(three.ok, true);
  assert.equal(three.format, 'three-column');
  assert.deepEqual(three.points[0], { z: 20, r: 35, angleDeg: 22, strength: null });

  assert.equal(parseFreeformPointPaste('0 13 90 1').ok, true);
  assert.match(parseFreeformPointPaste('50 60 90 1').error, /interior angle/);
});

test('FREEFORM full-profile endpoint rows preserve tangent overrides', () => {
  const parsed = parseFreeformPointPaste('0 13 30 1.2\n50 60 22 0.05\n120 140 45 0.8');
  const prepared = prepareFreeformPointPastePatch(parsed, getDefaults('FREEFORM'), { plane: 'H' });

  assert.equal(prepared.patch.throatAngle, 30);
  assert.equal(prepared.patch.mouthAngleH, 45);
  assert.equal(prepared.patch.throatTangentScaleH, 1.2);
  assert.equal(prepared.patch.mouthTangentScaleH, 0.8);
  assert.equal(prepared.patch.interiorH[0].strength, 0.05);
});

test('FREEFORM paste normalizes Fusion headers and rejects slices exports', () => {
  const spaced = parseFreeformPointPaste('# x_cm; y_cm; z_cm\n1;0;0\n2;0;5\n\n0;1;0\n0;2;5');
  assert.equal(spaced.ok, true);
  assert.equal(spaced.layout, 'fusion-angular');

  const slices = exportSlicesCSV([10, 0, 0, 0, 0, 10, -10, 0, 0, 0, 0, -10], {
    angularSegments: 4,
    lengthSegments: 0,
  });
  const rejected = parseFreeformPointPaste(slices);
  assert.equal(rejected.ok, false);
  assert.match(rejected.error, /slices export.*profiles export/i);
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

test('FREEFORM full-profile paste surfaces a length mismatch and can adopt the pasted extent', () => {
  const parsed = parseFreeformPointPaste('0 13\n100 151');
  const params = { ...getDefaults('FREEFORM'), length: 120 };
  const pending = prepareFreeformPointPastePatch(parsed, params, { plane: 'H' });

  assert.equal(pending.patch, null);
  assert.deepEqual(pending.decision, {
    type: 'length-mismatch',
    currentLength: 120,
    suggestedLength: 100,
    planes: ['H'],
  });
  assert.match(pending.message, /ends at 100 mm; model length is 120 mm/);

  const adjusted = prepareFreeformPointPastePatch(parsed, params, {
    plane: 'H',
    adjustLength: true,
  });
  assert.deepEqual(adjusted.patch, {
    length: 100,
    interiorH: [],
    throatRadius: 13,
    mouthRadiusH: 151,
  });
});

test('FREEFORM paste treats physical endpoint tolerance as a mouth and leaves fragments partial', () => {
  const params = { ...getDefaults('FREEFORM'), length: 120 };
  const nearMouth = prepareFreeformPointPastePatch(
    parseFreeformPointPaste('0 13\n119.999 151'),
    params,
    { plane: 'H' }
  );
  assert.deepEqual(nearMouth.patch.interiorH, []);
  assert.equal(nearMouth.patch.mouthRadiusH, 151);

  const fragment = prepareFreeformPointPastePatch(
    parseFreeformPointPaste('40 60\n80 100'),
    params,
    { plane: 'H' }
  );
  assert.equal(fragment.decision, null);
  assert.deepEqual(
    fragment.patch.interiorH.map(({ z, r }) => [z, r]),
    [
      [40, 60],
      [80, 100],
    ]
  );
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
