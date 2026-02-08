import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams } from '../src/geometry/index.js';
import { buildExportMeshWithGmsh } from '../src/app/exports.js';

function makePreparedParams(overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults('OSSE'),
      type: 'OSSE',
      L: '100',
      a: '50',
      a0: '15',
      r0: '12.7',
      angularSegments: 16,
      lengthSegments: 8,
      ...overrides
    },
    { type: 'OSSE', applyVerticalOffset: true }
  );
}

test('buildExportMeshWithGmsh requests backend gmsh meshing endpoint', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });
    return {
      ok: true,
      async json() {
        return {
          msh: '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n',
          generatedBy: 'gmsh',
          stats: { nodeCount: 3, elementCount: 1 }
        };
      }
    };
  };

  try {
    const app = {
      simulationPanel: {
        solver: {
          backendUrl: 'http://localhost:8000'
        }
      }
    };

    const prepared = makePreparedParams({ encDepth: 180, interfaceOffset: '6' });
    const result = await buildExportMeshWithGmsh(app, prepared);

    assert.equal(result.msh.includes('$MeshFormat'), true);
    assert.equal(requests.length, 1);
    assert.equal(requests[0].url, 'http://localhost:8000/api/mesh/generate-msh');

    const payload = JSON.parse(requests[0].init.body);
    assert.equal(typeof payload.geoText, 'string');
    assert.equal(payload.geoText.includes('Mesh 2;'), true);
    assert.equal(payload.geoText.includes('SizeMin = 40;'), true);
    assert.equal(payload.geoText.includes('SizeMax = 64;'), true);
    assert.equal(payload.mshVersion, '2.2');
  } finally {
    globalThis.fetch = originalFetch;
  }
});
