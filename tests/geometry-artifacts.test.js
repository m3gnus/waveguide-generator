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

function countTrianglesOnPlane(vertices, indices, axis, epsilon = 1e-7) {
  const coordOffset = axis === 'x' ? 0 : 2;
  let count = 0;
  for (let i = 0; i < indices.length; i += 3) {
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];
    const av = vertices[a * 3 + coordOffset];
    const bv = vertices[b * 3 + coordOffset];
    const cv = vertices[c * 3 + coordOffset];
    if (Math.abs(av) <= epsilon && Math.abs(bv) <= epsilon && Math.abs(cv) <= epsilon) {
      count += 1;
    }
  }
  return count;
}

test('buildGeometryArtifacts returns mesh/simulation/export contract', () => {
  const params = makePreparedParams({
    encDepth: 0,
    quadrants: '1234',
    wallThickness: 5
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

test('simulation payload removes split-plane faces for quadrant symmetry exports', () => {
  const q14Params = makePreparedParams({
    encDepth: 220,
    quadrants: '14'
  });
  const q12Params = makePreparedParams({
    encDepth: 220,
    quadrants: '12'
  });
  const q1Params = makePreparedParams({
    encDepth: 220,
    quadrants: '1'
  });

  const q14 = buildGeometryArtifacts(q14Params, { includeEnclosure: true }).simulation;
  const q12 = buildGeometryArtifacts(q12Params, { includeEnclosure: true }).simulation;
  const q1 = buildGeometryArtifacts(q1Params, { includeEnclosure: true }).simulation;

  assert.equal(countTrianglesOnPlane(q14.vertices, q14.indices, 'x'), 0);
  assert.equal(countTrianglesOnPlane(q12.vertices, q12.indices, 'z'), 0);
  assert.equal(countTrianglesOnPlane(q1.vertices, q1.indices, 'x'), 0);
  assert.equal(countTrianglesOnPlane(q1.vertices, q1.indices, 'z'), 0);
});

test('non-divisible angular segments still include symmetry boundary vertices', () => {
  const params = makePreparedParams({
    angularSegments: 50,
    lengthSegments: 20,
    encDepth: 0,
    quadrants: '1',
    wallThickness: 0
  });

  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: false });
  const ringCount = artifacts.mesh.ringCount;
  const vertices = artifacts.mesh.vertices;
  const eps = 1e-6;

  let hasXBoundary = false;
  let hasZBoundary = false;
  for (let i = 0; i < ringCount; i += 1) {
    const x = vertices[i * 3];
    const z = vertices[i * 3 + 2];
    if (Math.abs(x) <= eps) hasXBoundary = true;
    if (Math.abs(z) <= eps) hasZBoundary = true;
  }

  assert.equal(hasXBoundary, true);
  assert.equal(hasZBoundary, true);
});
