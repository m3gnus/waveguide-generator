import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { getDefaults } from '../src/config/defaults.js';
import { PARAM_SCHEMA } from '../src/config/schema.js';
import { AppEvents } from '../src/events.js';
import { GlobalState } from '../src/state.js';
import { getParameterSections } from '../src/ui/parameterInventory.js';
import { getControlInputMode, ParamPanel } from '../src/ui/paramPanel.js';
import { getViewportStateCacheKey } from '../src/app/viewportCacheKey.js';

const authoritativeFixture = JSON.parse(
  readFileSync(new URL('./fixtures/freeform-authoritative.json', import.meta.url), 'utf8')
);

function syntheticInsetGrid({ radiusH = 80, radiusV = 55, length = 120 } = {}) {
  const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  const innerPoints = [];
  for (const angle of angles) {
    for (const z of [0, length]) {
      innerPoints.push(radiusH * Math.cos(angle), radiusV * Math.sin(angle), z);
    }
  }
  return {
    angle_list: angles,
    grid_n_phi: angles.length,
    grid_n_length: 1,
    inner_points: innerPoints,
    full_circle: true,
    quadrants: 1234,
  };
}

class FakeElement {
  constructor(tagName, ownerDocument, namespaceURI = null) {
    this.tagName = String(tagName || '').toUpperCase();
    this.ownerDocument = ownerDocument;
    this.namespaceURI = namespaceURI;
    this.children = [];
    this.attributes = {};
    this.style = {};
    this._className = '';
    Object.defineProperty(this, 'className', {
      configurable: true,
      get: () => this._className,
      set: (value) => {
        if (this.namespaceURI) {
          throw new TypeError('SVGElement.className is an SVGAnimatedString.');
        }
        this._className = String(value || '');
      },
    });
    this.classList = {
      add: (...tokens) => {
        const existing = new Set(this._className.split(/\s+/).filter(Boolean));
        for (const token of tokens) {
          existing.add(token);
        }
        this.setAttribute('class', Array.from(existing).join(' '));
      },
      remove: (...tokens) => {
        const removed = new Set(tokens);
        this.setAttribute(
          'class',
          this._className
            .split(/\s+/)
            .filter((token) => token && !removed.has(token))
            .join(' ')
        );
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
    if (name === 'class') this._className = String(value);
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

  createElementNS(namespaceURI, tagName) {
    return new FakeElement(tagName, this, namespaceURI);
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

function withFreeformPanel(paramsPatch, callback) {
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
  let panel = null;

  try {
    GlobalState.loadState(
      {
        type: 'FREEFORM',
        params: { ...getDefaults('FREEFORM'), ...paramsPatch },
      },
      'param-panel-freeform-editor-test'
    );
    panel = new ParamPanel('param-container');
    panel.createFullPanel();
    callback({ fakeDocument, paramContainer, panel, editor: panel.freeformEditor });
  } finally {
    panel?.freeformEditor?.destroy();
    panel?.freeformInset?.destroy();
    GlobalState.loadState(previousState, 'param-panel-freeform-editor-test-restore');
    global.document = originalDocument;
  }
}

test('FakeDocument models SVG namespace className semantics', () => {
  const fakeDocument = new FakeDocument();
  const svg = fakeDocument.createElementNS('http://www.w3.org/2000/svg', 'svg');
  assert.equal(svg.namespaceURI, 'http://www.w3.org/2000/svg');
  assert.throws(() => {
    svg.className = 'invalid-svg-assignment';
  }, /SVGAnimatedString/);
  svg.setAttribute('class', 'valid-svg-class');
  assert.equal(svg.className, 'valid-svg-class');
});

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
  assert.deepEqual(
    core.groups.flatMap((group) => group.keys),
    [
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
      'inflectionPolicy',
    ]
  );
  assert.equal(PARAM_SCHEMA.FREEFORM.inflectionPolicy.label, 'Curve Direction');
  assert.deepEqual(PARAM_SCHEMA.FREEFORM.inflectionPolicy.options, [
    { value: 'warn', label: 'Warn on S-curves' },
    { value: 'reject', label: 'Enforce one-way' },
  ]);
  const source = getParameterSections('simulation', 'FREEFORM').find(
    (section) => section.id === 'source-definition'
  );
  assert.ok(source.groups[0].keys.includes('sourceRadius'));
  assert.ok(source.groups[0].keys.includes('sourceCurv'));
});

test('FREEFORM backend errors decorate the matching panel row and 2-D editor station', () => {
  withFreeformPanel(
    {
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 0.5, shape: 'rounded_rectangle', cornerRadiusMm: 8 },
        { t: 1, shape: 'ellipse' },
      ],
    },
    ({ fakeDocument, paramContainer, panel, editor }) => {
      const detail =
        'FREEFORM crossSections[1].cornerRadiusMm must be in [1.1, 55] mm at station t=0.5, got 1 mm';
      panel.setProfileError(detail);

      const row = collectNodes(
        paramContainer,
        (node) => node.attributes['data-freeform-station-index'] === '1'
      )[0];
      assert.match(row.className, /freeform-element-error/);
      assert.match(
        row.children.find((child) => child.className === 'freeform-element-error-message')
          .textContent,
        /^Station 2:/
      );
      const stationLine = collectNodes(
        editor.svg,
        (node) =>
          node.attributes['data-station-index'] === '1' &&
          String(node.attributes.class).includes('freeform-profile-station')
      )[0];
      assert.match(stationLine.attributes.class, /freeform-profile-element-error/);
      assert.equal(fakeDocument.getElementById('freeform-profile-error').textContent, detail);

      panel.setProfileError(null);
      assert.doesNotMatch(row.className, /freeform-element-error/);
      assert.equal(fakeDocument.getElementById('freeform-profile-error').parentNode, null);
    }
  );
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
    const pasteTextareas = collectNodes(paramContainer, (node) => node.tagName === 'TEXTAREA');
    assert.equal(pasteTextareas.length, 2);
    assert.ok(pasteTextareas.every((node) => node.parentNode.hidden));
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
    assert.deepEqual(GlobalState.get().params.interiorH, [
      { z: 60, r: 76.35, angleDeg: null, strength: null },
    ]);

    panel.createFullPanel();
    addPoints = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Add point'
    );
    addPoints[0].onclick({ preventDefault() {} });
    assert.deepEqual(GlobalState.get().params.interiorH, [
      { z: 30, r: 44.525, angleDeg: null, strength: null },
      { z: 60, r: 76.35, angleDeg: null, strength: null },
    ]);

    panel.createFullPanel();
    const horizontalRows = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-point-row'
    ).slice(0, 2);
    assert.equal(horizontalRows.length, 2);
    const horizontalHeader = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-point-header'
    )[0];
    assert.deepEqual(
      horizontalHeader.children.map((node) => node.textContent),
      ['Depth (mm)', 'Half-width (mm)', 'Angle (deg)', 'Strength', '']
    );
    assert.equal(Number(horizontalRows[0].children[0].value), 30);
    assert.equal(horizontalRows[0].children[2].value, '');
    assert.equal(horizontalRows[0].children[3].disabled, true);
    horizontalRows[0].children[0].value = 999;
    horizontalRows[0].children[0].onchange({ target: horizontalRows[0].children[0] });
    assert.equal(GlobalState.get().params.interiorH[1].z, 119);
    assert.match(horizontalRows[0].children[0].className, /freeform-point-clamped/);
    horizontalRows[0].children[4].onclick({ preventDefault() {} });
    assert.deepEqual(GlobalState.get().params.interiorH, [
      { z: 60, r: 76.35, angleDeg: null, strength: null },
    ]);

    const stationRows = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-station-row'
    );
    assert.equal(stationRows.length, 2);
    assert.equal(stationRows[0].children[0].disabled, true);
    assert.equal(stationRows[1].children[0].disabled, true);
    assert.deepEqual(
      stationRows[0].children[2].children.map((option) => option.value),
      ['circle']
    );
    assert.deepEqual(
      stationRows[1].children[2].children.map((option) => option.value),
      ['ellipse', 'superellipse', 'rounded_rectangle']
    );

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

