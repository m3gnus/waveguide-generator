import test from "node:test";
import assert from "node:assert/strict";

import {
  RECOMMENDED_DEFAULTS,
  loadSimAdvancedSettings,
  saveSimAdvancedSettings,
  resetSimAdvancedSettings,
  getCurrentSimAdvancedSettings,
} from "../src/ui/settings/simAdvancedSettings.js";

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

test("RECOMMENDED_DEFAULTS no longer exposes bemPrecision", () => {
  assert.equal("bemPrecision" in RECOMMENDED_DEFAULTS, false);
});

test("RECOMMENDED_DEFAULTS has expected keys", () => {
  assert.equal(RECOMMENDED_DEFAULTS.useBurtonMiller, true);
  assert.equal("enableWarmup" in RECOMMENDED_DEFAULTS, false);
  assert.equal("bemPrecision" in RECOMMENDED_DEFAULTS, false);
});

test("loadSimAdvancedSettings returns RECOMMENDED_DEFAULTS when localStorage is empty", () => {
  global.localStorage.clear();
  const settings = loadSimAdvancedSettings();
  assert.equal(settings.useBurtonMiller, true);
});

test("saveSimAdvancedSettings persists useBurtonMiller true", () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    useBurtonMiller: true,
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.useBurtonMiller, true);
});

test("saveSimAdvancedSettings persists useBurtonMiller false", () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    useBurtonMiller: false,
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.useBurtonMiller, false);
});

test("resetSimAdvancedSettings restores defaults", () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    useBurtonMiller: false,
  });
  const reset = resetSimAdvancedSettings();
  assert.equal(reset.useBurtonMiller, true);
});

test("getCurrentSimAdvancedSettings returns default advanced settings", () => {
  global.localStorage.clear();
  const current = getCurrentSimAdvancedSettings();
  assert.equal(current.useBurtonMiller, true);
});
