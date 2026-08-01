import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { PARAM_SCHEMA } from '../src/config/schema.js';
import { GlobalState } from '../src/state.js';
import { getParameterSections } from '../src/ui/parameterInventory.js';
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
      remove: (...tokens) => {
        const removed = new Set(tokens);
        this.className = this.className
          .split(/\s+/)
          .filter((token) => token && !removed.has(token))
          .join(' ');
      },
    };
    this.textContent = '';
    this.value = '';
    this.title = '';
    this.type = '';
    this.parentNode = null;
    this._id = '';
    this.selectionStart = null;
    this.selectionEnd = null;
    this.selectionDirection = 'none';
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

  get parentElement() {
    return this.parentNode;
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

  getAttribute(name) {
    return this.attributes[name] ?? null;
  }

  removeAttribute(name) {
    delete this.attributes[name];
  }

  querySelector(selector) {
    if (selector !== '.input-error-message') return null;
    return collectNodes(this, (node) => node.className === 'input-error-message')[0] || null;
  }

  contains(node) {
    if (node === this) return true;
    return this.children.some((child) => child.contains(node));
  }

  focus() {
    this.ownerDocument.activeElement = this;
  }

  setSelectionRange(start, end, direction = 'none') {
    this.selectionStart = start;
    this.selectionEnd = end;
    this.selectionDirection = direction;
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }
}

class FakeDocument {
  constructor() {
    this.elementsById = new Map();
    this.body = new FakeElement('body', this);
    this.activeElement = this.body;
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
  assert.equal(getControlInputMode(PARAM_SCHEMA['OSSE'].scale), 'number');
  assert.equal(getControlInputMode(PARAM_SCHEMA.MORPH.morphWidth), 'formula');
  assert.equal(getControlInputMode(PARAM_SCHEMA.GEOMETRY.rot), 'formula');
  assert.equal(getControlInputMode(PARAM_SCHEMA.MESH.angularSegments), 'number');
  assert.equal(getControlInputMode(PARAM_SCHEMA.MESH.apertureResolutionScale), 'number');
  assert.equal(getControlInputMode(PARAM_SCHEMA.ENCLOSURE.encFrontResolution), 'text');
  assert.equal(getControlInputMode(PARAM_SCHEMA.SOURCE.sourceContours), 'text');
  assert.equal(getControlInputMode(PARAM_SCHEMA.SOURCE.sourceShape), 'select');
});

test('parameter inventory exposes throat extension and scopes OSSE-only guiding curve', () => {
  const osseSections = getParameterSections('geometry', 'OSSE');
  assert.deepEqual(
    osseSections.map((section) => section.id),
    [
      'model-type',
      'core-profile',
      'throat-extension',
      'morph-target',
      'wall-enclosure',
      'guiding-curve',
      'preview-mesh',
    ]
  );

  const throatSection = osseSections.find((section) => section.id === 'throat-extension');
  assert.deepEqual(
    throatSection.groups.flatMap((group) => group.keys),
    ['throatExtAngle', 'throatExtLength', 'slotLength']
  );

  const guidingSection = osseSections.find((section) => section.id === 'guiding-curve');
  assert.deepEqual(
    guidingSection.groups.flatMap((group) => group.keys),
    [
      'throatProfile',
      'rot',
      'gcurveType',
      'gcurveDist',
      'gcurveWidth',
      'gcurveAspectRatio',
      'gcurveSeN',
      'gcurveSf',
      'gcurveSfA',
      'gcurveSfB',
      'gcurveSfM1',
      'gcurveSfM2',
      'gcurveSfN1',
      'gcurveSfN2',
      'gcurveSfN3',
      'gcurveRot',
      'circArcTermAngle',
      'circArcRadius',
    ]
  );

  const rosseSections = getParameterSections('geometry', 'R-OSSE');
  assert.deepEqual(
    rosseSections.map((section) => section.id),
    [
      'model-type',
      'core-profile',
      'throat-extension',
      'morph-target',
      'wall-enclosure',
      'preview-mesh',
    ]
  );

  const icwSectionIds = getParameterSections('geometry', 'ICW').map((section) => section.id);
  assert.ok(!icwSectionIds.includes('throat-extension'));
  assert.ok(!icwSectionIds.includes('guiding-curve'));

  const sourceSection = getParameterSections('simulation', 'R-OSSE').find(
    (section) => section.id === 'source-definition'
  );
  assert.deepEqual(
    sourceSection.groups.flatMap((group) => group.keys),
    ['sourceShape', 'sourceRadius', 'sourceCurv', 'sourceVelocity']
  );

  const meshSection = getParameterSections('simulation', 'R-OSSE').find(
    (section) => section.id === 'solve-export-mesh'
  );
  assert.deepEqual(
    meshSection.groups.flatMap((group) => group.keys),
    [
      'simType',
      'solverMode',
      'throatResolution',
      'mouthResolution',
      'rearResolution',
      'apertureResolutionScale',
      'maxTriangles',
      'allowLargeMesh',
      'verticalOffset',
      'quadrants',
      'encFrontResolution',
      'encBackResolution',
    ]
  );
});