test('FREEFORM point paste preview applies endpoints and interior anchors in one state update', () => {
  withFreeformPanel({}, ({ paramContainer }) => {
    const pasteButton = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Paste points…'
    )[0];
    pasteButton.onclick({ preventDefault() {} });

    const textarea = collectNodes(paramContainer, (node) => node.tagName === 'TEXTAREA')[0];
    assert.equal(textarea.parentNode.hidden, false);
    textarea.value = '# measured in mm\n0 13\n40 52\n120 151';
    textarea.oninput();

    const preview = collectNodes(
      textarea.parentNode,
      (node) => node.attributes.role === 'status'
    )[0];
    assert.match(preview.textContent, /3 rows · 2-column points/);
    const apply = collectNodes(
      textarea.parentNode,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Apply'
    )[0];
    assert.equal(apply.disabled, false);
    const historyBefore = GlobalState.undoStack.length;
    apply.onclick({ preventDefault() {} });

    assert.equal(GlobalState.undoStack.length, historyBefore + 1);
    assert.equal(GlobalState.get().params.throatRadius, 13);
    assert.equal(GlobalState.get().params.mouthRadiusH, 151);
    assert.deepEqual(GlobalState.get().params.interiorH, [
      { z: 40, r: 52, angleDeg: null, strength: null },
    ]);
  });
});

