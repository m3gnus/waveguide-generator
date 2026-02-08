import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { buildCanonicalMeshPayload, SURFACE_TAGS } from '../src/simulation/payload.js';

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
});

test('interface mode emits secondary and interface tags', () => {
  const params = makeBaseParams({
    encDepth: 260,
    interfaceOffset: '12',
    quadrants: '1',
    wallThickness: 5
  });
  const payload = quietBuild(params, { includeEnclosure: true });
  assert.equal(payload.metadata.interfaceEnabled, true);
  assert.ok(payload.metadata.tagCounts[SURFACE_TAGS.SECONDARY] > 0);
  assert.ok(payload.metadata.tagCounts[SURFACE_TAGS.INTERFACE] > 0);
});

test('enclosure without interface keeps enclosure surfaces as wall tags', () => {
  const params = makeBaseParams({
    encDepth: 260,
    interfaceOffset: '',
    quadrants: '1234',
    wallThickness: 5
  });
  const payload = quietBuild(params, { includeEnclosure: true });
  assert.equal(payload.metadata.interfaceEnabled, false);
  assert.equal(payload.metadata.tagCounts[SURFACE_TAGS.SECONDARY], 0);
  assert.equal(payload.metadata.tagCounts[SURFACE_TAGS.INTERFACE], 0);
});

test('freestanding export does not force rear closure', () => {
  const params = makeBaseParams({
    encDepth: 0,
    wallThickness: 8
  });
  const payload = quietBuild(params, { includeEnclosure: false });
  assert.equal(payload.metadata.rearClosureForced, false);
});

test('enclosure mode does not force rear closure', () => {
  const params = makeBaseParams({
    encDepth: 220,
    wallThickness: 8
  });
  const payload = quietBuild(params, { includeEnclosure: true });
  assert.equal(payload.metadata.rearClosureForced, false);
});
