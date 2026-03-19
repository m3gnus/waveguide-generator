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

test("RECOMMENDED_DEFAULTS.bemPrecision is single", () => {
  assert.equal(RECOMMENDED_DEFAULTS.bemPrecision, "single");
});

test("RECOMMENDED_DEFAULTS has expected keys", () => {
  assert.equal(RECOMMENDED_DEFAULTS.enableWarmup, true);
  assert.equal(RECOMMENDED_DEFAULTS.bemPrecision, "single");
  assert.equal(RECOMMENDED_DEFAULTS.useBurtonMiller, true);
});

test("loadSimAdvancedSettings returns RECOMMENDED_DEFAULTS when localStorage is empty", () => {
  global.localStorage.clear();
  const settings = loadSimAdvancedSettings();
  assert.equal(settings.bemPrecision, "single");
  assert.equal(settings.enableWarmup, true);
  assert.equal(settings.useBurtonMiller, true);
});

test("saveSimAdvancedSettings persists bemPrecision single", () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    bemPrecision: "single",
    enableWarmup: true,
    useBurtonMiller: true,
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.bemPrecision, "single");
});

test("saveSimAdvancedSettings persists bemPrecision double", () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    bemPrecision: "double",
    enableWarmup: false,
    useBurtonMiller: false,
  });
  const loaded = loadSimAdvancedSettings();
  assert.equal(loaded.bemPrecision, "double");
  assert.equal(loaded.enableWarmup, false);
  assert.equal(loaded.useBurtonMiller, false);
});

test("resetSimAdvancedSettings returns single precision", () => {
  global.localStorage.clear();
  saveSimAdvancedSettings({
    bemPrecision: "double",
    enableWarmup: false,
    useBurtonMiller: false,
  });
  const reset = resetSimAdvancedSettings();
  assert.equal(reset.bemPrecision, "single");
});

test("getCurrentSimAdvancedSettings returns single precision default", () => {
  global.localStorage.clear();
  const current = getCurrentSimAdvancedSettings();
  assert.equal(current.bemPrecision, "single");
});
