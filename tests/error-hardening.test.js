import test from 'node:test';
import assert from 'node:assert/strict';

import { EventBus } from '../src/events.js';
import { AppState, normalizePersistedState } from '../src/state.js';
import { getDefaults } from '../src/config/defaults.js';

test('EventBus isolates listener failures and continues notifying remaining listeners', () => {
  const bus = new EventBus();
  const received = [];

  bus.on('demo:event', () => {
    throw new Error('listener failure');
  });
  bus.on('demo:event', (payload) => {
    received.push(payload.value);
  });

  assert.doesNotThrow(() => {
    bus.emit('demo:event', { value: 42 });
  });
  assert.deepEqual(received, [42]);
});

test('EventBus wildcard listener failures are isolated from other wildcard listeners', () => {
  const bus = new EventBus();
  const wildcardEvents = [];

  bus.on('*', () => {
    throw new Error('wildcard failure');
  });
  bus.on('*', (payload) => {
    wildcardEvents.push(payload.event);
  });

  assert.doesNotThrow(() => {
    bus.emit('state:updated', { ok: true });
  });
  assert.deepEqual(wildcardEvents, ['state:updated']);
});

test('normalizePersistedState rejects malformed storage schema', () => {
  assert.equal(normalizePersistedState(null), null);
  assert.equal(normalizePersistedState({}), null);
  assert.equal(normalizePersistedState({ type: 'R-OSSE' }), null);
  assert.equal(normalizePersistedState({ type: 'UNKNOWN', params: {} }), null);
});

test('AppState falls back to defaults when persisted state schema is invalid', () => {
  const originalStorage = global.localStorage;
  let removedKey = null;

  global.localStorage = {
    getItem(key) {
      if (key === 'ath_state') {
        return JSON.stringify({ type: 'UNKNOWN', params: {} });
      }
      return null;
    },
    setItem() {},
    removeItem(key) {
      removedKey = key;
    }
  };

  try {
    const state = new AppState();
    assert.equal(state.get().type, 'R-OSSE');
    assert.equal(removedKey, 'ath_state');
  } finally {
    global.localStorage = originalStorage;
  }
});

test('AppState hydrates valid persisted state and merges defaults', () => {
  const originalStorage = global.localStorage;
  const defaults = getDefaults('OSSE');
  const sampleKey = Object.keys(defaults)[0];
  const customValue = typeof defaults[sampleKey] === 'number' ? defaults[sampleKey] + 5 : 'custom-value';

  global.localStorage = {
    getItem(key) {
      if (key === 'ath_state') {
        return JSON.stringify({
          type: 'OSSE',
          params: { [sampleKey]: customValue }
        });
      }
      return null;
    },
    setItem() {},
    removeItem() {}
  };

  try {
    const state = new AppState();
    const loaded = state.get();
    assert.equal(loaded.type, 'OSSE');
    assert.equal(loaded.params[sampleKey], customValue);
    assert.ok(Object.prototype.hasOwnProperty.call(loaded.params, sampleKey));
  } finally {
    global.localStorage = originalStorage;
  }
});
