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

  assert.equal(noMorph.ringCount, implicitMorph.ringCount);
  const ringCount = noMorph.ringCount;
  const mouthStart = Number(noMorphParams.lengthSegments) * ringCount;

  let maxIncrease = 0;
  for (let i = 0; i < ringCount; i += 1) {
    const idx = mouthStart + i;
    const rBase = Math.hypot(
      noMorph.vertices[idx * 3],
      noMorph.vertices[idx * 3 + 2]
    );
    const rMorph = Math.hypot(
      implicitMorph.vertices[idx * 3],
      implicitMorph.vertices[idx * 3 + 2]
    );
    maxIncrease = Math.max(maxIncrease, rMorph - rBase);
  }

  assert.ok(
    maxIncrease > 1e-3,
    `implicit morph target extents should change the mouth profile (maxIncrease=${maxIncrease})`
  );
});