test('FREEFORM profile editor renders endpoint, tangent, and interior handles from params', () => {
  withFreeformPanel(
    {
      interiorH: [
        [30, 40],
        [70, 90],
      ],
      interiorV: [[55, 68]],
    },
    ({ paramContainer, editor }) => {
      assert.ok(editor);
      const handles = collectNodes(
        paramContainer,
        (node) => node.attributes['data-handle'] !== undefined
      );
      assert.equal(handles.filter((node) => node.attributes['data-handle'] === 'radius').length, 3);
      assert.equal(handles.filter((node) => node.attributes['data-handle'] === 'angle').length, 3);
      assert.equal(
        handles.filter((node) => node.attributes['data-handle'] === 'interior').length,
        3
      );
      assert.equal(
        collectNodes(paramContainer, (node) => node.attributes['data-param'] === 'mouthRadiusH')
          .length > 1,
        true
      );
    }
  );
});

test('FREEFORM profile editor commits drag results through GlobalState update semantics', () => {
  withFreeformPanel({}, ({ editor }) => {
    editor.commitParam('mouthRadiusH', 157.5);
    assert.equal(GlobalState.get().params.mouthRadiusH, 157.5);
    assert.equal(editor.params.mouthRadiusH, 157.5);
  });
});

test('FREEFORM anchor drag keeps its identity while crossing another anchor', () => {
  withFreeformPanel(
    {
      interiorH: [
        [30, 20],
        [60, 40],
      ],
    },
    ({ paramContainer, editor }) => {
      const handle = collectNodes(
        paramContainer,
        (node) =>
          node.attributes['data-handle'] === 'interior' && node.attributes['data-index'] === '0'
      )[0];
      editor.onPointerDown({
        target: handle,
        pointerId: 21,
        clientX: editor.transforms.x(30),
        clientY: editor.transforms.y(20),
        preventDefault() {},
      });
      editor.onPointerMove({
        pointerId: 21,
        clientX: editor.transforms.x(70),
        clientY: editor.transforms.y(25),
        preventDefault() {},
      });
      editor.onPointerMove({
        pointerId: 21,
        clientX: editor.transforms.x(80),
        clientY: editor.transforms.y(30),
        preventDefault() {},
      });
      editor.onPointerUp({ pointerId: 21, preventDefault() {} });

      assert.deepEqual(GlobalState.get().params.interiorH, [
        { z: 60, r: 40, angleDeg: null, strength: null },
        { z: 80, r: 30, angleDeg: null, strength: null },
      ]);
    }
  );
});

test('FREEFORM anchor drag nudges an exact z collision instead of deleting an anchor', () => {
  withFreeformPanel(
    {
      interiorH: [
        [30, 20],
        [60, 40],
      ],
    },
    ({ paramContainer, editor }) => {
      const handle = collectNodes(
        paramContainer,
        (node) =>
          node.attributes['data-handle'] === 'interior' && node.attributes['data-index'] === '0'
      )[0];
      editor.onPointerDown({
        target: handle,
        pointerId: 22,
        clientX: editor.transforms.x(30),
        clientY: editor.transforms.y(20),
        preventDefault() {},
      });
      editor.onPointerMove({
        pointerId: 22,
        clientX: editor.transforms.x(60),
        clientY: editor.transforms.y(25),
        preventDefault() {},
      });
      editor.onPointerUp({ pointerId: 22, preventDefault() {} });

      assert.equal(GlobalState.get().params.interiorH.length, 2);
      assert.deepEqual(
        GlobalState.get().params.interiorH.map((point) => point.z),
        [60, 60.1]
      );
    }
  );
});

