import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { detachCreaseVertices } from '../src/app/viewportMesh.js';
import {
  createAdaptiveRingVertices,
  createRingVertices,
} from '../src/geometry/engine/mesh/horn.js';
import { prepareViewportMesh, validateViewportMesh } from '../src/modules/geometry/useCases.js';

function makeState(overrides = {}) {
  return {
    type: 'OSSE',
    params: {
      ...getDefaults('OSSE'),
      type: 'OSSE',
      angularSegments: 36,
      lengthSegments: 9,
      cornerSegments: 2,
      quadrants: '1234',
      encDepth: 0,
      wallThickness: 0,
      ...overrides,
    },
  };
}

test('viewport mesh variants use render-only tessellation instead of sparse mesh controls', () => {
  const state = makeState();
  const grid = prepareViewportMesh(state, { variant: 'grid' });
  const smooth = prepareViewportMesh(state, { variant: 'smooth' });
  const sparseHornTriangleCount = state.params.angularSegments * state.params.lengthSegments * 2;

  assert.equal(grid.variant, 'grid');
  assert.equal(smooth.variant, 'smooth');
  assert.equal(grid.preparedParams.lengthSegments, 9);
  assert.equal(smooth.preparedParams.lengthSegments, 9);
  assert.equal(grid.preparedParams.angularSegments, 36);
  assert.equal(smooth.preparedParams.angularSegments, 36);
  assert.ok(
    grid.indices.length / 3 > sparseHornTriangleCount,
    'grid viewport mesh should not expose sparse solve/export segment counts'
  );
  assert.ok(
    smooth.indices.length > grid.indices.length,
    'smooth viewport mesh should still use a denser render-only tessellation than grid'
  );
});

test('crease detach and viewport validation accept render-only smooth meshes', () => {
  const mesh = prepareViewportMesh(makeState(), { variant: 'smooth' });
  const detached = detachCreaseVertices(mesh);
  const validation = validateViewportMesh(detached);

  assert.equal(validation.ok, true);
  assert.equal(detached.indices.length, mesh.indices.length);
  assert.ok(detached.vertices.length >= mesh.vertices.length);
});

test('smooth viewport enclosure meshes can exceed argument-spread limits', () => {
  const mesh = prepareViewportMesh(
    makeState({
      encDepth: 220,
      angularSegments: 36,
      lengthSegments: 9,
      cornerSegments: 1,
    }),
    { variant: 'smooth' }
  );

  assert.ok(mesh.indices.length / 3 > 30000);
  assert.ok(mesh.groups.enclosure);
});

test('crease detachment splits hard edges without relying on group metadata', () => {
  const mesh = {
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1],
    indices: [0, 1, 2, 0, 3, 1],
    groups: {},
  };

  const detached = detachCreaseVertices(mesh, 30);

  assert.equal(detached.indices.length, mesh.indices.length);
  assert.ok(detached.vertices.length > mesh.vertices.length);
  assert.notDeepEqual(
    detached.indices.slice(0, 2),
    [detached.indices[3], detached.indices[5]],
    'the shared edge vertices should be duplicated across the hard crease'
  );
});

test('horn ring assembly reuses mouth profiles across axial slices', () => {
  let fixedEvaluations = 0;
  const fixedRingCount = 8;
  const fixedLengthSteps = 5;
  const fixedParams = {
    ...getDefaults('R-OSSE'),
    type: 'R-OSSE',
    morphTarget: 0,
    tmax: () => {
      fixedEvaluations += 1;
      return 1;
    },
  };
  const fixedAngles = Array.from(
    { length: fixedRingCount },
    (_, index) => (index / fixedRingCount) * Math.PI * 2
  );

  const fixedVertices = createRingVertices(
    fixedParams,
    null,
    fixedAngles,
    null,
    fixedRingCount,
    fixedLengthSteps,
    null
  );

  assert.equal(fixedVertices.length, (fixedLengthSteps + 1) * fixedRingCount * 3);
  // R-OSSE reads tmax once while validating and once while evaluating each
  // profile. There is one profile per vertex plus one cached mouth profile per angle.
  assert.equal(fixedEvaluations, (fixedLengthSteps + 2) * fixedRingCount * 2);

  let adaptiveEvaluations = 0;
  const adaptiveCounts = [8, 8, 12, 12, 12, 12];
  const adaptiveParams = {
    ...fixedParams,
    tmax: () => {
      adaptiveEvaluations += 1;
      return 1;
    },
  };

  const adaptiveVertices = createAdaptiveRingVertices(
    adaptiveParams,
    null,
    null,
    adaptiveCounts,
    adaptiveCounts.length - 1,
    null
  );

  const adaptiveVertexCount = adaptiveCounts.reduce((sum, count) => sum + count, 0);
  const uniqueMouthSamples = 8 + 12;
  assert.equal(adaptiveVertices.length, adaptiveVertexCount * 3);
  assert.equal(adaptiveEvaluations, (adaptiveVertexCount + uniqueMouthSamples) * 2);
});
