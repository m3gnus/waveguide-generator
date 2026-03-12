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