test('FREEFORM pointercancel discards an anchor drag without committing', () => {
  withFreeformPanel({ interiorH: [[30, 20]] }, ({ paramContainer, editor }) => {
    const before = JSON.parse(JSON.stringify(GlobalState.get().params.interiorH));
    const version = GlobalState.getVersion();
    const historyLength = GlobalState.undoStack.length;
    const handle = collectNodes(
      paramContainer,
      (node) => node.attributes['data-handle'] === 'interior'
    )[0];

    editor.onPointerDown({
      target: handle,
      pointerId: 23,
      clientX: editor.transforms.x(30),
      clientY: editor.transforms.y(20),
      preventDefault() {},
    });
    editor.onPointerMove({
      pointerId: 23,
      clientX: editor.transforms.x(70),
      clientY: editor.transforms.y(45),
      preventDefault() {},
    });
    editor.onPointerCancel({ pointerId: 23, preventDefault() {} });

    assert.deepEqual(editor.params.interiorH, before);
    assert.deepEqual(GlobalState.get().params.interiorH, before);
    assert.equal(GlobalState.getVersion(), version);
    assert.equal(GlobalState.undoStack.length, historyLength);
  });
});

test('FREEFORM profile editor flashes anchors clamped by a length update once', () => {
  withFreeformPanel({ interiorH: [[60, 55]] }, ({ paramContainer, panel, editor }) => {
    AppEvents.off('state:updated', editor._onStateUpdated);
    GlobalState.update({ length: 40 });
    panel.createFullPanel();

    assert.equal(GlobalState.get().params.interiorH[0].z, 39);
    const handle = collectNodes(
      paramContainer,
      (node) => node.attributes['data-handle'] === 'interior'
    )[0];
    assert.match(handle.className, /freeform-point-clamped/);

    panel.freeformEditor.draw();
    const redrawnHandle = collectNodes(
      paramContainer,
      (node) => node.attributes['data-handle'] === 'interior'
    )[0];
    assert.doesNotMatch(redrawnHandle.className, /freeform-point-clamped/);
  });
});

test('FREEFORM selected-anchor tangent handle commits angle/strength and double-click resets', () => {
  withFreeformPanel({ interiorH: [[40, 55]] }, ({ paramContainer, editor }) => {
    editor.selectedAnchor = { plane: 'H', index: 0 };
    editor.draw();
    let knob = collectNodes(
      paramContainer,
      (node) => node.attributes['data-handle'] === 'interior-tangent'
    )[0];
    assert.ok(knob);
    assert.match(knob.attributes['aria-label'], /automatic/);

    const baseArmLength = Number(knob.attributes['data-base-arm-length']);
    const targetAngle = 30;
    const targetStrength = 2;
    const radians = (targetAngle * Math.PI) / 180;
    const targetZ = 40 + baseArmLength * targetStrength * Math.cos(radians);
    const targetRadius = 55 + baseArmLength * targetStrength * Math.sin(radians);
    editor.onPointerDown({
      target: knob,
      pointerId: 7,
      clientX: Number(knob.attributes.cx),
      clientY: Number(knob.attributes.cy),
      preventDefault() {},
    });
    editor.onPointerMove({
      pointerId: 7,
      clientX: editor.transforms.x(targetZ),
      clientY: editor.transforms.y(targetRadius),
      preventDefault() {},
    });
    assert.equal(
      collectNodes(paramContainer, (node) => /strength 2\.00/.test(node.textContent)).length,
      1
    );
    editor.onPointerUp({ pointerId: 7, preventDefault() {} });
    assert.equal(GlobalState.get().params.interiorH[0].angleDeg, 30);
    assert.equal(GlobalState.get().params.interiorH[0].strength, 2);

    knob = collectNodes(
      paramContainer,
      (node) => node.attributes['data-handle'] === 'interior-tangent'
    )[0];
    editor.onDoubleClick({ target: knob, preventDefault() {} });
    assert.equal(GlobalState.get().params.interiorH[0].angleDeg, null);
    assert.equal(GlobalState.get().params.interiorH[0].strength, null);
  });
});

test('FREEFORM interior tangent pointer press without movement stays automatic', () => {
  withFreeformPanel({ interiorH: [[40, 55]] }, ({ paramContainer, editor }) => {
    editor.selectedAnchor = { plane: 'H', index: 0 };
    editor.draw();
    const knob = collectNodes(
      paramContainer,
      (node) => node.attributes['data-handle'] === 'interior-tangent'
    )[0];
    const version = GlobalState.getVersion();
    editor.onPointerDown({
      target: knob,
      pointerId: 8,
      clientX: Number(knob.attributes.cx),
      clientY: Number(knob.attributes.cy),
      preventDefault() {},
    });
    editor.onPointerUp({ pointerId: 8, preventDefault() {} });

    assert.equal(GlobalState.get().params.interiorH[0].angleDeg, null);
    assert.equal(GlobalState.get().params.interiorH[0].strength, null);
    assert.equal(GlobalState.getVersion(), version);
  });
});

