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
