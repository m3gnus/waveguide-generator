import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildPolarStatePatchForControl,
  buildPolarStatePatchFromConfig,
  buildCanonicalPolarBlocks,
  readPolarUiSettings,
  readPolarStateSettings,
  renderPolarSettingsSection,
  syncPolarControlsFromBlocks,
  syncPolarControlsFromState
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

class FakeRenderElement {
  constructor(tagName, ownerDocument) {
    this.tagName = String(tagName || '').toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.attributes = {};
    this.className = '';
    this.title = '';
    this.textContent = '';
    this.value = '';
    this.type = '';
    this.min = '';
    this.max = '';
    this.step = '';
    this.checked = false;
    this.htmlFor = '';
    this.parentNode = null;
    this._id = '';
    this.classList = {
      add: (...tokens) => {
        const existing = new Set(this.className.split(/\s+/).filter(Boolean));
        tokens.forEach((token) => existing.add(token));
        this.className = Array.from(existing).join(' ');
      }
    };
  }

  set id(value) {
    this._id = String(value || '');
    if (this._id) {
      this.ownerDocument.elementsById.set(this._id, this);
    }
  }

  get id() {
    return this._id;
  }

  set innerHTML(_value) {
    this.children = [];
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    if (child.id) {
      this.ownerDocument.elementsById.set(child.id, child);
    }
    return child;
  }

  setAttribute(name, value) {
    this.attributes[name] = String(value);
  }
}

class FakeRenderDocument {
  constructor() {
    this.elementsById = new Map();
    this.body = new FakeRenderElement('body', this);
  }

  createElement(tagName) {
    return new FakeRenderElement(tagName, this);
  }

  getElementById(id) {
    return this.elementsById.get(id) || null;
  }
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

test('readPolarStateSettings derives state-backed settings from canonical blocks', () => {
  const settings = readPolarStateSettings({
    _blocks: {
      'ABEC.Polars:SPL_V': {
        _items: {
          MapAngleRange: '10,190,19',
          NormAngle: '7',
          Distance: '3',
          Inclination: '90'
        }
      }
    }
  });

  assert.equal(settings.ok, true);
  assert.deepEqual(settings.angleRangeArray, [10, 190, 19]);
  assert.equal(settings.normAngle, 7);
  assert.equal(settings.distance, 3);
  assert.deepEqual(settings.enabledAxes, ['vertical']);
});

test('buildPolarStatePatchForControl persists explicit state fields and synced canonical blocks', () => {
  const doc = makeDoc({
    'polar-angle-start': { value: '15' }
  });

  const patch = buildPolarStatePatchForControl('polar-angle-start', {
    _blocks: {
      'Other.Block': { _items: { Foo: 'Bar' } }
    }
  }, doc);

  assert.equal(patch.polarAngleStart, 15);
  assert.deepEqual(patch.polarEnabledAxes, ['horizontal', 'vertical', 'diagonal']);
  assert.equal(patch._blocks['Other.Block']._items.Foo, 'Bar');
  assert.equal(patch._blocks['ABEC.Polars:SPL_H']._items.MapAngleRange, '15,180,34');
});

test('buildPolarStatePatchFromConfig converts job polar config into explicit state keys', () => {
  const patch = buildPolarStatePatchFromConfig({}, {
    angle_range: [0, 90, 10],
    norm_angle: 3,
    distance: 4,
    inclination: 22,
    enabled_axes: ['diagonal']
  });

  assert.equal(patch.polarAngleStep, 10);
  assert.equal(patch.polarNormAngle, 3);
  assert.equal(patch.polarDistance, 4);
  assert.equal(patch.polarDiagonalAngle, 22);
  assert.deepEqual(patch.polarEnabledAxes, ['diagonal']);
  assert.deepEqual(Object.keys(patch._blocks), ['ABEC.Polars:SPL_D']);
});

test('syncPolarControlsFromState projects explicit state-backed values to the DOM', () => {
  const doc = makeDoc();
  syncPolarControlsFromState({
    polarAngleStart: 5,
    polarAngleEnd: 95,
    polarAngleStep: 15,
    polarNormAngle: 2,
    polarDistance: 6,
    polarDiagonalAngle: 30,
    polarEnabledAxes: ['diagonal']
  }, doc);

  assert.equal(doc._elements['polar-angle-start'].value, '5');
  assert.equal(doc._elements['polar-angle-end'].value, '95');
  assert.equal(doc._elements['polar-angle-step'].value, '15');
  assert.equal(doc._elements['polar-norm-angle'].value, '2');
  assert.equal(doc._elements['polar-distance'].value, '6');
  assert.equal(doc._elements['polar-axis-horizontal'].checked, false);
  assert.equal(doc._elements['polar-axis-diagonal'].checked, true);
  assert.equal(doc._elements['polar-inclination'].disabled, false);
});

test('renderPolarSettingsSection builds the directivity block from polar metadata', () => {
  const doc = new FakeRenderDocument();
  const container = doc.createElement('div');
  container.id = 'polar-settings-container';
  doc.body.appendChild(container);

  renderPolarSettingsSection(doc);

  assert.ok(doc.getElementById('polar-angle-start'));
  assert.ok(doc.getElementById('polar-angle-end'));
  assert.ok(doc.getElementById('polar-axis-horizontal'));
  assert.ok(doc.getElementById('polar-axis-diagonal'));
  const section = container.children[0];
  assert.equal(section.children[0].textContent, 'Directivity Map');
  assert.match(section.children[1].textContent, /Polar planes and angular sampling/i);
  assert.equal(container.children.length, 1);
});