test('FREEFORM double-click inserts only on empty curve area, not handles', () => {
  withFreeformPanel(
    {
      interiorH: [[40, 55]],
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 0.5, shape: 'ellipse' },
        { t: 1, shape: 'ellipse' },
      ],
    },
    ({ paramContainer, editor }) => {
      const handles = ['interior', 'radius', 'angle', 'station'].map(
        (kind) => collectNodes(paramContainer, (node) => node.attributes['data-handle'] === kind)[0]
      );
      for (const handle of handles) {
        assert.ok(handle);
        editor.onDoubleClick({ target: handle, preventDefault() {} });
        assert.equal(GlobalState.get().params.interiorH.length, 1);
      }

      const curve = collectNodes(
        paramContainer,
        (node) =>
          node.attributes['data-curve-source'] !== undefined &&
          node.attributes['data-plane'] === 'H'
      )[0];
      const z = 80;
      const radius = editor.radiusAtZ('H', z);
      editor.onDoubleClick({
        target: curve,
        clientX: editor.transforms.x(z),
        clientY: editor.transforms.y(radius),
        preventDefault() {},
      });
      assert.equal(GlobalState.get().params.interiorH.length, 2);
    }
  );
});

test('FREEFORM profile editor inserts and deletes plane anchors', () => {
  withFreeformPanel({}, ({ editor }) => {
    editor.insertAnchor('H', 61.25, 78.75);
    assert.deepEqual(GlobalState.get().params.interiorH, [
      { z: 61.3, r: 78.8, angleDeg: null, strength: null },
    ]);
    editor.deleteAnchor('H', 0);
    assert.deepEqual(GlobalState.get().params.interiorH, []);

    editor.insertAnchor('V', 44, 53);
    assert.deepEqual(GlobalState.get().params.interiorV, [
      { z: 44, r: 53, angleDeg: null, strength: null },
    ]);
  });
});

test('FREEFORM point fields commit CAD angle and strength controls', () => {
  withFreeformPanel({ interiorH: [[40, 55]] }, ({ paramContainer, panel }) => {
    let row = collectNodes(paramContainer, (node) => node.className === 'freeform-point-row')[0];
    row.children[2].value = '27.4';
    row.children[2].onchange({ target: row.children[2] });
    assert.deepEqual(GlobalState.get().params.interiorH[0], {
      z: 40,
      r: 55,
      angleDeg: 27,
      strength: null,
    });

    panel.createFullPanel();
    row = collectNodes(paramContainer, (node) => node.className === 'freeform-point-row')[0];
    assert.equal(row.children[3].disabled, false);
    row.children[3].value = '1.8';
    row.children[3].onchange({ target: row.children[3] });
    assert.equal(GlobalState.get().params.interiorH[0].strength, 1.8);

    panel.createFullPanel();
    row = collectNodes(paramContainer, (node) => node.className === 'freeform-point-row')[0];
    row.children[2].value = '';
    row.children[2].onchange({ target: row.children[2] });
    assert.equal(GlobalState.get().params.interiorH[0].angleDeg, null);
    assert.equal(GlobalState.get().params.interiorH[0].strength, null);
  });
});

test('FREEFORM point table refuses non-positive radii', () => {
  withFreeformPanel({ interiorH: [[40, 55]] }, ({ paramContainer }) => {
    const row = collectNodes(paramContainer, (node) => node.className === 'freeform-point-row')[0];
    row.children[1].value = '-5';
    row.children[1].onchange({ target: row.children[1] });

    assert.equal(GlobalState.get().params.interiorH[0].r, 55);
    assert.match(
      collectNodes(row.parentNode, (node) => node.className === 'input-error-message')[0]
        .textContent,
      /greater than 0/
    );
  });
});

test('FREEFORM panel restores focus to the same point field after a rebuild', () => {
  withFreeformPanel(
    {
      interiorH: [
        [30, 40],
        [70, 90],
      ],
    },
    ({ fakeDocument, paramContainer, panel }) => {
      const radius = collectNodes(
        paramContainer,
        (node) => node.attributes['aria-label'] === 'interiorH point 2 radius'
      )[0];
      radius.focus();
      radius.value = '92';
      radius.onchange({ target: radius });
      panel.createFullPanel();

      assert.equal(fakeDocument.activeElement.attributes['aria-label'], 'interiorH point 2 radius');
      assert.equal(fakeDocument.activeElement.value, '92');
    }
  );
});

