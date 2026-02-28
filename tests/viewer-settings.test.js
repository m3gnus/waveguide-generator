import test, { beforeEach, describe } from 'node:test';
import assert from 'node:assert/strict';

import {
  RECOMMENDED_DEFAULTS,
  loadViewerSettings,
  saveViewerSettings,
  applyViewerSettingsToControls,
  setInvertWheelZoom,
  resetAllViewerSettings,
} from '../src/ui/settings/viewerSettings.js';

const store = {};

global.localStorage = {
  getItem: (key) => store[key] ?? null,
  setItem: (key, value) => { store[key] = value; },
  removeItem: (key) => { delete store[key]; },
  clear: () => {
    Object.keys(store).forEach((key) => delete store[key]);
  },
};

function makeControlsStub() {
  return {
    rotateSpeed: undefined,
    zoomSpeed: undefined,
    panSpeed: undefined,
    enableDamping: undefined,
    dampingFactor: undefined,
    _domElementKeyEvents: null,
    listenCalls: 0,
    stopCalls: 0,
    listenToKeyEvents(el) {
      this.listenCalls += 1;
      this._domElementKeyEvents = el;
    },
    stopListenToKeyEvents() {
      this.stopCalls += 1;
      this._domElementKeyEvents = null;
    },
  };
}

function makeWheelStub() {
  const activeCaptureWheelListeners = new Set();
  const added = [];
  const removed = [];
  return {
    activeCaptureWheelListeners,
    added,
    removed,
    addEventListener(type, fn, opts) {
      added.push({ type, fn, opts });
      if (type === 'wheel' && opts && opts.capture === true) {
        activeCaptureWheelListeners.add(fn);
      }
    },
    removeEventListener(type, fn, opts) {
      removed.push({ type, fn, opts });
      if (type === 'wheel' && opts && opts.capture === true) {
        activeCaptureWheelListeners.delete(fn);
      }
    },
  };
}

beforeEach(() => {
  global.localStorage.clear();
  global.window = {};
  resetAllViewerSettings();
  setInvertWheelZoom(makeWheelStub(), false);
});

describe('loadViewerSettings', () => {
  test('returns RECOMMENDED_DEFAULTS when localStorage is empty', () => {
    global.localStorage.clear();
    assert.deepEqual(loadViewerSettings(), RECOMMENDED_DEFAULTS);
  });

  test('returns RECOMMENDED_DEFAULTS when key is missing from localStorage', () => {
    global.localStorage.setItem('other-key', 'x');
    assert.deepEqual(loadViewerSettings(), RECOMMENDED_DEFAULTS);
  });

  test('returns RECOMMENDED_DEFAULTS when schema version mismatches', () => {
    global.localStorage.setItem('waveguide-app-settings', JSON.stringify({
      schemaVersion: 99,
      viewer: { rotateSpeed: 2.0 },
    }));
    assert.deepEqual(loadViewerSettings(), RECOMMENDED_DEFAULTS);
  });

  test('returns RECOMMENDED_DEFAULTS on malformed JSON', () => {
    global.localStorage.setItem('waveguide-app-settings', 'not-json');
    assert.deepEqual(loadViewerSettings(), RECOMMENDED_DEFAULTS);
  });

  test('tolerant merge: loads valid known fields from stored data', () => {
    global.localStorage.setItem('waveguide-app-settings', JSON.stringify({
      schemaVersion: 1,
      viewer: { rotateSpeed: 2.5, zoomSpeed: 3.0 },
    }));
    const result = loadViewerSettings();
    assert.equal(result.rotateSpeed, 2.5);
    assert.equal(result.zoomSpeed, 3.0);
    assert.equal(result.panSpeed, RECOMMENDED_DEFAULTS.panSpeed);
  });

  test('tolerant merge: unknown fields from stored data are discarded', () => {
    global.localStorage.setItem('waveguide-app-settings', JSON.stringify({
      schemaVersion: 1,
      viewer: { rotateSpeed: 1.5, unknownField: 'hello' },
    }));
    const result = loadViewerSettings();
    assert.equal(Object.hasOwn(result, 'unknownField'), false);
    assert.equal(result.rotateSpeed, 1.5);
  });

  test('tolerant merge: wrong-type fields fall back to default', () => {
    global.localStorage.setItem('waveguide-app-settings', JSON.stringify({
      schemaVersion: 1,
      viewer: { rotateSpeed: 'fast' },
    }));
    const result = loadViewerSettings();
    assert.equal(result.rotateSpeed, RECOMMENDED_DEFAULTS.rotateSpeed);
  });
});

