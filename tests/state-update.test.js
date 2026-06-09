import test from 'node:test';
import assert from 'node:assert/strict';

import { AppState } from '../src/state.js';
import { getDefaults } from '../src/config/defaults.js';

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
