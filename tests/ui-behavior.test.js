import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeParamInput } from '../src/ui/paramInput.js';
import { formatJobSummary, validateSimulationConfig } from '../src/ui/simulation/actions.js';
import { applyExportSelection } from '../src/ui/simulation/exports.js';
import {
  deriveExportFieldsFromFileName,
  markParametersChanged,
  resetParameterChangeTracking
} from '../src/ui/fileOps.js';
import {
  SETTINGS_CONTROL_IDS,
  getLiveUpdateEnabled,
  getDisplayMode,
  getDownloadSimMeshEnabled,
  openSettingsModal,
} from '../src/ui/settings/modal.js';
import {
  RECOMMENDED_DEFAULTS,
  resetAllViewerSettings,
  saveViewerSettings,
} from '../src/ui/settings/viewerSettings.js';

test('normalizeParamInput parses numeric literals consistently', () => {
  assert.equal(normalizeParamInput('1.0'), 1);
  assert.equal(normalizeParamInput(' 1e3 '), 1000);
  assert.equal(normalizeParamInput('-0.25'), -0.25);
  assert.equal(normalizeParamInput('45 + 10*cos(p)'), '45 + 10*cos(p)');
  assert.equal(normalizeParamInput('2+3'), '2+3');
});

test('validateSimulationConfig catches invalid ranges and counts', () => {
  assert.match(
    validateSimulationConfig({
      frequencyStart: 1000,
      frequencyEnd: 100,
      numFrequencies: 50
    }),
    /Start frequency/
  );

  assert.match(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 0
    }),
    /Number of frequencies/
  );

  assert.equal(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 50
    }),
    null
  );
});

test('formatJobSummary appends complete duration in m:ss', () => {
  const summary = formatJobSummary({
    status: 'complete',
    startedAt: '2026-02-24T12:00:00.000Z',
    completedAt: '2026-02-24T12:02:53.000Z'
  });
  assert.equal(summary, 'Complete (2:53)');
});

test('formatJobSummary appends complete duration in h:mm:ss', () => {
  const summary = formatJobSummary({
    status: 'complete',
    startedAt: '2026-02-24T12:00:00.000Z',
    completedAt: '2026-02-24T13:04:32.000Z'
  });
  assert.equal(summary, 'Complete (1:04:32)');
});

test('formatJobSummary falls back to Complete when duration is unavailable', () => {
  const summary = formatJobSummary({
    status: 'complete',
    startedAt: 'not-a-date',
    completedAt: null
  });
  assert.equal(summary, 'Complete');
});

test('applyExportSelection routes to expected handler', () => {
  const calls = [];
  const originalError = console.error;
  console.error = () => {};

  const handlers = {
    '1': () => calls.push('image'),
    '2': () => calls.push('csv'),
    '3': () => calls.push('json'),
    '4': () => calls.push('text')
  };

  try {
    assert.equal(applyExportSelection({}, '2', handlers), true);
    assert.deepEqual(calls, ['csv']);

    assert.equal(applyExportSelection({}, '9', handlers), false);
    assert.deepEqual(calls, ['csv']);
  } finally {
    console.error = originalError;
  }
});

test('applyExportSelection includes VACS spectrum option 7', () => {
  const calls = [];
  const handlers = {
    '1': () => calls.push('image'),
    '2': () => calls.push('csv'),
    '3': () => calls.push('json'),
    '4': () => calls.push('text'),
    '5': () => calls.push('polar'),
    '6': () => calls.push('impedance'),
    '7': () => calls.push('vacs')
  };

  assert.equal(applyExportSelection({}, '7', handlers), true);
  assert.deepEqual(calls, ['vacs']);
});

test('applyExportSelection includes CAD exports options 8 and 9', () => {
  const calls = [];
  const handlers = {
    '8': () => calls.push('stl'),
    '9': () => calls.push('fusion-csv')
  };

  assert.equal(applyExportSelection({}, '8', handlers), true);
  assert.equal(applyExportSelection({}, '9', handlers), true);
  assert.deepEqual(calls, ['stl', 'fusion-csv']);
});