describe('saveViewerSettings', () => {
  test('writes versioned JSON to localStorage', () => {
    saveViewerSettings({ ...RECOMMENDED_DEFAULTS, rotateSpeed: 2.0 });
    const parsed = JSON.parse(global.localStorage.getItem('waveguide-app-settings'));
    assert.equal(parsed.schemaVersion, 1);
    assert.equal(parsed.viewer.rotateSpeed, 2.0);
  });
});

describe('applyViewerSettingsToControls', () => {
  test('sets rotateSpeed, zoomSpeed, panSpeed, enableDamping, dampingFactor on controls', () => {
    const controls = makeControlsStub();
    applyViewerSettingsToControls(controls, {
      ...RECOMMENDED_DEFAULTS,
      rotateSpeed: 2.5,
      dampingEnabled: false,
    });
    assert.equal(controls.rotateSpeed, 2.5);
    assert.equal(controls.zoomSpeed, RECOMMENDED_DEFAULTS.zoomSpeed);
    assert.equal(controls.panSpeed, RECOMMENDED_DEFAULTS.panSpeed);
    assert.equal(controls.enableDamping, false);
    assert.equal(controls.dampingFactor, RECOMMENDED_DEFAULTS.dampingFactor);
  });

  test('calls listenToKeyEvents when keyboardPanEnabled is true', () => {
    const controls = makeControlsStub();
    applyViewerSettingsToControls(controls, {
      ...RECOMMENDED_DEFAULTS,
      keyboardPanEnabled: true,
    });
    assert.equal(controls.listenCalls, 1);
    assert.equal(controls._domElementKeyEvents, global.window);
  });

  test('guards against null controls (no throw)', () => {
    assert.doesNotThrow(() => {
      applyViewerSettingsToControls(null, RECOMMENDED_DEFAULTS);
    });
  });

  test('stopListenToKeyEvents guard: does not throw when _domElementKeyEvents is null', () => {
    const controls = makeControlsStub();
    controls._domElementKeyEvents = null;
    assert.doesNotThrow(() => {
      applyViewerSettingsToControls(controls, {
        ...RECOMMENDED_DEFAULTS,
        keyboardPanEnabled: false,
      });
    });
    assert.equal(controls.stopCalls, 0);
  });
});

describe('resetAllViewerSettings', () => {
  test('restores all fields to RECOMMENDED_DEFAULTS', () => {
    saveViewerSettings({
      ...RECOMMENDED_DEFAULTS,
      rotateSpeed: 3.0,
      zoomSpeed: 4.0,
    });
    const result = resetAllViewerSettings();
    assert.deepEqual(result, RECOMMENDED_DEFAULTS);
    const parsed = JSON.parse(global.localStorage.getItem('waveguide-app-settings'));
    assert.deepEqual(parsed.viewer, RECOMMENDED_DEFAULTS);
  });
});

describe('setInvertWheelZoom', () => {
  test('registers capture wheel listener when enabled', () => {
    const domEl = makeWheelStub();
    setInvertWheelZoom(domEl, true);
    assert.ok(
      domEl.added.some((entry) => (
        entry.type === 'wheel' &&
        entry.opts &&
        entry.opts.capture === true
      ))
    );
    setInvertWheelZoom(domEl, false);
  });

  test('removes previous listener when called twice', () => {
    const domEl = makeWheelStub();
    setInvertWheelZoom(domEl, true);
    setInvertWheelZoom(domEl, true);
    assert.ok(
      domEl.removed.some((entry) => (
        entry.type === 'wheel' &&
        entry.opts &&
        entry.opts.capture === true
      ))
    );
    assert.equal(domEl.activeCaptureWheelListeners.size, 1);
    setInvertWheelZoom(domEl, false);
  });
});
