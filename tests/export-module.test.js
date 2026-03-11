import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams } from '../src/geometry/index.js';
import { ExportModule } from '../src/modules/export/index.js';

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

test('ExportModule OCC mesh task requests backend mesh build and returns canonical payload', async () => {
  const originalFetch = globalThis.fetch;
  const requests = [];
  const statuses = [];

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
    const prepared = makePreparedParams({ encDepth: 180 });
    const exportTask = await ExportModule.task(
      ExportModule.importOccMeshBuild(prepared, {
        backendUrl: 'http://localhost:8000',
        onStatus(message) {
          statuses.push(message);
        }
      })
    );
    const result = ExportModule.output.occMesh(exportTask);

    assert.deepEqual(statuses, ['Connecting to backend...', 'Building mesh (Python OCC)...']);
    assert.equal(requests.length, 2);
    assert.equal(requests[0].url, 'http://localhost:8000/health');
    assert.equal(requests[1].url, 'http://localhost:8000/api/mesh/build');
    assert.equal(result.msh.includes('$MeshFormat'), true);
    assert.equal(result.meshStats.nodeCount, 3);
    assert.ok(Array.isArray(result.payload.surfaceTags));

    const payload = JSON.parse(requests[1].init.body);
    assert.equal(payload.formula_type, 'OSSE');
    assert.equal(payload.n_angular, 20);
    assert.equal(payload.n_length, 10);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test('ExportModule file tasks return save descriptors for STL, CSV, and config', () => {
  const prepared = makePreparedParams({ encDepth: 0, wallThickness: 0 });

  const stlTask = ExportModule.task(
    ExportModule.importStl(prepared, { baseName: 'demo' })
  );
  const stlFiles = ExportModule.output.files(stlTask);
  assert.equal(stlFiles.length, 1);
  assert.equal(stlFiles[0].fileName, 'demo.stl');
  assert.equal(stlFiles[0].content instanceof ArrayBuffer, true);
  assert.equal(new DataView(stlFiles[0].content).getUint32(80, true) > 0, true);

  const csvTask = ExportModule.task(
    ExportModule.importProfileCsv({
      vertices: new Float32Array([
        0, 0, 0,
        1, 0, 0,
        0, 1, 0,
        1, 1, 0
      ]),
      angularSegments: 4,
      lengthSegments: 1,
      baseName: 'demo'
    })
  );
  const csvFiles = ExportModule.output.files(csvTask);
  assert.deepEqual(
    csvFiles.map((file) => file.fileName),
    ['demo_profiles.csv', 'demo_slices.csv']
  );

  const configTask = ExportModule.task(
    ExportModule.importConfig({
      params: { type: 'OSSE', ...prepared },
      baseName: 'demo'
    })
  );
  const configFiles = ExportModule.output.files(configTask);
  assert.equal(configFiles.length, 1);
  assert.equal(configFiles[0].fileName, 'demo.txt');
  assert.equal(typeof configFiles[0].content, 'string');
});
