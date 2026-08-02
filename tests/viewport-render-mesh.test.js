import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as THREE from 'three';

import { getDefaults } from '../src/config/defaults.js';
import { detachCreaseVertices } from '../src/app/viewportMesh.js';
import { renderModel } from '../src/app/scene.js';
import { getViewportStateCacheKey } from '../src/app/viewportCacheKey.js';
import { AppEvents } from '../src/events.js';
import {
  createAdaptiveRingVertices,
  createRingVertices,
} from '../src/geometry/engine/mesh/horn.js';
import { prepareViewportMesh, validateViewportMesh } from '../src/modules/geometry/useCases.js';

const authoritativeFixture = JSON.parse(
  readFileSync(new URL('./fixtures/freeform-authoritative.json', import.meta.url), 'utf8')
);

function syntheticViewportGrid(rows) {
  const angles = [0, Math.PI / 2, Math.PI, (3 * Math.PI) / 2];
  const innerPoints = [];
  for (let index = 0; index < angles.length; index += 1) {
    const angle = angles[index];
    const source = index === 1 ? rows.V : rows.H;
    for (const [z, radius] of source) {
      innerPoints.push(radius * Math.cos(angle), radius * Math.sin(angle), z);
    }
  }
  return {
    angle_list: angles,
    grid_n_phi: angles.length,
    grid_n_length: rows.H.length - 1,
    inner_points: innerPoints,
    full_circle: true,
    quadrants: 1234,
    slice_map: Array.from({ length: rows.H.length }, (_, index) => index),
  };
}

async function waitFor(predicate, message, timeoutMs = 1000) {
  const deadline = Date.now() + timeoutMs;
  while (!predicate()) {
    if (Date.now() >= deadline) throw new Error(message);
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
}

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

test('FREEFORM viewport is server-only and never falls into the local OSSE engine', () => {
  const mesh = prepareViewportMesh({
    type: 'FREEFORM',
    params: { ...getDefaults('FREEFORM'), type: 'FREEFORM' },
  });

  assert.equal(mesh.serverOnly, true);
  assert.deepEqual(mesh.vertices, []);
  assert.deepEqual(mesh.indices, []);
  assert.equal(mesh.preparedParams.type, 'FREEFORM');
});

test('backend FREEFORM build publishes authoritative metadata with its viewport cache key', async () => {
  const currentState = {
    type: 'FREEFORM',
    params: { ...getDefaults('FREEFORM'), ...authoritativeFixture.params, type: 'FREEFORM' },
  };
  const cacheKey = getViewportStateCacheKey(currentState);
  const freeform = authoritativeFixture.freeform;
  const grid = syntheticViewportGrid({
    H: [freeform.curveSamples.H[0], freeform.curveSamples.H.at(-1)],
    V: [freeform.curveSamples.V[0], freeform.curveSamples.V.at(-1)],
  });
  const app = {
    scene: new THREE.Scene(),
    renderer: {},
    currentState,
    uiCoordinator: { readDisplayModeSetting: () => 'wireframe' },
    stats: { innerText: '' },
  };
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    return {
      ok: true,
      json: async () => ({
        formula: 'FREEFORM',
        mode: 'bare',
        params: currentState.params,
        grid,
        enclosure: null,
        metadata: { freeform },
      }),
    };
  };
  let received = null;
  const onAuthoritative = (payload) => {
    received = payload;
  };
  AppEvents.on('freeform:authoritative', onAuthoritative);
  try {
    renderModel(app);
    await waitFor(() => received !== null, 'authoritative viewport event timed out');
    assert.equal(fetchCount, 1);
    assert.deepEqual(received, { cacheKey, freeform });
    assert.equal(app._meshCache.grid.source, 'backend');
    assert.equal(app._currentMeshVariant, 'grid');
  } finally {
    AppEvents.off('freeform:authoritative', onAuthoritative);
    globalThis.fetch = originalFetch;
  }
});

test('changed FREEFORM state refetches immediately after a backend failure', async () => {
  const firstState = {
    type: 'FREEFORM',
    params: { ...getDefaults('FREEFORM'), ...authoritativeFixture.params, type: 'FREEFORM' },
  };
  const secondState = {
    type: 'FREEFORM',
    params: { ...firstState.params, mouthRadiusH: firstState.params.mouthRadiusH + 1 },
  };
  const freeform = authoritativeFixture.freeform;
  const grid = syntheticViewportGrid({
    H: [freeform.curveSamples.H[0], freeform.curveSamples.H.at(-1)],
    V: [freeform.curveSamples.V[0], freeform.curveSamples.V.at(-1)],
  });
  const app = {
    scene: new THREE.Scene(),
    renderer: {},
    currentState: firstState,
    uiCoordinator: {
      readDisplayModeSetting: () => 'wireframe',
      showError: () => {},
    },
    paramPanel: { setProfileError: () => {} },
    stats: { innerText: '' },
  };
  const originalFetch = globalThis.fetch;
  let fetchCount = 0;
  globalThis.fetch = async () => {
    fetchCount += 1;
    if (fetchCount === 1) {
      return {
        ok: false,
        status: 503,
        text: async () => 'temporary backend failure',
      };
    }
    return {
      ok: true,
      json: async () => ({
        formula: 'FREEFORM',
        mode: 'bare',
        params: secondState.params,
        grid,
        enclosure: null,
        metadata: { freeform },
      }),
    };
  };
  let received = null;
  const onAuthoritative = (payload) => {
    received = payload;
  };
  AppEvents.on('freeform:authoritative', onAuthoritative);
  try {
    renderModel(app);
    await waitFor(() => app._viewportFetch === null, 'failed viewport request did not settle');
    assert.equal(fetchCount, 1);

    app.currentState = secondState;
    renderModel(app);
    await waitFor(() => fetchCount === 2, 'changed FREEFORM state did not refetch');
    await waitFor(() => received !== null, 'refetched FREEFORM metadata was not published');
    assert.equal(received.cacheKey, getViewportStateCacheKey(secondState));
    assert.equal(received.freeform, freeform);
  } finally {
    AppEvents.off('freeform:authoritative', onAuthoritative);
    app._viewportFetch?.controller?.abort();
    clearTimeout(app._viewportCooldownRetryTimer);
    globalThis.fetch = originalFetch;
  }
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

test('crease detachment preserves smooth edges after neighboring vertices split', () => {
  const mesh = {
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0, -1, 0, 0, 0, 0, 1, 0, 1, 1],
    indices: [0, 1, 4, 0, 2, 1, 0, 3, 2, 2, 3, 5],
    groups: {},
  };

  const detached = detachCreaseVertices(mesh, 30);
  const firstSmoothTriangle = detached.indices.slice(3, 6);
  const secondSmoothTriangle = detached.indices.slice(6, 9);

  assert.equal(detached.vertices.length / 3, 10);
  assert.equal(firstSmoothTriangle[0], secondSmoothTriangle[0]);
  assert.equal(firstSmoothTriangle[1], secondSmoothTriangle[2]);
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
