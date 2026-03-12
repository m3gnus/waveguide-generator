import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { GlobalState } from '../src/state.js';
import {
  setupSimulationParamBindings,
  teardownSimulationParamBindings
} from '../src/ui/simulation/settings.js';
import { readSimulationState } from '../src/modules/simulation/state.js';

class FakeDocument {
  constructor() {
    this.elementsById = new Map();
    this.listeners = new Map();
  }

  getElementById(id) {
    return this.elementsById.get(id) || null;
  }

  addEventListener(type, handler) {
    if (!this.listeners.has(type)) {
      this.listeners.set(type, new Set());
    }
    this.listeners.get(type).add(handler);
  }

  removeEventListener(type, handler) {
    this.listeners.get(type)?.delete(handler);
  }

  dispatchEvent(event) {
    for (const handler of this.listeners.get(event.type) || []) {
      handler(event);
    }
  }
}

test('setupSimulationParamBindings tracks schema-driven frequency inputs rendered after initialization', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();

  global.document = fakeDocument;
  GlobalState.loadState({ type: 'R-OSSE', params: getDefaults('R-OSSE') }, 'simulation-settings-test');

  try {
    const panel = {
      simulationParamBindings: [
        { id: 'freq-start', key: 'freqStart', parse: (value) => parseFloat(value) }
      ]
    };

    setupSimulationParamBindings(panel);

    const input = { id: 'freq-start', value: '950' };
    fakeDocument.elementsById.set(input.id, input);
    fakeDocument.dispatchEvent({ type: 'change', target: input });

    assert.equal(readSimulationState().params.freqStart, 950);

    teardownSimulationParamBindings(panel);
    input.value = '1200';
    fakeDocument.dispatchEvent({ type: 'change', target: input });

    assert.equal(readSimulationState().params.freqStart, 950);
  } finally {
    GlobalState.loadState(previousState, 'simulation-settings-test-restore');
    global.document = originalDocument;
  }
});

test('setupSimulationParamBindings persists state-backed polar control changes into params and canonical blocks', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();

  global.document = fakeDocument;
  GlobalState.loadState({ type: 'R-OSSE', params: getDefaults('R-OSSE') }, 'simulation-settings-test');

  try {
    const panel = {
      simulationParamBindings: [
        { id: 'freq-start', key: 'freqStart', parse: (value) => parseFloat(value) }
      ]
    };

    fakeDocument.elementsById.set('polar-angle-start', { id: 'polar-angle-start', value: '0' });
    fakeDocument.elementsById.set('polar-angle-end', { id: 'polar-angle-end', value: '180' });
    fakeDocument.elementsById.set('polar-angle-step', { id: 'polar-angle-step', value: '5' });
    fakeDocument.elementsById.set('polar-distance', { id: 'polar-distance', value: '2' });
    fakeDocument.elementsById.set('polar-norm-angle', { id: 'polar-norm-angle', value: '5' });
    fakeDocument.elementsById.set('polar-inclination', { id: 'polar-inclination', value: '45', disabled: false });
    fakeDocument.elementsById.set('polar-axis-horizontal', { id: 'polar-axis-horizontal', checked: true });
    fakeDocument.elementsById.set('polar-axis-vertical', { id: 'polar-axis-vertical', checked: true });
    fakeDocument.elementsById.set('polar-axis-diagonal', { id: 'polar-axis-diagonal', checked: true });

    setupSimulationParamBindings(panel);

    const input = fakeDocument.getElementById('polar-angle-start');
    input.value = '15';
    fakeDocument.dispatchEvent({ type: 'change', target: input });

    const state = readSimulationState().params;
    assert.equal(state.polarAngleStart, 15);
    assert.deepEqual(state.polarEnabledAxes, ['horizontal', 'vertical', 'diagonal']);
    assert.equal(state._blocks['ABEC.Polars:SPL_H']._items.MapAngleRange, '15,180,34');

    teardownSimulationParamBindings(panel);
  } finally {
    GlobalState.loadState(previousState, 'simulation-settings-test-restore');
    global.document = originalDocument;
  }
});
