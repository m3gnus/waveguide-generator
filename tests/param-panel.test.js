import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { PARAM_SCHEMA } from '../src/config/schema.js';
import { GlobalState } from '../src/state.js';
import { getControlInputMode, ParamPanel } from '../src/ui/paramPanel.js';

class FakeElement {
  constructor(tagName, ownerDocument) {
    this.tagName = String(tagName || '').toUpperCase();
    this.ownerDocument = ownerDocument;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this.className = '';
    this.classList = {
      add: (...tokens) => {
        const existing = new Set(this.className.split(/\s+/).filter(Boolean));
        for (const token of tokens) {
          existing.add(token);
        }
        this.className = Array.from(existing).join(' ');
      },
    };
    this.textContent = '';
    this.value = '';
    this.title = '';
    this.type = '';
    this.parentNode = null;
    this._id = '';
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

class FakeDocument {
  constructor() {
    this.elementsById = new Map();
    this.body = new FakeElement('body', this);
  }

  createElement(tagName) {
    return new FakeElement(tagName, this);
  }

  getElementById(id) {
    return this.elementsById.get(id) || null;
  }
}

function collectNodes(node, predicate, matches = []) {
  if (!node) return matches;
  if (predicate(node)) {
    matches.push(node);
  }
  for (const child of node.children || []) {
    collectNodes(child, predicate, matches);
  }
  return matches;
}

test('formula allowlist limits per-row formula controls to audited fields', () => {
  assert.equal(getControlInputMode(PARAM_SCHEMA['R-OSSE'].R), 'formula');
  assert.equal(getControlInputMode(PARAM_SCHEMA['OSSE'].scale), 'formula');
  assert.equal(getControlInputMode(PARAM_SCHEMA.MORPH.morphWidth), 'formula');
  assert.equal(getControlInputMode(PARAM_SCHEMA.GEOMETRY.rot), 'formula');
  assert.equal(getControlInputMode(PARAM_SCHEMA.MESH.angularSegments), 'number');
  assert.equal(getControlInputMode(PARAM_SCHEMA.ENCLOSURE.encFrontResolution), 'text');
  assert.equal(getControlInputMode(PARAM_SCHEMA.SOURCE.sourceContours), 'text');
  assert.equal(getControlInputMode(PARAM_SCHEMA.SOURCE.sourceShape), 'select');
});

test('ParamPanel renders row-level formula buttons and removes the section-header affordance', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationContainer = fakeDocument.createElement('div');
  simulationContainer.id = 'simulation-param-container';
  fakeDocument.body.appendChild(simulationContainer);

  global.document = fakeDocument;
  GlobalState.loadState({ type: 'R-OSSE', params: getDefaults('R-OSSE') }, 'param-panel-test');

  try {
    const panel = new ParamPanel('param-container');
    panel.createFullPanel();

    const coreSection = paramContainer.children[1];
    const coreHeader = coreSection.children[0];
    const headerButtons = collectNodes(coreHeader, (node) => node.tagName === 'BUTTON');
    assert.equal(headerButtons.length, 0);

    const rButtons = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.attributes['data-param-key'] === 'R',
    );
    assert.equal(rButtons.length, 1);

    const encResolutionButtons = collectNodes(
      simulationContainer,
      (node) =>
        node.tagName === 'BUTTON' && node.attributes['data-param-key'] === 'encFrontResolution',
    );
    assert.equal(encResolutionButtons.length, 0);

    const sourceContourButtons = collectNodes(
      simulationContainer,
      (node) =>
        node.tagName === 'BUTTON' && node.attributes['data-param-key'] === 'sourceContours',
    );
    assert.equal(sourceContourButtons.length, 0);
  } finally {
    GlobalState.loadState(previousState, 'param-panel-test-restore');
    global.document = originalDocument;
  }
});
