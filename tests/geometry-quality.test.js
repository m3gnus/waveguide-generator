import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';
import { analyzeBemMeshIntegrity } from '../src/geometry/meshIntegrity.js';
import { validateMeshQuality } from '../src/geometry/quality.js';

function makePreparedParams(overrides = {}) {
  return prepareGeometryParams(
    {
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
    },
    { type: 'OSSE' }
  );
}

test('freestanding wall thickness adds shell and rear disc behind throat', () => {
  const baseParams = makePreparedParams({
    encDepth: 0,
    wallThickness: 0,
    quadrants: '1234'
  });
  const thickParams = makePreparedParams({
    encDepth: 0,
    wallThickness: 8,
    quadrants: '1234'
  });

  const base = buildGeometryArtifacts(baseParams, { includeEnclosure: false }).mesh;
  const thick = buildGeometryArtifacts(thickParams, { includeEnclosure: false }).mesh;

  assert.ok(thick.vertices.length > base.vertices.length, 'wall thickness should add vertices');
  assert.ok(thick.indices.length > base.indices.length, 'wall thickness should add triangles');
  assert.ok(thick.groups?.freestandingWall, 'freestanding wall group should be present');

  const innerVertexCount = (Number(thickParams.lengthSegments) + 1) * thick.ringCount;
  const outerStart = innerVertexCount;
  for (let idx = thick.ringCount; idx < innerVertexCount; idx += 1) {
    const ix = thick.vertices[idx * 3];
    const iy = thick.vertices[idx * 3 + 1];
    const iz = thick.vertices[idx * 3 + 2];
    const ox = thick.vertices[(outerStart + idx) * 3];
    const oy = thick.vertices[(outerStart + idx) * 3 + 1];
    const oz = thick.vertices[(outerStart + idx) * 3 + 2];
    const d = Math.hypot(ox - ix, oy - iy, oz - iz);
    assert.ok(Math.abs(d - 8) < 1e-5, 'outer wall should stay one thickness away from original surface');
  }

  const throatY = thick.vertices[1];
  const targetRearY = throatY - 8;
  for (let i = 0; i < thick.ringCount; i += 1) {
    const rearRingY = thick.vertices[(outerStart + i) * 3 + 1];
    assert.ok(Math.abs(rearRingY - throatY) < 1e-6); // Outer throat ring is at throatY
  }
  // The rear disc rim starts after the outer shell
  const radialSteps = thick.ringCount;
  const lengthSteps = Number(thickParams.lengthSegments);
  const outerShellVerts = (lengthSteps + 1) * radialSteps;
  const discRimStart = outerStart + outerShellVerts;
  for (let i = 0; i < thick.ringCount; i += 1) {
    const rimY = thick.vertices[(discRimStart + i) * 3 + 1];
    assert.ok(Math.abs(rimY - targetRearY) < 1e-6);
  }

  const integrity = analyzeBemMeshIntegrity(thick.vertices, thick.indices, {
    requireClosed: true,
    requireSingleComponent: true
  });
  assert.equal(integrity.boundaryEdges, 0);
  assert.equal(integrity.nonManifoldEdges, 0);
  assert.equal(integrity.sameDirectionSharedEdges, 0);
  assert.equal(integrity.duplicateTrianglesByIndex, 0);
  assert.equal(integrity.duplicateTrianglesByGeometry, 0);
});

test('wall thickness is ignored when enclosure depth is enabled', () => {
  const params = makePreparedParams({
    encDepth: 240,
    wallThickness: 10,
    quadrants: '1234'
  });

  const mesh = buildGeometryArtifacts(params, { includeEnclosure: true }).mesh;
  assert.ok(mesh.groups?.enclosure, 'enclosure group should exist');
  assert.equal(mesh.groups?.freestandingWall, undefined, 'freestanding wall group should be absent with enclosure');
});

test('mesh quality validator reports healthy enclosure mesh', () => {
  const params = makePreparedParams({
    encDepth: 240,
    wallThickness: 8,
    quadrants: '1234'
  });

  const mesh = buildGeometryArtifacts(params, { includeEnclosure: true }).mesh;
  const quality = validateMeshQuality(mesh.vertices, mesh.indices, mesh.groups);

  assert.equal(quality.degenerateTriangles, 0);
  assert.equal(quality.nonManifoldEdges, 0);
  assert.equal(quality.seam.sameDirection, 0);
  assert.ok(quality.seam.shared > 0);
  assert.ok(quality.components >= 1);
  assert.ok(quality.sourceConnectivity > 0);
});
