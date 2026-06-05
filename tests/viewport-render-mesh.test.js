import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { detachCreaseVertices } from '../src/app/viewportMesh.js';
import {
  prepareViewportMesh,
  validateViewportMesh
} from '../src/modules/geometry/useCases.js';

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
      ...overrides
    }
  };
}

test('viewport mesh variants keep wire grid sparse and shaded surface denser', () => {
  const state = makeState();
  const grid = prepareViewportMesh(state, { variant: 'grid' });
  const smooth = prepareViewportMesh(state, { variant: 'smooth' });

  assert.equal(grid.variant, 'grid');
  assert.equal(smooth.variant, 'smooth');
  assert.equal(grid.preparedParams.lengthSegments, 9);
  assert.equal(smooth.preparedParams.lengthSegments, 9);
  assert.equal(grid.preparedParams.angularSegments, 36);
  assert.equal(smooth.preparedParams.angularSegments, 36);
  assert.ok(
    smooth.indices.length > grid.indices.length,
    'smooth viewport mesh should use a denser render-only tessellation'
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

test('crease detachment splits hard edges without relying on group metadata', () => {
  const mesh = {
    vertices: [
      0, 0, 0,
      1, 0, 0,
      0, 1, 0,
      0, 0, 1
    ],
    indices: [0, 1, 2, 0, 3, 1],
    groups: {}
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
