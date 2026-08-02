import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { syncAppExportMenuCapabilities } from '../src/app/events.js';
import { getExportCapability } from '../src/modules/export/capabilities.js';
import { FREEFORM_NUMERIC_TOKEN, mapFreeformError } from '../src/ui/freeformErrorMapping.js';

const params = {
  ...getDefaults('FREEFORM'),
  length: 120,
  interiorH: [
    { z: 35, r: 42, angleDeg: null, strength: null },
    { z: 80, r: 90, angleDeg: null, strength: null },
  ],
  interiorV: [{ z: 60, r: 55, angleDeg: null, strength: null }],
  crossSections: [
    { t: 0, shape: 'circle' },
    { t: 0.5, shape: 'rounded_rectangle', cornerRadiusMm: 8 },
    { t: 1, shape: 'ellipse' },
  ],
};

test('FREEFORM mesher station locators map to the indexed station', () => {
  const detail =
    'FREEFORM crossSections[1].cornerRadiusMm must be in [1.1, 55] mm at station t=0.5, got 1 mm';
  const mapping = mapFreeformError(detail, params);

  assert.deepEqual(mapping.target, { kind: 'station', index: 1 });
  assert.match(mapping.message, /^Station 2:/);
  assert.equal(mapping.detail, detail);
});

test('FREEFORM mesher plane/anchor locators map wire anchors to editor anchors', () => {
  const detail =
    'FREEFORM profileH.points[2].angleDeg must be in (-90, 90) degrees for an interior anchor, got 95';
  const mapping = mapFreeformError(detail, params);

  assert.deepEqual(mapping.target, { kind: 'anchor', plane: 'H', anchorIndex: 2 });
  assert.match(mapping.message, /^H anchor 3:/);
});

test('FREEFORM mesher segment locators choose an editable anchor on that plane', () => {
  const detail =
    "FREEFORM profileV segment 0 radius overshoots its anchor range [12.7, 55] mm; set overshootPolicy='allow' to permit this intentionally";
  const mapping = mapFreeformError(detail, params);

  assert.deepEqual(mapping.target, { kind: 'anchor', plane: 'V', anchorIndex: 1 });
  assert.match(mapping.message, /^V curve near anchor 2:/);
});

test('FREEFORM near-t convexity errors map t-valued spans to their active station', () => {
  const spanParams = {
    ...params,
    crossSections: [
      { t: 0, shape: 'circle' },
      { t: 0.2, shape: 'ellipse' },
      { t: 1, shape: 'ellipse' },
    ],
  };
  const first = mapFreeformError(
    'FREEFORM crossSections span 0..0.2 produces a non-convex outline near t=0.1',
    spanParams
  );
  const second = mapFreeformError(
    'FREEFORM crossSections span 0.2..1 produces a non-convex outline near t=0.35',
    spanParams
  );

  assert.deepEqual(first.target, { kind: 'station', index: 1 });
  assert.deepEqual(second.target, { kind: 'station', index: 2 });
  assert.match(first.message, /^Station 2:/);
  assert.match(second.message, /^Station 3:/);
});

test('FREEFORM numeric diagnostics capture decimals, exponents, and signs in full', () => {
  const pattern = new RegExp(`^(${FREEFORM_NUMERIC_TOKEN})$`, 'i');
  for (const token of ['0.2', '1e-06', '-0.5']) {
    assert.equal(token.match(pattern)?.[1], token);
  }
});

test('FREEFORM t locators outside the active station span and unmatched detail stay generic', () => {
  assert.equal(
    mapFreeformError(
      'FREEFORM crossSections span 0.5..1 produces a non-convex outline near t=0.25',
      params
    ),
    null
  );
  assert.equal(mapFreeformError('FREEFORM params must be a mapping', params), null);
});

function createExportMenuFixture() {
  const documentRef = {
    createElement() {
      return createElement('', documentRef);
    },
  };
  const items = ['mwg_config', 'step', 'stl', 'fusion_csv'].map((format) =>
    createElement(format, documentRef)
  );
  return {
    items,
    root: {
      querySelectorAll(selector) {
        return selector === '[data-app-export-format]' ? items : [];
      },
    },
  };
}

function createElement(format, ownerDocument) {
  const attributes = {};
  const children = [];
  const element = {
    ownerDocument,
    dataset: format ? { appExportFormat: format } : {},
    attributes,
    children,
    disabled: false,
    className: '',
    textContent: '',
    title: '',
    setAttribute(name, value) {
      attributes[name] = String(value);
    },
    getAttribute(name) {
      return attributes[name] ?? null;
    },
    removeAttribute(name) {
      delete attributes[name];
    },
    appendChild(child) {
      child.parentNode = element;
      children.push(child);
      return child;
    },
    querySelector(selector) {
      return selector === '.export-menu-item-reason'
        ? children.find((child) => child.className === 'export-menu-item-reason') || null
        : null;
    },
    remove() {
      if (!element.parentNode) return;
      const index = element.parentNode.children.indexOf(element);
      if (index >= 0) element.parentNode.children.splice(index, 1);
      element.parentNode = null;
    },
  };
  return element;
}

test('FREEFORM export capability map disables local geometry menu items with visible reasons', () => {
  const { root, items } = createExportMenuFixture();
  syncAppExportMenuCapabilities(root, { type: 'FREEFORM', params });
  const byFormat = Object.fromEntries(items.map((item) => [item.dataset.appExportFormat, item]));

  assert.equal(byFormat.mwg_config.disabled, false);
  assert.equal(byFormat.step.disabled, false);
  for (const format of ['stl', 'fusion_csv']) {
    assert.equal(byFormat[format].disabled, true);
    assert.equal(byFormat[format].attributes['aria-disabled'], 'true');
    assert.match(byFormat[format].title, /use STEP export.*\.mwg config/i);
    assert.match(byFormat[format].children[0].textContent, /Not available for FREEFORM yet/);
  }
  assert.equal(getExportCapability('FREEFORM', 'step').available, true);

  syncAppExportMenuCapabilities(root, { type: 'OSSE', params: getDefaults('OSSE') });
  assert.equal(byFormat.stl.disabled, false);
  assert.equal(byFormat.stl.children.length, 0);
  assert.equal(byFormat.fusion_csv.disabled, false);
});
