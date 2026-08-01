import test from 'node:test';
import assert from 'node:assert/strict';

import { AppState, SUPPORTED_MODEL_TYPES, normalizePersistedState } from '../src/state.js';
import { getDefaults } from '../src/config/defaults.js';

test('FREEFORM is a supported model with independent valid collection defaults', () => {
  assert.equal(SUPPORTED_MODEL_TYPES.has('FREEFORM'), true);
  const defaults = getDefaults('FREEFORM');
  assert.equal(defaults.morphTarget, 0);
  assert.deepEqual(defaults.profileH, [
    [0, 12.7],
    [120, 140],
  ]);
  assert.deepEqual(defaults.profileV, defaults.profileH);
  assert.deepEqual(defaults.crossSections, [
    { t: 0, shape: 'circle' },
    { t: 1, shape: 'ellipse' },
  ]);

  defaults.profileH[0][0] = 99;
  defaults.crossSections[0].shape = 'ellipse';
  const freshDefaults = getDefaults('FREEFORM');
  assert.equal(freshDefaults.profileH[0][0], 0);
  assert.equal(freshDefaults.crossSections[0].shape, 'circle');
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
