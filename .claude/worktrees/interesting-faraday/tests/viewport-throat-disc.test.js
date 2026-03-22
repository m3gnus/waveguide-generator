import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';
import { detachThroatDiscVertices } from '../src/app/viewportMesh.js';

function buildViewportMesh(type, overrides = {}) {
  const prepared = prepareGeometryParams(
    {
      ...getDefaults(type),
      type,
      angularSegments: 48,
      lengthSegments: 16,
      quadrants: '1234',
      encDepth: 0,
      wallThickness: 0,
      ...overrides
    },
    {
      type,
      applyVerticalOffset: true
    }
  );

  return buildGeometryArtifacts(prepared, {
    includeEnclosure: false,
    adaptivePhi: false
  }).mesh;
}

function collectGroupVertices(indices, range) {
  const vertices = new Set();
  for (let triangleIndex = range.start; triangleIndex < range.end; triangleIndex += 1) {
    const base = triangleIndex * 3;
    vertices.add(indices[base]);
    vertices.add(indices[base + 1]);
    vertices.add(indices[base + 2]);
  }
  return vertices;
}

test('viewport throat-disc detachment isolates source-cap vertices from the horn wall', () => {
  const mesh = buildViewportMesh('R-OSSE', {
    R: '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)',
    a: '25 * (abs(cos(p)/1.2)^4 + abs(sin(p)/1)^3)^(-1/2.5)',
    a0: 15.5,
    r0: 12.7,
    k: 2.2,
    m: 0.85,
    b: '0.2',
    r: 0.4,
    q: 3.4,
    tmax: 1.0
  });

  const hornVerticesBefore = collectGroupVertices(mesh.indices, mesh.groups.horn);
  const throatVerticesBefore = collectGroupVertices(mesh.indices, mesh.groups.throat_disc);
  const sharedBefore = [...throatVerticesBefore].filter((index) => hornVerticesBefore.has(index));
  assert.ok(sharedBefore.length > 0, 'source cap should initially share vertices with the horn wall');

  const detached = detachThroatDiscVertices(mesh);
  const hornVerticesAfter = collectGroupVertices(detached.indices, detached.groups.horn);
  const throatVerticesAfter = collectGroupVertices(detached.indices, detached.groups.throat_disc);
  const sharedAfter = [...throatVerticesAfter].filter((index) => hornVerticesAfter.has(index));

  assert.equal(sharedAfter.length, 0);
  assert.equal(detached.indices.length, mesh.indices.length);
  assert.equal(detached.groups.throat_disc.start, mesh.groups.throat_disc.start);
  assert.equal(detached.groups.throat_disc.end, mesh.groups.throat_disc.end);
  assert.ok(detached.vertices.length > mesh.vertices.length);
  assert.ok(detached.detachedVertexCount >= 3);
});

test('viewport throat-disc detachment is a no-op when the mesh lacks a throat-disc group', () => {
  const mesh = buildViewportMesh('OSSE');
  const detached = detachThroatDiscVertices({
    ...mesh,
    groups: {
      ...mesh.groups,
      throat_disc: null
    }
  });

  assert.deepEqual(detached.vertices, mesh.vertices);
  assert.deepEqual(detached.indices, mesh.indices);
  assert.equal(detached.detachedVertexCount, 0);
});