test('FREEFORM panel restores focus to the same station depth field after a rebuild', () => {
  withFreeformPanel(
    {
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 0.4, shape: 'ellipse' },
        { t: 1, shape: 'ellipse' },
      ],
    },
    ({ fakeDocument, paramContainer, panel }) => {
      const depth = collectNodes(
        paramContainer,
        (node) => node.attributes['aria-label'] === 'Station 2 Depth (mm)'
      )[0];
      depth.focus();
      depth.value = '60';
      depth.onchange({ target: depth });
      panel.createFullPanel();

      assert.equal(fakeDocument.activeElement.attributes['aria-label'], 'Station 2 Depth (mm)');
      assert.equal(fakeDocument.activeElement.value, '60');
    }
  );
});

test('FREEFORM panel preserves uncommitted focused text across an unrelated rebuild', () => {
  withFreeformPanel({ interiorH: [[40, 55]] }, ({ fakeDocument, paramContainer, panel }) => {
    const angle = collectNodes(
      paramContainer,
      (node) => node.attributes['aria-label'] === 'interiorH point 1 tangent angle'
    )[0];
    angle.focus();
    angle.value = '37';
    GlobalState.update({ mouthRadiusV: 155 });
    panel.createFullPanel();

    assert.equal(
      fakeDocument.activeElement.attributes['aria-label'],
      'interiorH point 1 tangent angle'
    );
    assert.equal(fakeDocument.activeElement.value, '37');
    assert.equal(GlobalState.get().params.interiorH[0].angleDeg, null);
  });
});

test('FREEFORM station editor uses millimetre corner radius', () => {
  withFreeformPanel(
    {
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 1, shape: 'rounded_rectangle', cornerRadiusMm: 10 },
      ],
    },
    ({ paramContainer }) => {
      const cornerInput = collectNodes(
        paramContainer,
        (node) => node.attributes['aria-label'] === 'Station 2 Corner radius (mm)'
      )[0];
      assert.ok(cornerInput);
      assert.equal(cornerInput.min, '1');
      assert.equal(cornerInput.step, '1');
      assert.equal(cornerInput.value, 10);

      cornerInput.value = '11';
      cornerInput.onchange({ target: cornerInput });
      assert.deepEqual(GlobalState.get().params.crossSections[1], {
        t: 1,
        shape: 'rounded_rectangle',
        cornerRadiusMm: 11,
      });
    }
  );
});

test('FREEFORM station rows commit normalized and millimetre depth bidirectionally', () => {
  withFreeformPanel(
    {
      length: 120,
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 0.4, shape: 'ellipse' },
        { t: 1, shape: 'ellipse' },
      ],
    },
    ({ paramContainer, panel }) => {
      const findInput = (label) =>
        collectNodes(paramContainer, (node) => node.attributes['aria-label'] === label)[0];

      let position = findInput('Station 2 position t');
      let depth = findInput('Station 2 Depth (mm)');
      assert.equal(Number(position.value), 0.4);
      assert.equal(depth.value, '48.0');

      depth.value = '60';
      depth.onchange({ target: depth });
      assert.equal(GlobalState.get().params.crossSections[1].t, 0.5);

      panel.createFullPanel();
      position = findInput('Station 2 position t');
      position.value = '0.25';
      position.onchange({ target: position });
      assert.equal(GlobalState.get().params.crossSections[1].t, 0.25);

      panel.createFullPanel();
      depth = findInput('Station 2 Depth (mm)');
      assert.equal(depth.value, '30.0');
    }
  );
});

