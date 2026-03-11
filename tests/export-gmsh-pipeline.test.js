import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams } from '../src/geometry/index.js';
import { buildExportMeshFromParams, prepareExportArtifacts } from '../src/modules/export/useCases.js';

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

test('prepareExportArtifacts requests backend OCC meshing endpoint', async () => {
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
    const backendUrl = 'http://localhost:8000';

    const prepared = makePreparedParams({ encDepth: 180 });
    const result = await prepareExportArtifacts(prepared, { backendUrl });

    assert.equal(result.msh.includes('$MeshFormat'), true);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, 'http://localhost:8000/health');
    assert.equal(requests[1].url, 'http://localhost:8000/api/mesh/build');

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.formula_type, 'OSSE');
    assert.equal(payload.n_angular, 20);
    assert.equal(payload.n_length, 10);

    assert.equal(typeof result.msh, 'string');
    assert.equal(result.msh.includes('$MeshFormat'), true);
    assert.equal(typeof result.meshStats, 'object');
    assert.equal(result.meshStats.nodeCount, 3);
    assert.equal(result.meshStats.elementCount, 1);
    assert.equal(typeof result.artifacts, 'object');
    assert.equal(typeof result.payload, 'object');
    assert.ok(Array.isArray(result.payload.surfaceTags));

    const usedLegacyRoute = requests.some((request) => request.url.endsWith('/api/mesh/generate-msh'));
    assert.equal(usedLegacyRoute, false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('prepareExportArtifacts does not fall back to /api/mesh/generate-msh on 503', async () => {
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
    const backendUrl = 'http://localhost:8000';

    const prepared = makePreparedParams({ encDepth: 0, wallThickness: 0 });

    await assert.rejects(
      () => prepareExportArtifacts(prepared, { backendUrl }),
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

test('buildExportMeshFromParams forwards to prepareExportArtifacts for compatibility', async () => {
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
    const prepared = makePreparedParams();
    const result = await buildExportMeshFromParams(prepared, { backendUrl: 'http://localhost:8000' });
    assert.equal(typeof result.msh, 'string');
    assert.equal(requests.includes('http://localhost:8000/api/mesh/build'), true);
  } finally {
    globalThis.fetch = originalFetch;
  }
});
