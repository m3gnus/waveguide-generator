import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';
import { analyzeBemMeshIntegrity } from '../src/geometry/meshIntegrity.js';

function prepare(type, overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults(type),
      type,
      angularSegments: 64,
      lengthSegments: 20,
      quadrants: '1234',
      ...overrides
    },
    { type }
  );
}

function buildMesh(params, options = {}) {
  return buildGeometryArtifacts(params, options).mesh;
}

function radiusAt(vertices, idx) {
  const x = vertices[idx * 3];
  const z = vertices[idx * 3 + 2];
  return Math.hypot(x, z);
}

test('bare horn build keeps only inner horn surfaces', () => {
  const params = prepare('OSSE', {
    encDepth: 0,
    wallThickness: 0
  });
  const mesh = buildMesh(params, { includeEnclosure: false });

  assert.ok(mesh.groups?.horn, 'horn group should exist');
  assert.ok(mesh.groups?.inner_wall, 'inner wall group should exist');
  assert.ok(mesh.groups?.throat_disc, 'throat disc group should exist');
  assert.equal(mesh.groups?.freestandingWall, undefined);
  assert.equal(mesh.groups?.enclosure, undefined);
});

test('freestanding thickened OSSE build emits explicit wall surfaces', () => {
  const params = prepare('OSSE', {
    encDepth: 0,
    wallThickness: 8
  });
  const mesh = buildMesh(params, { includeEnclosure: false });

  assert.ok(mesh.groups?.freestandingWall, 'freestanding wall group should exist');
  assert.ok(mesh.groups?.outer_wall, 'outer wall group should exist');
  assert.ok(mesh.groups?.mouth_rim, 'mouth rim group should exist');
  assert.ok(mesh.groups?.throat_return, 'throat return group should exist');
  assert.ok(mesh.groups?.rear_cap, 'rear cap group should exist');
  assert.ok(mesh.groups?.throat_disc, 'throat disc group should exist');
  assert.equal(mesh.groups?.enclosure, undefined);
});

test('freestanding thickened R-OSSE build emits explicit wall surfaces', () => {
  const params = prepare('R-OSSE', {
    encDepth: 0,
    wallThickness: 6
  });
  const mesh = buildMesh(params, { includeEnclosure: false });

  assert.ok(mesh.groups?.freestandingWall, 'freestanding wall group should exist');
  assert.ok(mesh.groups?.outer_wall, 'outer wall group should exist');
  assert.ok(mesh.groups?.mouth_rim, 'mouth rim group should exist');
  assert.ok(mesh.groups?.throat_return, 'throat return group should exist');
  assert.ok(mesh.groups?.rear_cap, 'rear cap group should exist');
  assert.ok(mesh.groups?.throat_disc, 'throat disc group should exist');
  assert.equal(mesh.groups?.enclosure, undefined);
});

test('freestanding wall thickness stays one true 3D offset away from the inner surface', () => {
  const thickness = 7;
  const params = prepare('OSSE', {
    encDepth: 0,
    wallThickness: thickness
  });
  const mesh = buildMesh(params, { includeEnclosure: false });

  const ringCount = mesh.ringCount;
  const lengthSteps = Number(params.lengthSegments);
  const innerVertexCount = (lengthSteps + 1) * ringCount;
  const outerStart = innerVertexCount;

  for (let row = 1; row <= lengthSteps; row += 1) {
    for (let col = 0; col < ringCount; col += 1) {
      const idx = row * ringCount + col;
      const innerX = mesh.vertices[idx * 3];
      const innerY = mesh.vertices[idx * 3 + 1];
      const innerZ = mesh.vertices[idx * 3 + 2];
      const outerX = mesh.vertices[(outerStart + idx) * 3];
      const outerY = mesh.vertices[(outerStart + idx) * 3 + 1];
      const outerZ = mesh.vertices[(outerStart + idx) * 3 + 2];
      const offset = Math.hypot(outerX - innerX, outerY - innerY, outerZ - innerZ);

      assert.ok(
        Math.abs(offset - thickness) < 2e-4,
        `3D offset should equal wall thickness (row=${row}, col=${col})`
      );
    }
  }
});