test('FREEFORM station drag clamps between neighbours, shows mm, and moves the scrubber', () => {
  withFreeformPanel(
    {
      length: 120,
      crossSections: [
        { t: 0, shape: 'circle' },
        { t: 0.4, shape: 'ellipse' },
        { t: 0.6, shape: 'superellipse', exponent: 4 },
        { t: 1, shape: 'ellipse' },
      ],
    },
    ({ paramContainer, editor }) => {
      const stationHit = collectNodes(
        paramContainer,
        (node) =>
          node.attributes['data-handle'] === 'station' && node.attributes['data-index'] === '1'
      )[0];
      assert.ok(stationHit);
      assert.equal(
        collectNodes(paramContainer, (node) => node.attributes['data-handle'] === 'station').length,
        2
      );
      assert.ok(
        collectNodes(paramContainer, (node) => node.textContent === 'ellipse · 48.0 mm').length >= 1
      );

      editor.onPointerDown({
        target: stationHit,
        pointerId: 12,
        clientX: editor.transforms.x(48),
        clientY: 40,
        preventDefault() {},
      });
      assert.equal(
        collectNodes(paramContainer, (node) => node.textContent === '48.0 mm · t=0.400').length,
        1
      );

      editor.onPointerMove({
        pointerId: 12,
        clientX: editor.transforms.x(108),
        clientY: 40,
        preventDefault() {},
      });
      const scrubber = collectNodes(
        paramContainer,
        (node) => node.attributes['data-freeform-scrubber'] !== undefined
      )[0];
      assert.equal(Number(scrubber.value), 0.59);
      assert.equal(
        collectNodes(
          paramContainer,
          (node) => node.attributes['data-scrub-cursor'] !== undefined
        )[0].attributes['data-scrub-t'],
        '0.590'
      );

      editor.onPointerUp({ pointerId: 12, preventDefault() {} });
      assert.equal(GlobalState.get().params.crossSections[1].t, 0.59);
      assert.equal(GlobalState.get().params.crossSections[2].t, 0.6);
    }
  );
});

test('FREEFORM inset scrubber drives the profile editor cursor', () => {
  withFreeformPanel({}, ({ paramContainer }) => {
    const scrubber = collectNodes(
      paramContainer,
      (node) => node.attributes['data-freeform-scrubber'] !== undefined
    )[0];
    scrubber.value = '0.4';
    scrubber.oninput({ target: scrubber });

    const cursor = collectNodes(
      paramContainer,
      (node) => node.attributes['data-scrub-cursor'] !== undefined
    )[0];
    assert.equal(cursor.attributes['data-scrub-t'], '0.400');
  });
});

test('FREEFORM inset renders the sampled grid outline and dimension readout', () => {
  withFreeformPanel(authoritativeFixture.params, ({ paramContainer }) => {
    assert.equal(
      collectNodes(
        paramContainer,
        (node) => node.textContent === 'outline appears after the first build'
      ).length,
      1
    );
    const scrubber = collectNodes(
      paramContainer,
      (node) => node.attributes['data-freeform-scrubber'] !== undefined
    )[0];
    scrubber.value = '0.4';
    scrubber.oninput({ target: scrubber });
    AppEvents.emit('freeform:authoritative', {
      cacheKey: getViewportStateCacheKey(GlobalState.get()),
      freeform: authoritativeFixture.freeform,
      grid: syntheticInsetGrid(),
    });

    assert.equal(
      collectNodes(
        paramContainer,
        (node) => node.textContent === 'depth 48.0 mm · t 0.40 · 160.0 x 110.0 mm'
      ).length,
      1
    );
    assert.equal(
      collectNodes(paramContainer, (node) =>
        (node.attributes.class || '').includes('freeform-cross-section-outline')
      ).length,
      1
    );
  });
});

test('FREEFORM profile editor legend chips toggle each plane without changing params', () => {
  withFreeformPanel({}, ({ editor }) => {
    const before = JSON.parse(JSON.stringify(GlobalState.get().params));
    editor.togglePlane('V');
    assert.equal(editor.visibility.V, false);
    assert.equal(
      editor.findByAttribute('data-plane-toggle', 'V').getAttribute('aria-pressed'),
      'false'
    );
    assert.equal(editor.findByAttribute('data-plane', 'V'), null);
    assert.deepEqual(GlobalState.get().params, before);

    editor.togglePlane('V');
    assert.equal(editor.visibility.V, true);
    assert.ok(editor.findByAttribute('data-plane', 'V'));
  });
});