test('FREEFORM inventory hides morph and keeps source controls available', () => {
  const geometrySections = getParameterSections('geometry', 'FREEFORM');
  const sectionIds = geometrySections.map((section) => section.id);
  assert.ok(sectionIds.includes('core-profile'));
  assert.ok(!sectionIds.includes('morph-target'));
  assert.ok(!sectionIds.includes('guiding-curve'));
  const core = geometrySections.find((section) => section.id === 'core-profile');
  assert.deepEqual(core.groups.flatMap((group) => group.keys), [
    'scale',
    'length',
    'throatRadius',
    'throatAngle',
    'mouthRadiusH',
    'mouthAngleH',
    'interiorH',
    'throatTangentScaleH',
    'mouthTangentScaleH',
    'mouthRadiusV',
    'mouthAngleV',
    'interiorV',
    'throatTangentScaleV',
    'mouthTangentScaleV',
    'crossSections',
    'overshootPolicy',
  ]);
  const source = getParameterSections('simulation', 'FREEFORM').find(
    (section) => section.id === 'source-definition'
  );
  assert.ok(source.groups[0].keys.includes('sourceRadius'));
  assert.ok(source.groups[0].keys.includes('sourceCurv'));
});

test('ParamPanel FREEFORM point tables add, clamp, sort, remove, and show their empty state', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationSettingsContainer = fakeDocument.createElement('div');
  simulationSettingsContainer.id = 'simulation-settings-container';
  fakeDocument.body.appendChild(simulationSettingsContainer);
  const simulationContainer = fakeDocument.createElement('div');
  simulationContainer.id = 'simulation-param-container';
  fakeDocument.body.appendChild(simulationContainer);
  global.document = fakeDocument;

  try {
    GlobalState.loadState(
      { type: 'FREEFORM', params: getDefaults('FREEFORM') },
      'param-panel-freeform-test'
    );
    const panel = new ParamPanel('param-container');
    panel.createFullPanel();

    const typeSelect = fakeDocument.getElementById('model-type');
    assert.ok(typeSelect.children.some((option) => option.value === 'FREEFORM'));
    panel.setProfileError('Mesher rejected the profile');
    const profileError = fakeDocument.getElementById('freeform-profile-error');
    assert.equal(profileError.textContent, 'Mesher rejected the profile');
    assert.equal(profileError.parentNode.id, 'core-profile');
    panel.setProfileError(null);
    assert.equal(profileError.parentNode, null);
    assert.equal(collectNodes(paramContainer, (node) => node.tagName === 'TEXTAREA').length, 0);
    const emptyHints = collectNodes(
      paramContainer,
      (node) => node.textContent === 'No interior points — throat and mouth define a 2-anchor curve'
    );
    assert.equal(emptyHints.length, 2);
    let addPoints = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Add point'
    );
    assert.equal(addPoints.length, 2);
    addPoints[0].onclick({ preventDefault() {} });
    assert.deepEqual(GlobalState.get().params.interiorH, [[60, 76.35]]);

    panel.createFullPanel();
    addPoints = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Add point'
    );
    addPoints[0].onclick({ preventDefault() {} });
    assert.deepEqual(GlobalState.get().params.interiorH, [[30, 44.525], [60, 76.35]]);

    panel.createFullPanel();
    const horizontalRows = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-point-row'
    ).slice(0, 2);
    assert.equal(horizontalRows.length, 2);
    assert.equal(Number(horizontalRows[0].children[0].value), 30);
    horizontalRows[0].children[0].value = 999;
    horizontalRows[0].children[0].onchange({ target: horizontalRows[0].children[0] });
    assert.equal(GlobalState.get().params.interiorH[1][0], 119.999);
    assert.match(horizontalRows[0].children[0].className, /freeform-point-clamped/);
    horizontalRows[0].children[2].onclick({ preventDefault() {} });
    assert.deepEqual(GlobalState.get().params.interiorH, [[60, 76.35]]);

    const stationRows = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-station-row'
    );
    assert.equal(stationRows.length, 2);
    assert.equal(stationRows[0].children[0].disabled, true);
    assert.equal(stationRows[1].children[0].disabled, true);
    assert.deepEqual(stationRows[0].children[1].children.map((option) => option.value), ['circle']);
    assert.deepEqual(stationRows[1].children[1].children.map((option) => option.value), [
      'ellipse',
      'superellipse',
      'rounded_rectangle',
    ]);

    const addStation = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Add station'
    )[0];
    addStation.onclick({ preventDefault() {} });
    assert.deepEqual(GlobalState.get().params.crossSections, [
      { t: 0, shape: 'circle' },
      { t: 0.5, shape: 'ellipse' },
      { t: 1, shape: 'ellipse' },
    ]);
  } finally {
    GlobalState.loadState(previousState, 'param-panel-freeform-test-restore');
    global.document = originalDocument;
  }
});