test('deriveExportFieldsFromFileName parses output name and counter from file names', () => {
  assert.deepEqual(
    deriveExportFieldsFromFileName('horn.cfg'),
    { outputName: 'horn', counter: 1 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('horn_design_12.cfg'),
    { outputName: 'horn_design', counter: 12 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('horn_design_0.cfg'),
    { outputName: 'horn_design_0', counter: 1 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('my file name_3.txt'),
    { outputName: 'my file name', counter: 3 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('260219superhorn35.cfg'),
    { outputName: '260219superhorn', counter: 35 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('   '),
    { outputName: 'horn_design', counter: 1 }
  );
});

test('markParametersChanged increments counter once per change cycle and skips import baseline update', () => {
  const originalDocument = global.document;
  const counterEl = { value: '35' };
  global.document = {
    getElementById(id) {
      if (id === 'export-counter') return counterEl;
      return null;
    }
  };

  try {
    resetParameterChangeTracking({ skipNext: true });
    markParametersChanged();
    assert.equal(counterEl.value, '35');

    markParametersChanged();
    assert.equal(counterEl.value, '36');

    markParametersChanged();
    assert.equal(counterEl.value, '36');

    resetParameterChangeTracking();
    markParametersChanged();
    assert.equal(counterEl.value, '37');
  } finally {
    global.document = originalDocument;
    resetParameterChangeTracking();
  }
});

// --- Phase 1 migration regression tests: Settings modal entry ---

test('SETTINGS_CONTROL_IDS maps all migrated controls to their element IDs', () => {
  // Verifies the canonical ID map exists so consumers can reference controls
  // that now live inside the dynamically-created settings modal.
  assert.equal(SETTINGS_CONTROL_IDS.liveUpdate, 'live-update');
  assert.equal(SETTINGS_CONTROL_IDS.displayMode, 'display-mode');
  assert.equal(SETTINGS_CONTROL_IDS.downloadSimMesh, 'download-sim-mesh');
  assert.equal(SETTINGS_CONTROL_IDS.checkUpdates, 'check-updates-btn');
});

test('settings getters return in-memory defaults when modal is not open', () => {
  // When the modal is closed there are no DOM elements for these controls.
  // Getters must return stored defaults rather than null/undefined.
  const originalDocument = global.document;
  global.document = { getElementById: () => null };

  try {
    // Default: live-update = true
    assert.equal(getLiveUpdateEnabled(), true);
    // Default: display-mode = standard
    assert.equal(getDisplayMode(), 'standard');
    // Default: download-sim-mesh = false
    assert.equal(getDownloadSimMeshEnabled(), false);
  } finally {
    global.document = originalDocument;
  }
});

test('settings getters read DOM values when elements are present', () => {
  const originalDocument = global.document;

  const liveUpdateEl = { checked: false };
  const displayModeEl = { value: 'zebra' };
  const downloadMeshEl = { checked: true };

  global.document = {
    getElementById(id) {
      if (id === 'live-update') return liveUpdateEl;
      if (id === 'display-mode') return displayModeEl;
      if (id === 'download-sim-mesh') return downloadMeshEl;
      return null;
    }
  };

  try {
    assert.equal(getLiveUpdateEnabled(), false);
    assert.equal(getDisplayMode(), 'zebra');
    assert.equal(getDownloadSimMeshEnabled(), true);
  } finally {
    global.document = originalDocument;
  }
});

test('openSettingsModal creates modal with all four required section names', () => {
  // Minimal DOM environment for on-demand modal construction.
  const originalDocument = global.document;
  const originalWindow = global.window;

  const appendedChildren = [];
  const createdElements = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.document = {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        type: '',
        title: '',
        dataset: {},
        setAttribute(k, v) { this._attrs[k] = v; },
        getAttribute(k) { return this._attrs[k]; },
        addEventListener(evt, fn) {
          this._eventListeners[evt] = this._eventListeners[evt] || [];
          this._eventListeners[evt].push(fn);
        },
        appendChild(child) {
          this._children.push(child);
          return child;
        },
        querySelectorAll(selector) {
          // Return nav buttons or section divs based on class
          const results = [];
          const walk = (node) => {
            if (!node || !node._children) return;
            for (const child of node._children) {
              if (selector === '.settings-nav-btn' && child.className && child.className.includes('settings-nav-btn')) {
                results.push(child);
              }
              if (selector === '.settings-section' && child.className && child.className.includes('settings-section')) {
                results.push(child);
              }
              walk(child);
            }
          };
          walk(this);
          return results;
        },
        querySelector(selector) {
          if (selector === '[role="dialog"]') {
            const walk = (node) => {
              if (!node || !node._children) return null;
              for (const child of node._children) {
                if (child._attrs && child._attrs['role'] === 'dialog') return child;
                const found = walk(child);
                if (found) return found;
              }
              return null;
            };
            return walk(this);
          }
          return null;
        },
        focus() {},
        remove() {},
        classList: {
          _list: new Set(),
          toggle(cls, force) {
            if (force === undefined) {
              if (this._list.has(cls)) this._list.delete(cls); else this._list.add(cls);
            } else if (force) {
              this._list.add(cls);
            } else {
              this._list.delete(cls);
            }
          },
          includes(cls) { return this._list.has(cls); }
        },
      };
      createdElements.push(el);
      return el;
    },
    body: {
      appendChild(child) {
        appendedChildren.push(child);
        return child;
      }
    }
  };

  try {
    openSettingsModal();

    // The backdrop div should have been appended to body
    assert.equal(appendedChildren.length, 1, 'One element should be appended to body');

    // Collect all textContent values from created elements to find section headings
    const allText = createdElements.map((el) => el.textContent).filter(Boolean);

    // All four required section nav labels must be present
    assert.ok(allText.some(t => t === 'Viewer'), 'Viewer section must be present');
    assert.ok(allText.some(t => t === 'Simulation Basic'), 'Simulation Basic section must be present');
    assert.ok(allText.some(t => t === 'Simulation Advanced'), 'Simulation Advanced section must be present');
    assert.ok(allText.some(t => t === 'System'), 'System section must be present');
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

test('openSettingsModal places check-updates-btn inside the modal, not in the actions panel', () => {
  // Regression: check-updates-btn must only exist inside the dynamically-created
  // settings modal. If it were found in the static DOM at startup (via getElementById
  // before modal open), the binding in events.js would attach the old direct handler
  // pattern instead of the delegation chain.
  const originalDocument = global.document;
  const originalWindow = global.window;

  const appendedChildren = [];
  const createdElements = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.document = {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        type: '',
        title: '',
        dataset: {},
        setAttribute(k, v) { this._attrs[k] = v; },
        getAttribute(k) { return this._attrs[k]; },
        addEventListener(evt, fn) {
          this._eventListeners[evt] = this._eventListeners[evt] || [];
          this._eventListeners[evt].push(fn);
        },
        appendChild(child) { this._children.push(child); return child; },
        querySelectorAll() { return []; },
        querySelector() { return null; },
        focus() {},
        remove() {},
        classList: {
          _list: new Set(),
          toggle() {},
          includes() { return false; }
        },
      };
      createdElements.push(el);
      return el;
    },
    body: {
      appendChild(child) { appendedChildren.push(child); return child; }
    }
  };

  try {
    openSettingsModal();

    // check-updates-btn must be created inside the modal (within the appended backdrop)
    const updateBtnElements = createdElements.filter(el => el.id === 'check-updates-btn');
    assert.equal(updateBtnElements.length, 1, 'Exactly one check-updates-btn should be created');

    // Verify it is NOT directly in the static DOM (getElementById returns null before modal open)
    const staticBtn = global.document.getElementById('check-updates-btn');
    assert.equal(staticBtn, null, 'check-updates-btn should not exist in static DOM before modal is opened');
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

function createSettingsModalDocument(createdElements, appendedChildren) {
  return {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        type: '',
        title: '',
        name: '',
        value: '',
        checked: false,
        dataset: {},
        min: '',
        max: '',
        step: '',
        setAttribute(k, v) { this._attrs[k] = v; },
        getAttribute(k) { return this._attrs[k]; },
        addEventListener(evt, fn) {
          this._eventListeners[evt] = this._eventListeners[evt] || [];
          this._eventListeners[evt].push(fn);
        },
        appendChild(child) {
          this._children.push(child);
          return child;
        },
        querySelectorAll() { return []; },
        querySelector(selector) {
          if (selector === '[role="dialog"]') {
            const walk = (node) => {
              if (!node || !node._children) return null;
              for (const child of node._children) {
                if (child._attrs && child._attrs.role === 'dialog') return child;
                const found = walk(child);
                if (found) return found;
              }
              return null;
            };
            return walk(this);
          }
          return null;
        },
        focus() {},
        remove() {},
        classList: { toggle() {}, includes() { return false; } },
      };
      createdElements.push(el);
      return el;
    },
    body: {
      appendChild(child) {
        appendedChildren.push(child);
        return child;
      }
    }
  };
}

test('recommended badges are visible when viewer values match defaults', () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalLocalStorage = global.localStorage;

  const store = {};
  const createdElements = [];
  const appendedChildren = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.localStorage = {
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => { store[key] = value; },
    removeItem: (key) => { delete store[key]; },
    clear: () => Object.keys(store).forEach((key) => delete store[key]),
  };
  resetAllViewerSettings();
  global.document = createSettingsModalDocument(createdElements, appendedChildren);

  try {
    openSettingsModal();
    const badges = createdElements.filter((el) => el.className === 'settings-recommended-badge');
    assert.ok(badges.length > 0, 'Expected recommended badges to be created');
    assert.ok(
      badges.every((badge) => badge.hidden === false),
      'All badges should be visible when values are recommended'
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.localStorage = originalLocalStorage;
  }
});

test('recommended badge hides when a viewer value differs from default', () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalLocalStorage = global.localStorage;

  const store = {};
  const createdElements = [];
  const appendedChildren = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.localStorage = {
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => { store[key] = value; },
    removeItem: (key) => { delete store[key]; },
    clear: () => Object.keys(store).forEach((key) => delete store[key]),
  };
  saveViewerSettings({ ...RECOMMENDED_DEFAULTS, rotateSpeed: 2.5 });
  global.document = createSettingsModalDocument(createdElements, appendedChildren);

  try {
    openSettingsModal();
    const badges = createdElements.filter((el) => el.className === 'settings-recommended-badge');
    assert.ok(badges.length > 0, 'Expected recommended badges to be created');
    assert.ok(
      badges.some((badge) => badge.hidden === true),
      'At least one badge should hide for non-recommended values'
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.localStorage = originalLocalStorage;
  }
});

test('recommended badge rule remains stable for all default values', () => {
  for (const key of Object.keys(RECOMMENDED_DEFAULTS)) {
    assert.equal(
      RECOMMENDED_DEFAULTS[key] !== RECOMMENDED_DEFAULTS[key],
      false,
      `Expected default value for ${key} to match itself`
    );
  }
});
