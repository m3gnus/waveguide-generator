import test from 'node:test';
import assert from 'node:assert/strict';

import { AppEvents } from '../src/events.js';
import { provideMeshForSimulation } from '../src/app/mesh.js';
import { validateCanonicalMeshPayload } from '../src/solver/index.js';
import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

function makePreparedParams(overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults('OSSE'),
      type: 'OSSE',
      L: '120',
      a: '45',
      a0: '15.5',
      r0: '12.7',
      angularSegments: 24,
      lengthSegments: 10,
      ...overrides
    },
    { type: 'OSSE', applyVerticalOffset: true }
  );
}

test('app mesh provider emits canonical payload from shared geometry artifacts pipeline', () => {
  const emitted = [];
  const originalEmit = AppEvents.emit;
  AppEvents.emit = (event, data) => {
    emitted.push({ event, data });
  };

  try {
    const prepared = makePreparedParams({ encDepth: 200, interfaceOffset: '8' });
    const app = {
      prepareParamsForMesh: () => prepared
    };

    provideMeshForSimulation(app);

    assert.equal(emitted.length, 1);
    assert.equal(emitted[0].event, 'simulation:mesh-ready');
    validateCanonicalMeshPayload(emitted[0].data);

    const expected = buildGeometryArtifacts(prepared, {
      includeEnclosure: Number(prepared.encDepth || 0) > 0
    }).simulation;

    assert.deepEqual(emitted[0].data.surfaceTags, expected.surfaceTags);
    assert.equal(emitted[0].data.indices.length, expected.indices.length);
    assert.equal(emitted[0].data.vertices.length, expected.vertices.length);
  } finally {
    AppEvents.emit = originalEmit;
  }
});
