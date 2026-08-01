import test from 'node:test';
import assert from 'node:assert/strict';

import { AppState, SUPPORTED_MODEL_TYPES, normalizePersistedState } from '../src/state.js';
import { getDefaults } from '../src/config/defaults.js';

test('FREEFORM is a supported model with independent valid collection defaults', () => {
  assert.equal(SUPPORTED_MODEL_TYPES.has('FREEFORM'), true);
  const defaults = getDefaults('FREEFORM');
  assert.equal(defaults.morphTarget, 0);
  assert.equal(defaults.length, 120);
  assert.equal(defaults.throatRadius, 12.7);
  assert.equal(defaults.throatAngle, 15.5);
  assert.equal(defaults.mouthRadiusH, 140);
  assert.equal(defaults.mouthRadiusV, 140);
  assert.equal(defaults.angularSegments, 96);
  assert.equal(defaults.lengthSegments, 48);
  assert.deepEqual(defaults.interiorH, []);
  assert.deepEqual(defaults.interiorV, []);
  assert.deepEqual(defaults.crossSections, [
    { t: 0, shape: 'circle' },
    { t: 1, shape: 'ellipse' },
  ]);

  defaults.interiorH.push([50, 60]);
  defaults.crossSections[0].shape = 'ellipse';
  const freshDefaults = getDefaults('FREEFORM');
  assert.deepEqual(freshDefaults.interiorH, []);
  assert.equal(freshDefaults.crossSections[0].shape, 'circle');
});

test('persisted legacy FREEFORM profiles migrate before defaults are merged', () => {
  const normalized = normalizePersistedState({
    type: 'FREEFORM',
    params: {
      profileH: [
        [0, 13],
        [40, 60],
        [150, 170],
      ],
      profileV: [
        [0, 13],
        [80, 75],
        [150, 120],
      ],
      throatAngleH: 17,
      throatAngleV: 17,
      mouthAngleH: 65,
      mouthAngleV: 55,
    },
  });

  assert.equal(normalized.params.length, 150);
  assert.equal(normalized.params.throatRadius, 13);
  assert.equal(normalized.params.throatAngle, 17);
  assert.equal(normalized.params.mouthRadiusH, 170);
  assert.equal(normalized.params.mouthRadiusV, 120);
  assert.deepEqual(normalized.params.interiorH, [{ z: 40, r: 60, angleDeg: null, strength: null }]);
  assert.deepEqual(normalized.params.interiorV, [{ z: 80, r: 75, angleDeg: null, strength: null }]);
  assert.equal(Object.hasOwn(normalized.params, 'profileH'), false);
  assert.equal(Object.hasOwn(normalized.params, 'throatAngleV'), false);
});

test('persisted FREEFORM interior rows migrate to the object anchor model', () => {
  const normalized = normalizePersistedState({
    type: 'FREEFORM',
    params: {
      interiorH: [[40, 60, 22, 1.7]],
      interiorV: [[70, 75, -8]],
    },
  });

  assert.deepEqual(normalized.params.interiorH, [{ z: 40, r: 60, angleDeg: 22, strength: 1.7 }]);
  assert.deepEqual(normalized.params.interiorV, [{ z: 70, r: 75, angleDeg: -8, strength: null }]);
});

test('AppState.loadState migrates legacy FREEFORM params on direct state replacement', () => {
  const state = new AppState();
  state.loadState({
    type: 'FREEFORM',
    params: {
      profileH: [
        [0, 12.7],
        [120, 155],
      ],
      profileV: [
        [0, 12.7],
        [120, 105],
      ],
      throatAngleH: 14,
      throatAngleV: 14,
      mouthAngleH: 62,
      mouthAngleV: 52,
    },
  });

  assert.deepEqual(state.get().params, {
    length: 120,
    throatRadius: 12.7,
    throatAngle: 14,
    mouthRadiusH: 155,
    mouthRadiusV: 105,
    interiorH: [],
    interiorV: [],
    mouthAngleH: 62,
    mouthAngleV: 52,
  });
});

test('AppState.update skips exact no-op updates without version or history churn', () => {
  const state = new AppState();
  state.current = {
    type: 'R-OSSE',
    params: getDefaults('R-OSSE'),
  };
  state.undoStack = [];
  state.redoStack = [];
  state._stateVersion = 0;

  assert.equal(state.update({}, 'R-OSSE'), false);
  assert.equal(state.getVersion(), 0);
  assert.equal(state.undoStack.length, 0);

  assert.equal(state.update({ freqStart: state.current.params.freqStart + 10 }), true);
  assert.equal(state.getVersion(), 1);
  assert.equal(state.undoStack.length, 1);
});

test('AppState.loadState skips exact no-op replacements', () => {
  const state = new AppState();
  const snapshot = {
    type: 'OSSE',
    params: getDefaults('OSSE'),
  };
  state.current = JSON.parse(JSON.stringify(snapshot));
  state.undoStack = [];
  state.redoStack = [];
  state._stateVersion = 0;

  assert.equal(state.loadState(snapshot, 'noop-test'), false);
  assert.equal(state.getVersion(), 0);
  assert.equal(state.undoStack.length, 0);
});

test('persisted states migrate the untouched legacy mesh budget to the larger hard limit', () => {
  const normalized = normalizePersistedState({
    type: 'R-OSSE',
    params: {
      maxTriangles: 18000,
      allowLargeMesh: 0,
    },
  });

  assert.equal(normalized.params.maxTriangles, 50000);
});

test('persisted states preserve a customized mesh budget', () => {
  const normalized = normalizePersistedState({
    type: 'R-OSSE',
    params: {
      maxTriangles: 24000,
      allowLargeMesh: 0,
    },
  });

  assert.equal(normalized.params.maxTriangles, 24000);
});