test('ParamPanel renders row-level formula buttons and removes the section-header affordance', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationSettingsContainer = fakeDocument.createElement('div');
  simulationSettingsContainer.id = 'simulation-settings-container';
  fakeDocument.body.appendChild(simulationSettingsContainer);
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
    assert.equal(coreHeader.textContent, 'Profile Dimensions');

    const rButtons = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.attributes['data-param-key'] === 'R'
    );
    assert.equal(rButtons.length, 1);

    const morphRows = collectNodes(
      paramContainer,
      (node) =>
        node.className === 'input-row' && node.attributes['data-param-key'] === 'morphTarget'
    );
    assert.equal(morphRows.length, 1);

    const encResolutionButtons = collectNodes(
      simulationContainer,
      (node) =>
        node.tagName === 'BUTTON' && node.attributes['data-param-key'] === 'encFrontResolution'
    );
    assert.equal(encResolutionButtons.length, 0);

    const sourceContourButtons = collectNodes(
      simulationContainer,
      (node) => node.tagName === 'BUTTON' && node.attributes['data-param-key'] === 'sourceContours'
    );
    assert.equal(sourceContourButtons.length, 0);
    const sourceContourRows = collectNodes(
      simulationContainer,
      (node) =>
        node.className === 'input-row' && node.attributes['data-param-key'] === 'sourceContours'
    );
    assert.equal(sourceContourRows.length, 0);

    const freqStartInput = fakeDocument.getElementById('freq-start');
    const freqEndInput = fakeDocument.getElementById('freq-end');
    const freqStepsInput = fakeDocument.getElementById('freq-steps');
    assert.equal(String(freqStartInput?.value), String(getDefaults('R-OSSE').freqStart));
    assert.equal(String(freqEndInput?.value), String(getDefaults('R-OSSE').freqEnd));
    assert.equal(String(freqStepsInput?.value), String(getDefaults('R-OSSE').numFreqs));

    const simulationLabel = collectNodes(
      simulationSettingsContainer,
      (node) => node.tagName === 'LABEL' && node.textContent === 'Sweep Start (Hz)'
    );
    assert.equal(simulationLabel.length, 1);
    const simulationHelpLabels = collectNodes(
      simulationSettingsContainer,
      (node) =>
        node.tagName === 'LABEL' && /backend BEM sweep/i.test(node.attributes['data-tooltip'] || '')
    );
    assert.equal(simulationHelpLabels.length, 2);

    const throatSliceDensityRows = collectNodes(
      paramContainer,
      (node) =>
        node.attributes['data-param-key'] === 'throatSliceDensity' && node.className === 'input-row'
    );
    const verticalOffsetRows = collectNodes(
      simulationContainer,
      (node) =>
        node.attributes['data-param-key'] === 'verticalOffset' && node.className === 'input-row'
    );
    const apertureScaleRows = collectNodes(
      simulationContainer,
      (node) =>
        node.attributes['data-param-key'] === 'apertureResolutionScale' &&
        node.className === 'input-row'
    );
    const quadrantRows = collectNodes(
      simulationContainer,
      (node) => node.attributes['data-param-key'] === 'quadrants' && node.className === 'input-row'
    );
    const quadrantAutoButtons = collectNodes(
      simulationContainer,
      (node) =>
        node.tagName === 'BUTTON' &&
        node.attributes['data-param-key'] === 'quadrants' &&
        node.textContent === 'Auto'
    );
    assert.equal(throatSliceDensityRows.length, 1);
    assert.equal(verticalOffsetRows.length, 1);
    assert.equal(apertureScaleRows.length, 1);
    assert.equal(quadrantRows.length, 1);
    assert.equal(quadrantAutoButtons.length, 1);

    const geometrySectionTitles = paramContainer.children.map(
      (child) => child.children[0]?.textContent
    );
    assert.deepEqual(geometrySectionTitles, [
      'Model Type',
      'Profile Dimensions',
      'Throat Extension',
      'Morph Target',
      'Wall & Enclosure',
      'Viewport Mesh',
    ]);

    const simulationSectionTitles = simulationContainer.children.map(
      (child) => child.children[0]?.textContent
    );
    assert.deepEqual(simulationSectionTitles, ['Source Definition', 'Solve & Export Mesh']);
  } finally {
    GlobalState.loadState(previousState, 'param-panel-test-restore');
    global.document = originalDocument;
  }
});

