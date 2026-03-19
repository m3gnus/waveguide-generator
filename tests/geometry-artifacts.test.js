import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  SURFACE_TAGS,
  prepareGeometryParams,
  buildGeometryArtifacts,
  buildCanonicalMeshPayload
} from '../src/geometry/index.js';
import { exportLegacyMSH } from './helpers/legacyMsh.js';

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

function measureGroupBounds(mesh, groupName) {
  const range = mesh.groups?.[groupName];
  assert.ok(range, `Expected ${groupName} group to be present`);

  let minX = Infinity;
  let maxX = -Infinity;
  let minY = Infinity;
  let maxY = -Infinity;
  let minZ = Infinity;
  let maxZ = -Infinity;

  for (let t = range.start; t < range.end; t += 1) {
    for (let k = 0; k < 3; k += 1) {
      const idx = mesh.indices[t * 3 + k];
      const x = mesh.vertices[idx * 3];
      const y = mesh.vertices[idx * 3 + 1];
      const z = mesh.vertices[idx * 3 + 2];
      minX = Math.min(minX, x);
      maxX = Math.max(maxX, x);
      minY = Math.min(minY, y);
      maxY = Math.max(maxY, y);
      minZ = Math.min(minZ, z);
      maxZ = Math.max(maxZ, z);
    }
  }

  return { minX, maxX, minY, maxY, minZ, maxZ };
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
    subdomainSlices: '3',
    interfaceOffset: '12',
    interfaceDraw: '4',
    quadrants: '1',
    wallThickness: 5
  });

  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const payload = buildCanonicalMeshPayload(params, { includeEnclosure: true });

  assert.deepEqual(artifacts.simulation.surfaceTags, payload.surfaceTags);
  assert.equal(artifacts.simulation.vertices.length, payload.vertices.length);
  assert.equal(artifacts.simulation.indices.length, payload.indices.length);
  assert.equal(
    Object.prototype.hasOwnProperty.call(artifacts.simulation.metadata, 'interfaceEnabled'),
    false
  );
});

test('legacy test-only MSH helper omits interface/secondary physical groups', () => {
  const params = makePreparedParams({
    encDepth: 240,
    subdomainSlices: '2',
    interfaceOffset: '10',
    interfaceDraw: '3',
    quadrants: '1'
  });
  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const payload = artifacts.simulation;

  assert.equal(payload.surfaceTags.includes(SURFACE_TAGS.SECONDARY), false);
  assert.equal(payload.surfaceTags.includes(SURFACE_TAGS.INTERFACE), false);

  const msh = exportLegacyMSH(payload.vertices, payload.indices, payload.surfaceTags, {
    verticalOffset: payload.metadata?.verticalOffset || 0
  });

  assert.equal(/2 3 "SD2G0"/.test(msh), false);
  assert.equal(/2 4 "I1-2"/.test(msh), false);
});

test('simulation payload ignores quadrants and keeps full-domain topology', () => {
  const fullParams = makePreparedParams({
    encDepth: 220,
    quadrants: '1234'
  });
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

  const full = buildGeometryArtifacts(fullParams, { includeEnclosure: true }).simulation;
  const q14 = buildGeometryArtifacts(q14Params, { includeEnclosure: true }).simulation;
  const q12 = buildGeometryArtifacts(q12Params, { includeEnclosure: true }).simulation;
  const q1 = buildGeometryArtifacts(q1Params, { includeEnclosure: true }).simulation;

  assert.equal(q14.indices.length, full.indices.length);
  assert.equal(q12.indices.length, full.indices.length);
  assert.equal(q1.indices.length, full.indices.length);
  assert.equal(q14.surfaceTags.length, full.surfaceTags.length);
  assert.equal(q12.surfaceTags.length, full.surfaceTags.length);
  assert.equal(q1.surfaceTags.length, full.surfaceTags.length);

  const fullX = countTrianglesOnPlane(full.vertices, full.indices, 'x');
  const fullZ = countTrianglesOnPlane(full.vertices, full.indices, 'z');
  assert.equal(countTrianglesOnPlane(q14.vertices, q14.indices, 'x'), fullX);
  assert.equal(countTrianglesOnPlane(q14.vertices, q14.indices, 'z'), fullZ);
  assert.equal(countTrianglesOnPlane(q12.vertices, q12.indices, 'x'), fullX);
  assert.equal(countTrianglesOnPlane(q12.vertices, q12.indices, 'z'), fullZ);
  assert.equal(countTrianglesOnPlane(q1.vertices, q1.indices, 'x'), fullX);
  assert.equal(countTrianglesOnPlane(q1.vertices, q1.indices, 'z'), fullZ);
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

test('scale below 1 keeps enclosure clearances absolute in the raw geometry pipeline', () => {
  const rawParams = {
    ...getDefaults('OSSE'),
    type: 'OSSE',
    L: '100',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    angularSegments: 32,
    lengthSegments: 12,
    scale: 0.5,
    encDepth: 40,
    encSpaceL: 8,
    encSpaceT: 8,
    encSpaceR: 8,
    encSpaceB: 8,
    wallThickness: 0
  };

  const artifacts = buildGeometryArtifacts(rawParams, { includeEnclosure: true });
  const hornBounds = measureGroupBounds(artifacts.mesh, 'horn');
  const enclosureBounds = measureGroupBounds(artifacts.mesh, 'enclosure');

  assert.ok(Math.abs(hornBounds.minX - enclosureBounds.minX - 8) < 1e-6);
  assert.ok(Math.abs(enclosureBounds.maxX - hornBounds.maxX - 8) < 1e-6);
  assert.ok(Math.abs(hornBounds.minZ - enclosureBounds.minZ - 8) < 1e-6);
  assert.ok(Math.abs(enclosureBounds.maxZ - hornBounds.maxZ - 8) < 1e-6);
  assert.ok(Math.abs(hornBounds.maxY - enclosureBounds.minY - 40) < 1e-6);
});

test('scaled rounded enclosure still reserves the requested horn clearances', () => {
  const rawParams = {
    ...getDefaults('OSSE'),
    type: 'OSSE',
    L: '100',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    angularSegments: 32,
    lengthSegments: 12,
    scale: 0.5,
    encDepth: 40,
    encEdge: 10,
    cornerSegments: 6,
    encSpaceL: 8,
    encSpaceT: 8,
    encSpaceR: 8,
    encSpaceB: 8,
    wallThickness: 0
  };

  const artifacts = buildGeometryArtifacts(rawParams, { includeEnclosure: true });
  const hornBounds = measureGroupBounds(artifacts.mesh, 'horn');
  const enclosureBounds = measureGroupBounds(artifacts.mesh, 'enclosure');

  assert.ok(hornBounds.minX >= enclosureBounds.minX + 8 - 1e-6);
  assert.ok(hornBounds.maxX <= enclosureBounds.maxX - 8 + 1e-6);
  assert.ok(hornBounds.minZ >= enclosureBounds.minZ + 8 - 1e-6);
  assert.ok(hornBounds.maxZ <= enclosureBounds.maxZ - 8 + 1e-6);
  assert.ok(hornBounds.maxY <= enclosureBounds.maxY + 1e-6);
  assert.ok(hornBounds.maxY - enclosureBounds.minY <= 40 + 1e-6);
});
