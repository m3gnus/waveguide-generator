import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildCanonicalPolarBlocks,
  readPolarUiSettings,
  syncPolarControlsFromBlocks
} from '../src/ui/simulation/polarSettings.js';

function makeDoc(overrides = {}) {
  const elements = {
    'polar-angle-start': { value: '0' },
    'polar-angle-end': { value: '180' },
    'polar-angle-step': { value: '5' },
    'polar-distance': { value: '2' },
    'polar-norm-angle': { value: '5' },
    'polar-inclination': { value: '45', disabled: false },
    'polar-axis-horizontal': { checked: true },
    'polar-axis-vertical': { checked: true },
    'polar-axis-diagonal': { checked: true }
  };

  Object.entries(overrides).forEach(([id, patch]) => {
    elements[id] = { ...(elements[id] || {}), ...patch };
  });

  return {
    getElementById(id) {
      return elements[id] || null;
    },
    _elements: elements
  };
}

test('readPolarUiSettings returns default all-axis selection', () => {
  const doc = makeDoc();
  const settings = readPolarUiSettings(doc);

  assert.equal(settings.ok, true);
  assert.deepEqual(settings.enabledAxes, ['horizontal', 'vertical', 'diagonal']);
  assert.equal(settings.diagonalAngle, 45);
  assert.deepEqual(settings.angleRangeArray, [0, 180, 37]);
});

test('syncPolarControlsFromBlocks maps 90 degrees to vertical axis', () => {
  const doc = makeDoc();
  syncPolarControlsFromBlocks({
    'ABEC.Polars:SPL_V': {
      _items: {
        MapAngleRange: '0,180,37',
        Inclination: '90'
      }
    }
  }, doc);

  assert.equal(doc._elements['polar-axis-horizontal'].checked, false);
  assert.equal(doc._elements['polar-axis-vertical'].checked, true);
  assert.equal(doc._elements['polar-axis-diagonal'].checked, false);
});

test('syncPolarControlsFromBlocks maps non-cardinal inclination to diagonal and preserves angle', () => {
  const doc = makeDoc();
  syncPolarControlsFromBlocks({
    'ABEC.Polars:SPL_D': {
      _items: {
        MapAngleRange: '0,180,37',
        Inclination: '35'
      }
    }
  }, doc);

  assert.equal(doc._elements['polar-axis-horizontal'].checked, false);
  assert.equal(doc._elements['polar-axis-vertical'].checked, false);
  assert.equal(doc._elements['polar-axis-diagonal'].checked, true);
  assert.equal(doc._elements['polar-inclination'].value, '35');
});

test('syncPolarControlsFromBlocks maps 270 degrees to vertical axis', () => {
  const doc = makeDoc();
  syncPolarControlsFromBlocks({
    'ABEC.Polars:SPL_V': {
      _items: {
        MapAngleRange: '0,180,37',
        Inclination: '270'
      }
    }
  }, doc);

  assert.equal(doc._elements['polar-axis-horizontal'].checked, false);
  assert.equal(doc._elements['polar-axis-vertical'].checked, true);
  assert.equal(doc._elements['polar-axis-diagonal'].checked, false);
});

test('readPolarUiSettings rejects empty axis selection', () => {
  const doc = makeDoc({
    'polar-axis-horizontal': { checked: false },
    'polar-axis-vertical': { checked: false },
    'polar-axis-diagonal': { checked: false }
  });
  const settings = readPolarUiSettings(doc);

  assert.equal(settings.ok, false);
  assert.match(settings.validationError, /at least one polar axis/i);
});

test('buildCanonicalPolarBlocks emits only selected canonical axes with correct inclinations', () => {
  const blocks = buildCanonicalPolarBlocks({
    enabledAxes: ['horizontal', 'diagonal'],
    polarRange: '0,180,37',
    distance: 2,
    normAngle: 5,
    diagonalAngle: 33
  });

  assert.deepEqual(Object.keys(blocks).sort(), ['ABEC.Polars:SPL_D', 'ABEC.Polars:SPL_H']);
  assert.equal(blocks['ABEC.Polars:SPL_H']._items.MapAngleRange, '0,180,37');
  assert.equal(blocks['ABEC.Polars:SPL_D']._items.Inclination, '33');
});