test('ParamPanel hides inactive straight slot row but keeps active slot values visible', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationSettingsContainer = fakeDocument.createElement('div');
  simulationSettingsContainer.id = 'simulation-settings-container';
  fakeDocument.body.appendChild(simulationSettingsContainer);
  const simulationContainer = fakeDocument.createElement('div');
  simulationContainer.id = 'simulation-param-container';
  fakeDocument.body.appendChild(simulationContainer);

  global.document = fakeDocument;

  try {
    GlobalState.loadState(
      {
        type: 'OSSE',
        params: {
          ...getDefaults('OSSE'),
          throatExtAngle: '0',
          throatExtLength: '0',
          slotLength: '0',
        },
      },
      'param-panel-slot-hidden-test'
    );
    const panel = new ParamPanel('param-container');
    panel.createFullPanel();

    let slotRows = collectNodes(
      paramContainer,
      (node) => node.attributes['data-param-key'] === 'slotLength' && node.className === 'input-row'
    );
    assert.equal(slotRows.length, 0);

    GlobalState.loadState(
      {
        type: 'OSSE',
        params: {
          ...getDefaults('OSSE'),
          throatExtAngle: '0',
          throatExtLength: '0',
          slotLength: '6',
        },
      },
      'param-panel-slot-visible-test'
    );
    panel.createFullPanel();

    slotRows = collectNodes(
      paramContainer,
      (node) => node.attributes['data-param-key'] === 'slotLength' && node.className === 'input-row'
    );
    assert.equal(slotRows.length, 1);
  } finally {
    GlobalState.loadState(previousState, 'param-panel-slot-test-restore');
    global.document = originalDocument;
  }
});

test('ParamPanel renders ICW coverage controls for flat baffle only', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationSettingsContainer = fakeDocument.createElement('div');
  simulationSettingsContainer.id = 'simulation-settings-container';
  fakeDocument.body.appendChild(simulationSettingsContainer);
  const simulationContainer = fakeDocument.createElement('div');
  simulationContainer.id = 'simulation-param-container';
  fakeDocument.body.appendChild(simulationContainer);

  global.document = fakeDocument;

  const countRows = (key) =>
    collectNodes(
      paramContainer,
      (node) => node.attributes['data-param-key'] === key && node.className === 'input-row'
    ).length;

  try {
    GlobalState.loadState(
      {
        type: 'ICW',
        params: getDefaults('ICW'),
      },
      'param-panel-icw-coverage-visible-test'
    );
    const panel = new ParamPanel('param-container');
    panel.createFullPanel();

    assert.equal(countRows('coverage_angle'), 1);
    assert.equal(countRows('hold_start'), 1);
    assert.equal(countRows('hold_end'), 1);

    GlobalState.loadState(
      {
        type: 'ICW',
        params: {
          ...getDefaults('ICW'),
          termination: 'rollback',
        },
      },
      'param-panel-icw-coverage-hidden-test'
    );
    panel.createFullPanel();

    assert.equal(countRows('coverage_angle'), 0);
    assert.equal(countRows('hold_start'), 0);
    assert.equal(countRows('hold_end'), 0);
    assert.equal(countRows('theta1_deg'), 1);
    assert.equal(countRows('depth'), 1);
    assert.equal(countRows('L'), 0);
  } finally {
    GlobalState.loadState(previousState, 'param-panel-icw-coverage-test-restore');
    global.document = originalDocument;
  }
});

