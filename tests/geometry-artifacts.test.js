import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  SURFACE_TAGS,
  prepareGeometryParams,
  buildGeometryArtifacts,
  buildCanonicalMeshPayload
} from '../src/geometry/index.js';
import { exportMSH } from '../src/export/msh.js';

function makePreparedParams(overrides = {}) {
  return prepareGeometryParams(
    {
      ...getDefaults('OSSE'),
      type: 'OSSE',
      L: '120',
      a: '45',
      a0: '15.5',
      r0: '12.7',
      s: '0.6',
      n: 4.158,
      q: 0.991,
      k: 7,
      h: 0,
      angularSegments: 32,
      lengthSegments: 12,
      ...overrides
    },
    { type: 'OSSE' }
  );
}

test('buildGeometryArtifacts returns mesh/simulation/export contract', () => {
  const params = makePreparedParams({
    encDepth: 0,
    quadrants: '1234',
    wallThickness: 5,
    rearShape: 0
  });

  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: false });

  assert.ok(Array.isArray(artifacts.mesh.vertices));
  assert.ok(Array.isArray(artifacts.mesh.indices));
  assert.ok(Number.isInteger(artifacts.mesh.ringCount));
  assert.equal(typeof artifacts.mesh.fullCircle, 'boolean');

  assert.ok(Array.isArray(artifacts.simulation.surfaceTags));
  assert.equal(
    artifacts.simulation.surfaceTags.length,
    artifacts.simulation.indices.length / 3
  );
  assert.ok(artifacts.simulation.surfaceTags.includes(SURFACE_TAGS.SOURCE));
  assert.equal(artifacts.simulation.format, 'msh');

  const athVertices = artifacts.export.toAthVertices();
  assert.equal(athVertices.length, artifacts.simulation.vertices.length);
});

test('buildGeometryArtifacts simulation payload matches buildCanonicalMeshPayload', () => {
  const params = makePreparedParams({
    encDepth: 220,
    interfaceOffset: '12',
    quadrants: '1',
    wallThickness: 5
  });

  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const payload = buildCanonicalMeshPayload(params, { includeEnclosure: true });

  assert.deepEqual(artifacts.simulation.surfaceTags, payload.surfaceTags);
  assert.equal(artifacts.simulation.vertices.length, payload.vertices.length);
  assert.equal(artifacts.simulation.indices.length, payload.indices.length);
  assert.equal(artifacts.simulation.metadata.interfaceEnabled, true);
});

test('exportMSH preserves canonical interface and secondary domain tags', () => {
  const params = makePreparedParams({
    encDepth: 240,
    interfaceOffset: '10',
    quadrants: '1'
  });
  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const payload = artifacts.simulation;

  assert.ok(payload.surfaceTags.includes(SURFACE_TAGS.SECONDARY));
  assert.ok(payload.surfaceTags.includes(SURFACE_TAGS.INTERFACE));

  const msh = exportMSH(payload.vertices, payload.indices, payload.surfaceTags, {
    verticalOffset: payload.metadata?.verticalOffset || 0
  });

  assert.match(msh, /2 3 "SD2G0"/);
  assert.match(msh, /2 4 "I1-2"/);
});
