import test from 'node:test';
import assert from 'node:assert/strict';

import {
  RECOMMENDED_DEFAULTS,
  getAutoExportOnComplete,
  getSelectedExportFormats,
  getTaskListMinRatingFilter,
  getTaskListSortPreference,
  loadSimulationManagementSettings,
  saveSimulationManagementSettings
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
    }
  };
}

test('simulation management settings load defaults and persist selected formats', () => {
  const originalLocalStorage = global.localStorage;
  global.localStorage = createStorage();

  try {
    const defaults = loadSimulationManagementSettings();
    assert.equal(defaults.autoExportOnComplete, RECOMMENDED_DEFAULTS.autoExportOnComplete);
    assert.deepEqual(defaults.selectedFormats, RECOMMENDED_DEFAULTS.selectedFormats);

    const saved = saveSimulationManagementSettings({
      autoExportOnComplete: false,
      selectedFormats: ['csv', 'json', 'csv'],
      defaultSort: 'completed_desc',
      minRatingFilter: 0
    });

    assert.equal(saved.autoExportOnComplete, false);
    assert.deepEqual(saved.selectedFormats, ['csv', 'json']);

    const reloaded = loadSimulationManagementSettings();
    assert.equal(reloaded.autoExportOnComplete, false);
    assert.deepEqual(reloaded.selectedFormats, ['csv', 'json']);
  } finally {
    global.localStorage = originalLocalStorage;
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
          }
        },
        {
          checked: false,
          getAttribute(name) {
            return name === 'data-sim-management-format' ? 'json' : null;
          }
        }
      ];
    }
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