test('ParamPanel restores focused control selection after rebuilding', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationSettingsContainer = fakeDocument.createElement('div');
  simulationSettingsContainer.id = 'simulation-settings-container';
  fakeDocument.body.appendChild(simulationSettingsContainer);
  const simulationContainer = fakeDocument.createElement('div');
  simulationContainer.id = 'simulation-param-container';
  fakeDocument.body.appendChild(simulationContainer);

  global.document = fakeDocument;

  try {
    GlobalState.loadState(
      { type: 'R-OSSE', params: getDefaults('R-OSSE') },
      'param-panel-focus-test'
    );
    const panel = new ParamPanel('param-container');
    panel.createFullPanel();

    const originalInput = collectNodes(
      paramContainer,
      (node) => node.tagName === 'INPUT' && node.attributes['data-param-key'] === 'R'
    )[0];
    originalInput.focus();
    originalInput.setSelectionRange(1, 3, 'forward');

    panel.createFullPanel();

    const restoredInput = collectNodes(
      paramContainer,
      (node) => node.tagName === 'INPUT' && node.attributes['data-param-key'] === 'R'
    )[0];
    assert.notEqual(restoredInput, originalInput);
    assert.equal(fakeDocument.activeElement, restoredInput);
    assert.equal(restoredInput.selectionStart, 1);
    assert.equal(restoredInput.selectionEnd, 3);
    assert.equal(restoredInput.selectionDirection, 'forward');
  } finally {
    GlobalState.loadState(previousState, 'param-panel-focus-test-restore');
    global.document = originalDocument;
  }
});

test('ParamPanel rejects invalid numeric commits using schema limits', () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  const simulationSettingsContainer = fakeDocument.createElement('div');
  simulationSettingsContainer.id = 'simulation-settings-container';
  fakeDocument.body.appendChild(simulationSettingsContainer);
  const simulationContainer = fakeDocument.createElement('div');
  simulationContainer.id = 'simulation-param-container';
  fakeDocument.body.appendChild(simulationContainer);

  global.document = fakeDocument;

  try {
    GlobalState.loadState(
      { type: 'OSSE', params: getDefaults('OSSE') },
      'param-panel-validation-test'
    );
    const panel = new ParamPanel('param-container');
    panel.createFullPanel();

    const frequencyStart = fakeDocument.getElementById('freq-start');
    const originalFrequencyStart = GlobalState.get().params.freqStart;
    for (const value of ['', 'Infinity', '15']) {
      frequencyStart.value = value;
      frequencyStart.onchange({ target: frequencyStart });
      assert.equal(GlobalState.get().params.freqStart, originalFrequencyStart);
      assert.equal(frequencyStart.attributes['aria-invalid'], 'true');
    }
    assert.equal(
      frequencyStart.parentElement.querySelector('.input-error-message').textContent,
      'Enter a value between 20 and 20000.'
    );

    frequencyStart.value = '25';
    frequencyStart.onchange({ target: frequencyStart });
    assert.equal(GlobalState.get().params.freqStart, 25);

    const cornerSegments = collectNodes(
      paramContainer,
      (node) => node.tagName === 'INPUT' && node.attributes['data-param-key'] === 'cornerSegments'
    )[0];
    const originalCornerSegments = GlobalState.get().params.cornerSegments;
    cornerSegments.value = '4.5';
    cornerSegments.onchange({ target: cornerSegments });
    assert.equal(GlobalState.get().params.cornerSegments, originalCornerSegments);
    assert.equal(
      cornerSegments.parentElement.querySelector('.input-error-message').textContent,
      'Enter a whole number.'
    );

    const terminationSmoothness = collectNodes(
      paramContainer,
      (node) => node.tagName === 'INPUT' && node.attributes['data-param-key'] === 'q'
    )[0];
    const originalSmoothness = GlobalState.get().params.q;
    terminationSmoothness.value = '9';
    terminationSmoothness.onchange({ target: terminationSmoothness });
    assert.equal(GlobalState.get().params.q, originalSmoothness);
    assert.equal(
      terminationSmoothness.parentElement.querySelector('.input-error-message').textContent,
      'Enter a value between 0.1 and 2.'
    );

    terminationSmoothness.value = '0.85 + 0.3*cos(p)^2';
    assert.equal(
      panel.validateInputOnChange(terminationSmoothness, 'q', PARAM_SCHEMA.OSSE.q),
      true
    );

    terminationSmoothness.value = 'x'.repeat(501);
    terminationSmoothness.onchange({ target: terminationSmoothness });
    assert.equal(GlobalState.get().params.q, originalSmoothness);
    assert.match(
      terminationSmoothness.parentElement.querySelector('.input-error-message').textContent,
      /Formula too long/
    );
  } finally {
    GlobalState.loadState(previousState, 'param-panel-validation-test-restore');
    global.document = originalDocument;
  }
});
