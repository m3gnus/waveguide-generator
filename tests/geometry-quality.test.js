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

test('rear transition continues the outer back-side slope into a circular back plate', () => {
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

    const throatOuterRadii = [];
    const nextOuterRadii = [];
    let throatOuterY = 0;
    let nextOuterY = 0;
    for (let col = 0; col < ringCount; col += 1) {
      const throatOuterIdx = outerStart + col;
      const nextOuterIdx = outerStart + ringCount + col;
      throatOuterRadii.push(radiusAt(mesh.vertices, throatOuterIdx));
      nextOuterRadii.push(radiusAt(mesh.vertices, nextOuterIdx));
      throatOuterY += mesh.vertices[throatOuterIdx * 3 + 1];
      nextOuterY += mesh.vertices[nextOuterIdx * 3 + 1];
    }
    throatOuterY /= ringCount;
    nextOuterY /= ringCount;

    const avgThroatOuterRadius =
      throatOuterRadii.reduce((sum, radius) => sum + radius, 0) / ringCount;
    const avgNextOuterRadius = nextOuterRadii.reduce((sum, radius) => sum + radius, 0) / ringCount;
    const radiusT = (rearY - throatOuterY) / (nextOuterY - throatOuterY);
    const expectedRearRadius =
      avgThroatOuterRadius + (avgNextOuterRadius - avgThroatOuterRadius) * radiusT;
    const rearRadii = [];

    for (let col = 0; col < ringCount; col += 1) {
      const innerIdx = col;
      const throatOuterIdx = outerStart + col;
      const rearIdx = rearRimStart + col;

      const innerY = mesh.vertices[innerIdx * 3 + 1];
      const rearOuterY = mesh.vertices[rearIdx * 3 + 1];
      assert.ok(
        Math.abs(mesh.vertices[throatOuterIdx * 3 + 1] - innerY) < 1e-6,
        'outer throat ring should keep throat axial station'
      );
      assert.ok(Math.abs(rearOuterY - rearY) < 1e-6, 'rear rim should lie on the back plate plane');

      const radialDelta = radiusAt(mesh.vertices, throatOuterIdx) - radiusAt(mesh.vertices, innerIdx);
      assert.ok(Math.abs(radialDelta - thickness) < 1e-3, 'outer throat ring should be one thickness radially outward');

      rearRadii.push(radiusAt(mesh.vertices, rearIdx));
    }

    const minRearRadius = Math.min(...rearRadii);
    const maxRearRadius = Math.max(...rearRadii);
    assert.ok(maxRearRadius - minRearRadius < 1e-6, `${type} rear cap rim should be circular`);
    assert.ok(
      Math.abs(rearRadii[0] - expectedRearRadius) < 1e-4,
      `${type} rear cap radius should continue the average back-side slope`
    );
    assert.ok(
      Math.abs(expectedRearRadius - avgThroatOuterRadius) > 0.5,
      `${type} rear transition should not degenerate to a cylinder`
    );
  }
});
