import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { buildCanonicalMeshPayload, SURFACE_TAGS } from '../src/simulation/payload.js';
import { assertBemMeshIntegrity } from '../src/geometry/meshIntegrity.js';

function quietBuild(params, options = {}) {
  const originalWarn = console.warn;
  const originalError = console.error;
  try {
    console.warn = () => {};
    console.error = () => {};
    return buildCanonicalMeshPayload(params, options);
  } finally {
    console.warn = originalWarn;
    console.error = originalError;
  }
}

function makeBaseParams(overrides = {}) {
  return {
    ...getDefaults('OSSE'),
    type: 'OSSE',
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    s: '0.6',
    n: 4.158,
    q: 0.991,
    k: 7,
    h: 0,
    angularSegments: 32,
    lengthSegments: 12,
    ...overrides
  };
}

test('canonical mesh payload includes surface tags and source coverage', () => {
  const params = {
    ...makeBaseParams(),
    encDepth: 0,
    quadrants: '1234',
    wallThickness: 5
  };

  const payload = quietBuild(params, { includeEnclosure: false });
  assert.ok(Array.isArray(payload.vertices));
  assert.ok(Array.isArray(payload.indices));
  assert.ok(Array.isArray(payload.surfaceTags));
  assert.equal(payload.surfaceTags.length, payload.indices.length / 3);
  assert.ok(payload.surfaceTags.includes(SURFACE_TAGS.SOURCE));
  assert.ok(payload.metadata.tagCounts[SURFACE_TAGS.SOURCE] > 0);
  assert.equal(payload.format, 'msh');
  assert.equal(
    Object.prototype.hasOwnProperty.call(payload.metadata, 'rearClosureForced'),
    false
  );
});

test('legacy interface params are ignored for enclosure payload tagging', () => {
  const params = makeBaseParams({
    encDepth: 260,
    subdomainSlices: '3',
    interfaceOffset: '12',
    interfaceDraw: '5',
    quadrants: '1',
    wallThickness: 5
  });
  const payload = quietBuild(params, { includeEnclosure: true });
  assert.equal(
    Object.prototype.hasOwnProperty.call(payload.metadata, 'interfaceEnabled'),
    false
  );
  assert.equal(payload.metadata.tagCounts[SURFACE_TAGS.SECONDARY], 0);
  assert.equal(payload.metadata.tagCounts[SURFACE_TAGS.INTERFACE], 0);
});

test('enclosure surfaces remain wall-tagged', () => {
  const params = makeBaseParams({
    encDepth: 260,
    quadrants: '1234',
    wallThickness: 5
  });
  const payload = quietBuild(params, { includeEnclosure: true });
  assert.equal(payload.metadata.tagCounts[SURFACE_TAGS.SECONDARY], 0);
  assert.equal(payload.metadata.tagCounts[SURFACE_TAGS.INTERFACE], 0);
});

test('integrity failures report explicit aggregated messages', () => {
  const vertices = [
    0, 0, 0,
    1, 0, 0,
    0, 1, 0,
    0, 0, 1
  ];
  const indices = [
    0, 1, 2,
    0, 1, 2,
    0, 1, 3
  ];

  assert.throws(
    () => assertBemMeshIntegrity(vertices, indices, { requireClosed: true, requireSingleComponent: true }),
    /BEM mesh integrity validation failed:/
  );
});
