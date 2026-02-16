import test from 'node:test';
import assert from 'node:assert/strict';

import { AppEvents } from '../src/events.js';
import { provideMeshForSimulation } from '../src/app/mesh.js';
import { validateCanonicalMeshPayload } from '../src/solver/index.js';
import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

function makePreparedParams(overrides = {}) {
  return {
    ...getDefaults('OSSE'),
    type: 'OSSE',
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    angularSegments: 24,
    lengthSegments: 10,
    ...overrides
  };
}

test('app mesh provider emits canonical payload from shared geometry artifacts pipeline', () => {
  const emitted = [];
  const originalEmit = AppEvents.emit;
  AppEvents.emit = (event, data) => {
    emitted.push({ event, data });
  };

  try {
    const preparedInput = makePreparedParams({ encDepth: 200, interfaceOffset: '8', quadrants: '1' });
    const app = {
      prepareParamsForMesh: (options = {}) => prepareGeometryParams(preparedInput, { type: 'OSSE', ...options })
    };

    provideMeshForSimulation(app);

    assert.equal(emitted.length, 1);
    assert.equal(emitted[0].event, 'simulation:mesh-ready');
    validateCanonicalMeshPayload(emitted[0].data);

    const expectedPrepared = prepareGeometryParams(preparedInput, {
      type: 'OSSE',
      applyVerticalOffset: true,
      forceFullQuadrants: true
    });
    const expected = buildGeometryArtifacts(expectedPrepared, {
      includeEnclosure: Number(expectedPrepared.encDepth || 0) > 0
    }).simulation;

    assert.equal(emitted[0].data.metadata.fullCircle, true);
    assert.equal(emitted[0].data.metadata.splitPlaneTrianglesRemoved, 0);
    assert.deepEqual(emitted[0].data.surfaceTags, expected.surfaceTags);
    assert.equal(emitted[0].data.indices.length, expected.indices.length);
    assert.equal(emitted[0].data.vertices.length, expected.vertices.length);
  } finally {
    AppEvents.emit = originalEmit;
  }
});
