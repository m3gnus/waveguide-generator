import test from 'node:test';
import assert from 'node:assert/strict';

import {
  RECOMMENDED_DEFAULTS,
  getAutoExportOnComplete,
  getSelectedExportFormats,
  getTaskListMinRatingFilter,
  getTaskListSortPreference,
  loadSimulationManagementSettings,
  saveSimulationManagementSettings,
} from '../src/ui/settings/simulationManagementSettings.js';

function createStorage() {
  const map = new Map();
  return {
    getItem(key) {
      return map.has(key) ? map.get(key) : null;
    },
    setItem(key, value) {
      map.set(key, String(value));
    },
    removeItem(key) {
      map.delete(key);
    },
    clear() {
      map.clear();
    },
  };
}

test('simulation management settings load defaults and persist selected formats', () => {
  const originalLocalStorage = global.localStorage;
  global.localStorage = createStorage();

  try {
    const defaults = loadSimulationManagementSettings();
    assert.equal(defaults.autoExportOnComplete, RECOMMENDED_DEFAULTS.autoExportOnComplete);
    assert.equal(defaults.downloadSimMesh, RECOMMENDED_DEFAULTS.downloadSimMesh);
    assert.deepEqual(defaults.selectedFormats, RECOMMENDED_DEFAULTS.selectedFormats);

    const saved = saveSimulationManagementSettings({
      autoExportOnComplete: false,
      downloadSimMesh: true,
      selectedFormats: ['csv', 'json', 'csv'],
      defaultSort: 'completed_desc',
      minRatingFilter: 0,
    });

    assert.equal(saved.autoExportOnComplete, false);
    assert.equal(saved.downloadSimMesh, true);
    assert.deepEqual(saved.selectedFormats, ['csv', 'json']);

    const reloaded = loadSimulationManagementSettings();
    assert.equal(reloaded.autoExportOnComplete, false);
    assert.equal(reloaded.downloadSimMesh, true);
    assert.deepEqual(reloaded.selectedFormats, ['csv', 'json']);

    const noneSelected = saveSimulationManagementSettings({
      autoExportOnComplete: false,
      downloadSimMesh: false,
      selectedFormats: [],
      defaultSort: 'completed_desc',
      minRatingFilter: 0,
    });

    assert.deepEqual(noneSelected.selectedFormats, []);
  } finally {
    global.localStorage = originalLocalStorage;
  }
});

test('simulation management settings migrate legacy default format selection to none', () => {
  const originalLocalStorage = global.localStorage;
  global.localStorage = createStorage();
  global.localStorage.setItem(
    'waveguide-simulation-management-settings',
    JSON.stringify({
      schemaVersion: 1,
      simulationManagement: {
        autoExportOnComplete: false,
        selectedFormats: [
          'png',
          'csv',
          'json',
          'txt',
          'polar_csv',
          'impedance_csv',
          'vacs',
          'stl',
          'fusion_csv',
        ],
        defaultSort: 'rating_desc',
        minRatingFilter: 2,
      },
    })
  );

  try {
    const migrated = loadSimulationManagementSettings();
    assert.deepEqual(migrated.selectedFormats, []);
    assert.equal(migrated.defaultSort, 'rating_desc');
    assert.equal(migrated.minRatingFilter, 2);
  } finally {
    global.localStorage = originalLocalStorage;
  }
});

test('simulation management DOM getter preserves an empty live format selection', () => {
  const originalDocument = global.document;
  global.document = {
    querySelectorAll(selector) {
      if (selector !== 'input[data-sim-management-format]') {
        return [];
      }
      return [
        {
          checked: false,
          getAttribute(name) {
            return name === 'data-sim-management-format' ? 'csv' : null;
          },
        },
        {
          checked: false,
          getAttribute(name) {
            return name === 'data-sim-management-format' ? 'json' : null;
          },
        },
      ];
    },
  };

  try {
    assert.deepEqual(getSelectedExportFormats(), []);
  } finally {
    global.document = originalDocument;
  }
});

test('simulation management DOM getters prefer live modal controls', () => {
  const originalDocument = global.document;
  global.document = {
    getElementById(id) {
      if (id === 'simmanage-auto-export') {
        return { checked: false };
      }
      if (id === 'simmanage-default-sort') {
        return { value: 'label_asc' };
      }
      if (id === 'simmanage-min-rating') {
        return { value: '4' };
      }
      if (id === 'simulation-jobs-sort') {
        return { value: 'rating_desc' };
      }
      if (id === 'simulation-jobs-min-rating') {
        return { value: '3' };
      }
      return null;
    },
    querySelectorAll(selector) {
      if (selector !== 'input[data-sim-management-format]') {
        return [];
      }
      return [
        {
          checked: true,
          getAttribute(name) {
            return name === 'data-sim-management-format' ? 'csv' : null;
          },
        },
        {
          checked: false,
          getAttribute(name) {
            return name === 'data-sim-management-format' ? 'json' : null;
          },
        },
      ];
    },
  };

  try {
    assert.equal(getAutoExportOnComplete(), false);
    assert.deepEqual(getSelectedExportFormats(), ['csv']);
    assert.equal(getTaskListSortPreference(), 'label_asc');
    assert.equal(getTaskListMinRatingFilter(), 4);
  } finally {
    global.document = originalDocument;
  }
});