test('FREEFORM profile editor consumes authoritative curves, spans, and deviation diagnostics', () => {
  withFreeformPanel(authoritativeFixture.params, ({ paramContainer, editor }) => {
    const curves = () =>
      collectNodes(paramContainer, (node) => node.attributes['data-curve-source']);
    assert.deepEqual(
      curves().map((node) => node.attributes['data-curve-source']),
      ['preview', 'preview']
    );

    AppEvents.emit('freeform:authoritative', {
      cacheKey: getViewportStateCacheKey(GlobalState.get()),
      freeform: authoritativeFixture.freeform,
    });

    assert.deepEqual(
      curves().map((node) => node.attributes['data-curve-source']),
      ['authoritative', 'authoritative']
    );
    editor.drag = { guide: null };
    editor.draw();
    assert.deepEqual(
      curves().map((node) => node.attributes['data-curve-source']),
      ['preview', 'preview']
    );
    assert.ok(curves().every((node) => node.className.includes('freeform-profile-curve-pending')));
    editor.drag = null;
    editor.draw();
    assert.equal(
      curves()[0].attributes.d,
      editor.curvePath(authoritativeFixture.freeform.curveSamples.H, editor.transforms)
    );
    const overlays = collectNodes(paramContainer, (node) =>
      (node.attributes.class || '').includes('freeform-profile-inflection-overlay')
    );
    assert.equal(overlays.length, 2);
    assert.deepEqual(
      overlays.map((node) => node.attributes['data-inflection-plane']),
      ['H', 'V']
    );
    const badge = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-profile-inflection-badge'
    )[0];
    assert.equal(badge.textContent, 'S-curve H 24.2° · V 4.2°');
    assert.match(badge.title, /H curve bellies 3\.0 mm from the polyline/);
    const deviation = collectNodes(
      paramContainer,
      (node) => node.className === 'freeform-profile-deviation-readout'
    )[0];
    assert.equal(deviation.textContent, 'Belly H 3.0 · V 2.5 mm');
    assert.match(deviation.title, /V curve bellies 2\.5 mm from the polyline/);
  });
});

test('FREEFORM profile editor ignores authoritative payloads with stale viewport cache keys', () => {
  withFreeformPanel(authoritativeFixture.params, ({ paramContainer, editor }) => {
    AppEvents.emit('freeform:authoritative', {
      cacheKey: `${getViewportStateCacheKey(GlobalState.get())}:stale`,
      freeform: authoritativeFixture.freeform,
    });

    assert.equal(editor.authoritative, null);
    assert.deepEqual(
      collectNodes(paramContainer, (node) => node.attributes['data-curve-source']).map(
        (node) => node.attributes['data-curve-source']
      ),
      ['preview', 'preview']
    );
    assert.equal(
      collectNodes(paramContainer, (node) =>
        (node.attributes.class || '').includes('freeform-profile-inflection-overlay')
      ).length,
      0
    );
  });
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

test('ParamPanel conversion dialog commits converted FREEFORM params in one update', async () => {
  const originalDocument = global.document;
  const previousState = JSON.parse(JSON.stringify(GlobalState.get()));
  const fakeDocument = new FakeDocument();
  const paramContainer = fakeDocument.createElement('div');
  paramContainer.id = 'param-container';
  fakeDocument.body.appendChild(paramContainer);
  global.document = fakeDocument;

  let summary = '';
  const cachedViewportMesh = { grid: { marker: 'cached' }, preparedParams: { scale: 1 } };
  try {
    GlobalState.loadState(
      { type: 'OSSE', params: getDefaults('OSSE') },
      'param-panel-convert-test'
    );
    const historyBefore = GlobalState.undoStack.length;
    const panel = new ParamPanel('param-container', {
      getCachedViewportMesh: () => cachedViewportMesh,
      convertCurrentDesign: async (state, options) => {
        assert.equal(state.type, 'OSSE');
        assert.equal(options.viewportMesh, cachedViewportMesh);
        return {
          params: {
            ...getDefaults('FREEFORM'),
            length: 88,
            throatRadius: 11,
            interiorH: [{ z: 44, r: 30, angleDeg: null, strength: null }],
          },
          report: {
            maxDeviationMmH: 0.08,
            maxDeviationMmV: 0.11,
            truncatedMm: 34.9,
            anchorCountH: 1,
            anchorCountV: 0,
          },
        };
      },
      showConversionDialog: async ({ convertCurrentDesign }) => {
        const conversion = await convertCurrentDesign();
        summary = conversion.summary;
        return { action: 'convert', conversion };
      },
    });
    panel.createFullPanel();

    const convertButtons = collectNodes(
      paramContainer,
      (node) => node.tagName === 'BUTTON' && node.textContent === 'Convert to FREEFORM'
    );
    assert.equal(convertButtons.length, 1);
    const typeSelect = fakeDocument.getElementById('model-type');
    typeSelect.value = 'FREEFORM';
    await typeSelect.onchange({ target: typeSelect });

    assert.equal(GlobalState.get().type, 'FREEFORM');
    assert.equal(GlobalState.get().params.length, 88);
    assert.equal(GlobalState.get().params.throatRadius, 11);
    assert.equal(GlobalState.undoStack.length, Math.min(50, historyBefore + 1));
    assert.equal(summary, 'max deviation H 0.08 mm, V 0.11 mm; rollback lip 34.9 mm dropped');
  } finally {
    GlobalState.loadState(previousState, 'param-panel-convert-test-restore');
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