test('freestanding wall mesh remains closed and manifold', () => {
  const params = prepare('R-OSSE', {
    encDepth: 0,
    wallThickness: 8
  });
  const mesh = buildMesh(params, { includeEnclosure: false });

  const integrity = analyzeBemMeshIntegrity(mesh.vertices, mesh.indices, {
    requireClosed: true,
    requireSingleComponent: true
  });

  assert.equal(integrity.boundaryEdges, 0);
  assert.equal(integrity.nonManifoldEdges, 0);
  assert.equal(integrity.sameDirectionSharedEdges, 0);
  assert.equal(integrity.duplicateTrianglesByIndex, 0);
  assert.equal(integrity.duplicateTrianglesByGeometry, 0);
});

test('rear transition continues the outer back-side slope into the back plate instead of a cylinder', () => {
  for (const type of ['OSSE', 'R-OSSE']) {
    const thickness = 8;
    const params = prepare(type, {
      encDepth: 0,
      wallThickness: thickness
    });
    const mesh = buildMesh(params, { includeEnclosure: false });

    const ringCount = mesh.ringCount;
    const lengthSteps = Number(params.lengthSegments);
    const innerVertexCount = (lengthSteps + 1) * ringCount;
    const outerStart = innerVertexCount;
    const rearRimStart = outerStart + innerVertexCount;

    let throatY = 0;
    for (let col = 0; col < ringCount; col += 1) {
      throatY += mesh.vertices[col * 3 + 1];
    }
    throatY /= ringCount;
    const rearY = throatY - thickness;

    let maxRearShift = 0;
    for (let col = 0; col < ringCount; col += 1) {
      const innerIdx = col;
      const throatOuterIdx = outerStart + col;
      const nextOuterIdx = outerStart + ringCount + col;
      const rearIdx = rearRimStart + col;

      const innerY = mesh.vertices[innerIdx * 3 + 1];
      const throatOuterY = mesh.vertices[throatOuterIdx * 3 + 1];
      const rearOuterY = mesh.vertices[rearIdx * 3 + 1];
      assert.ok(Math.abs(throatOuterY - innerY) < 1e-6, 'outer throat ring should keep throat axial station');
      assert.ok(Math.abs(rearOuterY - rearY) < 1e-6, 'rear rim should lie on the back plate plane');

      const radialDelta = radiusAt(mesh.vertices, throatOuterIdx) - radiusAt(mesh.vertices, innerIdx);
      assert.ok(Math.abs(radialDelta - thickness) < 1e-3, 'outer throat ring should be one thickness radially outward');

      const x0 = mesh.vertices[throatOuterIdx * 3];
      const y0 = mesh.vertices[throatOuterIdx * 3 + 1];
      const z0 = mesh.vertices[throatOuterIdx * 3 + 2];
      const x1 = mesh.vertices[nextOuterIdx * 3];
      const y1 = mesh.vertices[nextOuterIdx * 3 + 1];
      const z1 = mesh.vertices[nextOuterIdx * 3 + 2];
      const t = (rearY - y0) / (y1 - y0);

      const expectedX = x0 + (x1 - x0) * t;
      const expectedZ = z0 + (z1 - z0) * t;
      const rearX = mesh.vertices[rearIdx * 3];
      const rearZ = mesh.vertices[rearIdx * 3 + 2];

      assert.ok(Math.abs(rearX - expectedX) < 1e-5, `rear rim x should continue the local back-side slope (${type}, col=${col})`);
      assert.ok(Math.abs(rearZ - expectedZ) < 1e-5, `rear rim z should continue the local back-side slope (${type}, col=${col})`);

      maxRearShift = Math.max(maxRearShift, Math.hypot(rearX - x0, rearZ - z0));
    }

    assert.ok(maxRearShift > 0.5, `${type} rear transition should not degenerate to a cylinder`);
  }
});
