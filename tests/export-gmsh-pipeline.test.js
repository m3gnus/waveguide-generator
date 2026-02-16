import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams } from '../src/geometry/index.js';
import { buildExportMeshFromParams } from '../src/app/exports.js';

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

test('buildExportMeshFromParams requests backend OCC meshing endpoint', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url, init) => {
    requests.push({ url, init });

    if (url.endsWith('/health')) {
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        async json() {
          return { status: 'ok' };
        }
      };
    }

    if (url.endsWith('/api/mesh/build')) {
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        async json() {
          return {
            msh: '$MeshFormat\n2.2 0 8\n$EndMeshFormat\n',
            generatedBy: 'gmsh-occ',
            stats: { nodeCount: 3, elementCount: 1 }
          };
        }
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
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
    const result = await buildExportMeshFromParams(app, prepared);

    assert.equal(result.msh.includes('$MeshFormat'), true);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, 'http://localhost:8000/health');
    assert.equal(requests[1].url, 'http://localhost:8000/api/mesh/build');

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.formula_type, 'OSSE');
    assert.equal(payload.n_angular, 20);
    assert.equal(payload.n_length, 10);

    assert.equal(typeof result.geoText, 'string');
    assert.equal(result.geoText.includes('Mesh 2;'), true);
    assert.equal(result.geoText.includes('= MathEval;'), true);
    assert.equal(result.geoText.includes('.F = "'), true);

    const usedLegacyRoute = requests.some((request) => request.url.endsWith('/api/mesh/generate-msh'));
    assert.equal(usedLegacyRoute, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('buildExportMeshFromParams does not fall back to /api/mesh/generate-msh on 503', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];

  globalThis.fetch = async (url) => {
    requests.push(url);

    if (url.endsWith('/health')) {
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        async json() {
          return { status: 'ok' };
        }
      };
    }

    if (url.endsWith('/api/mesh/build')) {
      return {
        ok: false,
        status: 503,
        statusText: 'Service Unavailable',
        async json() {
          return { detail: 'Python OCC mesh builder unavailable' };
        }
      };
    }

    throw new Error(`Unexpected request URL: ${url}`);
  };

  try {
    const app = {
      simulationPanel: {
        solver: {
          backendUrl: 'http://localhost:8000'
        }
      }
    };

    const prepared = makePreparedParams({ encDepth: 0, wallThickness: 0 });

    await assert.rejects(
      () => buildExportMeshFromParams(app, prepared),
      /\/api\/mesh\/build failed: Python OCC mesh builder unavailable/
    );

    assert.equal(requests.length, 2);
    assert.equal(requests[0], 'http://localhost:8000/health');
    assert.equal(requests[1], 'http://localhost:8000/api/mesh/build');
    assert.equal(requests.some((url) => url.endsWith('/api/mesh/generate-msh')), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
