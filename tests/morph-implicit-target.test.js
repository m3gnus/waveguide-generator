import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

function prepare(overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults('R-OSSE'),
      type: 'R-OSSE',
      angularSegments: 96,
      lengthSegments: 24,
      quadrants: '1234',
      R: '160 * (abs(cos(p)/1.9)^4 + abs(sin(p)/1.0)^4)^(-1/4)',
      a: '28 * (abs(cos(p)/1.3)^4 + abs(sin(p)/1.0)^3)^(-1/2.7)',
      encDepth: 0,
      wallThickness: 0,
      morphRate: 1,
      morphFixed: 0,
      morphAllowShrinkage: 0,
      ...overrides
    },
    { type: 'R-OSSE' }
  );
}

function buildMesh(params) {
  return buildGeometryArtifacts(params, { includeEnclosure: false }).mesh;
}

function triangleArea(vertices, indices, triOffset) {
  const ia = indices[triOffset] * 3;
  const ib = indices[triOffset + 1] * 3;
  const ic = indices[triOffset + 2] * 3;
  const ax = vertices[ia];
  const ay = vertices[ia + 1];
  const az = vertices[ia + 2];
  const bx = vertices[ib];
  const by = vertices[ib + 1];
  const bz = vertices[ib + 2];
  const cx = vertices[ic];
  const cy = vertices[ic + 1];
  const cz = vertices[ic + 2];
  const abx = bx - ax;
  const aby = by - ay;
  const abz = bz - az;
  const acx = cx - ax;
  const acy = cy - ay;
  const acz = cz - az;
  const crossX = aby * acz - abz * acy;
  const crossY = abz * acx - abx * acz;
  const crossZ = abx * acy - aby * acx;
  return 0.5 * Math.hypot(crossX, crossY, crossZ);
}

test('R-OSSE morphing derives implicit target extents when width/height are unset', () => {
  const noMorphParams = prepare({
    morphTarget: 0,
    morphWidth: 0,
    morphHeight: 0
  });
  const implicitMorphParams = prepare({
    morphTarget: 1,
    morphWidth: 0,
    morphHeight: 0,
    morphCorner: 0
  });

  const noMorph = buildMesh(noMorphParams);
  const implicitMorph = buildMesh(implicitMorphParams);

  // The rounded-rect morph folds Mesh.CornerSegments into the angular point
  // budget (canonical ATH behaviour), so the morphed mesh has its own ring
  // topology. Compare the two mouths by largest radius rather than by matching
  // vertex index — the implicit extents must enlarge the widest mouth radius.
  const mouthMaxRadius = (mesh) => {
    const mouthStart = Number(noMorphParams.lengthSegments) * mesh.ringCount;
    let maxR = 0;
    for (let i = 0; i < mesh.ringCount; i += 1) {
      const idx = mouthStart + i;
      maxR = Math.max(
        maxR,
        Math.hypot(mesh.vertices[idx * 3], mesh.vertices[idx * 3 + 2])
      );
    }
    return maxR;
  };

  const maxIncrease = mouthMaxRadius(implicitMorph) - mouthMaxRadius(noMorph);
  assert.ok(
    maxIncrease > 1e-3,
    `implicit morph target extents should change the mouth profile (maxIncrease=${maxIncrease})`
  );
});

test('full-radius rounded-rect morph does not duplicate azimuth samples', () => {
  const mesh = buildMesh(
    prepare({
      morphTarget: 1,
      morphWidth: 100,
      morphHeight: 100,
      morphCorner: 50 - 1e-9,
      morphAllowShrinkage: 1
    })
  );

  let minArea = Infinity;
  for (let i = 0; i < mesh.indices.length; i += 3) {
    minArea = Math.min(minArea, triangleArea(mesh.vertices, mesh.indices, i));
  }

  assert.ok(minArea > 1e-9, `expected no degenerate triangles, min area ${minArea}`);
});
