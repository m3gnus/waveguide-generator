import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { buildCanonicalMeshPayload, SURFACE_TAGS } from '../src/simulation/payload.js';

test('canonical mesh payload includes surface tags for every triangle', () => {
  const params = {
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
    angularSegments: 40,
    lengthSegments: 16,
    encDepth: 0,
    quadrants: '1234',
    wallThickness: 5,
    rearShape: 0
  };

  const originalWarn = console.warn;
  const originalError = console.error;
  let payload;
  try {
    console.warn = () => {};
    console.error = () => {};
    payload = buildCanonicalMeshPayload(params, { includeEnclosure: false });
  } finally {
    console.warn = originalWarn;
    console.error = originalError;
  }
  assert.ok(Array.isArray(payload.vertices));
  assert.ok(Array.isArray(payload.indices));
  assert.ok(Array.isArray(payload.surfaceTags));
  assert.equal(payload.surfaceTags.length, payload.indices.length / 3);
  assert.ok(payload.surfaceTags.includes(SURFACE_TAGS.SOURCE));
  assert.equal(payload.format, 'msh');
});
