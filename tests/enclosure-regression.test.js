import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

function prepare(type, overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults(type),
      type,
      angularSegments: 80,
      lengthSegments: 24,
      quadrants: '1234',
      ...overrides
    },
    { type }
  );
}

function buildMesh(params, options = {}) {
  return buildGeometryArtifacts(params, options).mesh;
}

function collectEnclosureVertexSet(vertices, indices, enclosureRange) {
  const vertexSet = new Set();
  for (let t = enclosureRange.start; t < enclosureRange.end; t += 1) {
    const triOffset = t * 3;
    vertexSet.add(indices[triOffset]);
    vertexSet.add(indices[triOffset + 1]);
    vertexSet.add(indices[triOffset + 2]);
  }
  return vertexSet;
}

test('OSSE enclosure build selects enclosure mode and emits enclosure groups', () => {
  const params = prepare('OSSE', {
    encDepth: 220,
    wallThickness: 10,
    encSpaceL: 30,
    encSpaceT: 20,
    encSpaceR: 40,
    encSpaceB: 25
  });
  const mesh = buildMesh(params, { includeEnclosure: true });

  assert.ok(mesh.groups?.enclosure, 'enclosure group should exist');
  assert.ok(mesh.groups?.enc_front, 'enc_front group should exist');
  assert.ok(mesh.groups?.enc_side, 'enc_side group should exist');
  assert.ok(mesh.groups?.enc_rear, 'enc_rear group should exist');
  assert.equal(mesh.groups?.freestandingWall, undefined, 'wall shell should not be built when enclosure depth is active');

  const { vertices, indices, ringCount, groups } = mesh;
  const mouthStart = Number(params.lengthSegments) * ringCount;
  let mouthY = -Infinity;
  for (let i = 0; i < ringCount; i += 1) {
    mouthY = Math.max(mouthY, vertices[(mouthStart + i) * 3 + 1]);
  }

  const enclosureVerts = collectEnclosureVertexSet(vertices, indices, groups.enclosure);
  let enclosureMaxY = -Infinity;
  for (const idx of enclosureVerts) {
    enclosureMaxY = Math.max(enclosureMaxY, vertices[idx * 3 + 1]);
  }
  assert.ok(
    enclosureMaxY <= mouthY + 1e-6,
    `Enclosure protrudes ahead of mouth plane (maxY=${enclosureMaxY}, mouthY=${mouthY})`
  );
});

test('R-OSSE enclosure requests are rejected clearly', () => {
  const params = prepare('R-OSSE', {
    encDepth: 200,
    wallThickness: 0
  });

  assert.throws(
    () => buildMesh(params, { includeEnclosure: true }),
    /R-OSSE enclosure is not supported by the default geometry contract/
  );
});

test('enclosure edge treatment supports rounded and chamfered corners with clean radius clamping', () => {
  for (const encEdgeType of [1, 2]) {
    const params = prepare('OSSE', {
      encDepth: 180,
      wallThickness: 0,
      encEdgeType,
      encEdge: 1e6
    });
    const mesh = buildMesh(params, { includeEnclosure: true });

    assert.ok(mesh.groups?.enclosure, 'enclosure group should exist');
    assert.ok(mesh.groups?.enc_edge, 'enc_edge group should exist when encEdge > 0');

    for (const value of mesh.vertices) {
      assert.equal(Number.isFinite(value), true, 'enclosure vertices should remain finite after edge clamp');
    }
  }
});
