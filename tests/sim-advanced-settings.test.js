import test from 'node:test';
import assert from 'node:assert/strict';

import {
  RECOMMENDED_DEFAULTS,
  loadSimAdvancedSettings,
  saveSimAdvancedSettings,
  resetSimAdvancedSettings,
  getCurrentSimAdvancedSettings,
} from '../src/ui/settings/simAdvancedSettings.js';

const SETTINGS_KEY = 'waveguide-sim-advanced-settings';

const store = {};

global.localStorage = {
  getItem: (key) => store[key] ?? null,
  setItem: (key, value) => {
    store[key] = value;
  },
  removeItem: (key) => {
    delete store[key];
  },
  clear: () => {
    Object.keys(store).forEach((key) => delete store[key]);
  },
};

test('RECOMMENDED_DEFAULTS no longer exposes bemPrecision', () => {
  assert.equal('bemPrecision' in RECOMMENDED_DEFAULTS, false);
});

test('RECOMMENDED_DEFAULTS has expected keys', () => {
  assert.equal(typeof RECOMMENDED_DEFAULTS.solverBackend, 'string');
  assert.equal('useBurtonMiller' in RECOMMENDED_DEFAULTS, false);
  assert.equal('enableWarmup' in RECOMMENDED_DEFAULTS, false);
  assert.equal('bemPrecision' in RECOMMENDED_DEFAULTS, false);
  assert.equal('quadratureRegular' in RECOMMENDED_DEFAULTS, false);
  assert.equal('workgroupSizeMultiple' in RECOMMENDED_DEFAULTS, false);
  assert.equal('assemblyBackend' in RECOMMENDED_DEFAULTS, false);
});

test('loadSimAdvancedSettings returns RECOMMENDED_DEFAULTS when localStorage is empty', () => {
  global.localStorage.clear();
  const settings = loadSimAdvancedSettings();
  assert.equal(settings.solverBackend, RECOMMENDED_DEFAULTS.solverBackend);
});

test('saveSimAdvancedSettings persists metal backend selection', () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    solverBackend: 'metal',
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.solverBackend, 'metal');
});

test('saveSimAdvancedSettings persists bempp backend selection', () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    solverBackend: 'bempp',
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.solverBackend, 'bempp');
});

test('saveSimAdvancedSettings coerces invalid backend to auto', () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    solverBackend: 'invalid-backend',
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.solverBackend, 'auto');
});

test('loadSimAdvancedSettings resets stale schema versions to defaults', () => {
  global.localStorage.clear();
  global.localStorage.setItem(
    SETTINGS_KEY,
    JSON.stringify({
      schemaVersion: 5,
      simAdvanced: { solverBackend: 'bempp', useBurtonMiller: true },
    })
  );
  const loaded = loadSimAdvancedSettings();
  assert.deepEqual(loaded, { ...RECOMMENDED_DEFAULTS });
});

test('persisted settings no longer include useBurtonMiller', () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    solverBackend: 'metal',
    useBurtonMiller: true,
  });
  const persisted = JSON.parse(global.localStorage.getItem(SETTINGS_KEY));
  assert.equal('useBurtonMiller' in persisted.simAdvanced, false);
  assert.deepEqual(persisted.simAdvanced, { solverBackend: 'metal' });
});

test('resetSimAdvancedSettings restores defaults', () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    solverBackend: 'metal',
  });
  const reset = resetSimAdvancedSettings();
  assert.equal(reset.solverBackend, RECOMMENDED_DEFAULTS.solverBackend);
});

test('getCurrentSimAdvancedSettings returns default advanced settings', () => {
  global.localStorage.clear();
  loadSimAdvancedSettings();
  const current = getCurrentSimAdvancedSettings();
  assert.equal(current.solverBackend, RECOMMENDED_DEFAULTS.solverBackend);
});
