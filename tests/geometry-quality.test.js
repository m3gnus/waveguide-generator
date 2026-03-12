import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

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

function collectVerticesForTriRange(indices, range) {
  const out = new Set();
  for (let t = range.start; t < range.end; t += 1) {
    const off = t * 3;
    out.add(indices[off]);
    out.add(indices[off + 1]);
    out.add(indices[off + 2]);
  }
  return out;
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

test('freestanding wall thickness is constant in each local axial/radial section', () => {
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

      const radialLen = Math.hypot(innerX, innerZ);
      if (radialLen <= 1e-9) continue;
      const rx = innerX / radialLen;
      const rz = innerZ / radialLen;

      const dx = outerX - innerX;
      const dy = outerY - innerY;
      const dz = outerZ - innerZ;
      const radialOffset = dx * rx + dz * rz;
      const localSectionOffset = Math.hypot(dy, radialOffset);

      assert.ok(
        Math.abs(localSectionOffset - thickness) < 2e-4,
        `local-section offset should equal wall thickness (row=${row}, col=${col})`
      );
    }
  }
});

test('throat outer ring is radially offset at same axial station and rear cap sits one thickness back', () => {
  const thickness = 8;
  const params = prepare('OSSE', {
    encDepth: 0,
    wallThickness: thickness
  });
  const mesh = buildMesh(params, { includeEnclosure: false });

  const ringCount = mesh.ringCount;
  const lengthSteps = Number(params.lengthSegments);
  const outerStart = (lengthSteps + 1) * ringCount;

  let throatY = 0;
  for (let col = 0; col < ringCount; col += 1) {
    throatY += mesh.vertices[col * 3 + 1];
  }
  throatY /= ringCount;
  const rearY = throatY - thickness;

  for (let col = 0; col < ringCount; col += 1) {
    const innerIdx = col;
    const outerIdx = outerStart + col;
    const innerY = mesh.vertices[innerIdx * 3 + 1];
    const outerY = mesh.vertices[outerIdx * 3 + 1];
    assert.ok(Math.abs(outerY - innerY) < 1e-6, 'outer throat ring should keep throat axial station');

    const radialDelta = radiusAt(mesh.vertices, outerIdx) - radiusAt(mesh.vertices, innerIdx);
    assert.ok(Math.abs(radialDelta - thickness) < 1e-4, 'outer throat ring should be one thickness radially outward');
  }

  const throatReturnVerts = collectVerticesForTriRange(mesh.indices, mesh.groups.throat_return);
  const rearCapVerts = collectVerticesForTriRange(mesh.indices, mesh.groups.rear_cap);

  let throatReturnTouchesThroatY = false;
  let throatReturnTouchesRearY = false;
  for (const idx of throatReturnVerts) {
    const y = mesh.vertices[idx * 3 + 1];
    if (Math.abs(y - throatY) < 1e-6) throatReturnTouchesThroatY = true;
    if (Math.abs(y - rearY) < 1e-6) throatReturnTouchesRearY = true;
  }
  assert.equal(throatReturnTouchesThroatY, true);
  assert.equal(throatReturnTouchesRearY, true);

  for (const idx of rearCapVerts) {
    const y = mesh.vertices[idx * 3 + 1];
    assert.ok(Math.abs(y - rearY) < 1e-6, 'rear cap should be planar at rear y');
  }
});
