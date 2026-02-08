import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';
import { buildGmshGeo } from '../src/export/gmshGeoBuilder.js';

function makePreparedParams(overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults('OSSE'),
      type: 'OSSE',
      L: '120',
      a: '45',
      a0: '15.5',
      r0: '12.7',
      angularSegments: 24,
      lengthSegments: 10,
      throatResolution: 4,
      mouthResolution: 9,
      rearResolution: 12,
      ...overrides
    },
    { type: 'OSSE', applyVerticalOffset: true }
  );
}

test('buildGmshGeo emits required mesh options and field controls', () => {
  const params = makePreparedParams({
    encDepth: 240,
    interfaceOffset: '8',
    encFrontResolution: '6,7,8,9',
    encBackResolution: '11,12,13,14',
    quadrants: '1'
  });

  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const { geoText, geoStats } = buildGmshGeo(params, artifacts.mesh, artifacts.simulation, {
    mshVersion: '2.2'
  });

  assert.match(geoText, /Mesh\.Algorithm = 1;/);
  assert.match(geoText, /Mesh\.Algorithm3D = 5;/);
  assert.match(geoText, /Mesh\.RecombinationAlgorithm = 1;/);
  assert.match(geoText, /Mesh\.MeshSizeFromCurvature = 1;/);
  assert.match(geoText, /Mesh\.RecombineAll = 0;/);
  assert.match(geoText, /Mesh 2;/);
  assert.match(geoText, /Background Field =/);
  assert.match(geoText, /Field\[\d+\] = Distance;/);
  assert.doesNotMatch(geoText, /Field\(\d+\) = Distance;/);

  assert.equal(typeof geoStats.pointCount, 'number');
  assert.equal(geoStats.surfaceCount, artifacts.simulation.indices.length / 3);
  assert.equal(geoStats.throatResolution, 4);
  assert.equal(geoStats.mouthResolution, 9);
});

test('buildGmshGeo includes canonical physical groups for interface/enclosure meshes', () => {
  const params = makePreparedParams({
    encDepth: 200,
    interfaceOffset: '10',
    quadrants: '1'
  });

  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const { geoText } = buildGmshGeo(params, artifacts.mesh, artifacts.simulation);

  assert.match(geoText, /Physical Surface\("SD1G0", 1\)/);
  assert.match(geoText, /Physical Surface\("SD1D1001", 2\)/);
  assert.match(geoText, /Physical Surface\("SD2G0", 3\)/);
  assert.match(geoText, /Physical Surface\("I1-2", 4\)/);
});

test('buildGmshGeo compacts large physical groups into parse-safe ranges', () => {
  const params = makePreparedParams();
  const triCount = 5000;
  const indices = [];
  for (let i = 0; i < triCount; i += 1) {
    indices.push(0, 1, 2);
  }

  const mesh = {
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices
  };

  const simulation = {
    vertices: mesh.vertices,
    indices: mesh.indices,
    surfaceTags: new Array(triCount).fill(1),
    metadata: { verticalOffset: 0 }
  };

  const { geoText } = buildGmshGeo(params, mesh, simulation, { mshVersion: '2.2' });
  assert.match(geoText, /Physical Surface\("SD1G0", 1\) = \{1:5000\};/);

  const longestLine = geoText.split('\n').reduce((max, line) => Math.max(max, line.length), 0);
  assert.ok(longestLine < 2000, `expected compact .geo output, got line length ${longestLine}`);
});
