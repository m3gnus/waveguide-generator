import test from 'node:test';
import assert from 'node:assert/strict';

import { provideMeshForSimulation } from '../src/app/mesh.js';
import { GlobalState } from '../src/state.js';
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
  let publishedPayload = null;
  const originalGet = GlobalState.get;

  const preparedInput = makePreparedParams({ encDepth: 200, quadrants: '1' });
  const app = {
    publishSimulationMesh(payload) {
      publishedPayload = payload;
      return payload;
    },
    publishSimulationMeshError() {
      throw new Error('unexpected mesh error');
    }
  };

  GlobalState.get = () => ({
    type: 'OSSE',
    params: preparedInput
  });

  try {
    provideMeshForSimulation(app);
  } finally {
    GlobalState.get = originalGet;
  }

  assert.ok(publishedPayload);
  validateCanonicalMeshPayload(publishedPayload);

  const expectedPrepared = prepareGeometryParams(preparedInput, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const expected = buildGeometryArtifacts(expectedPrepared, {
    includeEnclosure: Number(expectedPrepared.encDepth || 0) > 0
  }).simulation;

  assert.equal(publishedPayload.metadata.fullCircle, true);
  assert.deepEqual(publishedPayload.surfaceTags, expected.surfaceTags);
  assert.equal(publishedPayload.indices.length, expected.indices.length);
  assert.equal(publishedPayload.vertices.length, expected.vertices.length);
});

test('app mesh provider emits explicit simulation:mesh-error on generation failure', () => {
  let errorMessage = null;
  const originalGet = GlobalState.get;

  const app = {
    publishSimulationMesh() {
      throw new Error('unexpected mesh success');
    },
    publishSimulationMeshError(message) {
      errorMessage = message;
      return null;
    }
  };

  GlobalState.get = () => {
    throw new Error('intentional mesh setup failure');
  };

  try {
    provideMeshForSimulation(app);
  } finally {
    GlobalState.get = originalGet;
  }

  assert.match(errorMessage, /intentional mesh setup failure/);
});
