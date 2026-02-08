import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildGeometryArtifacts } from '../src/geometry/index.js';

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
      angularSegments: 80,
      lengthSegments: 40,
      quadrants: '1234',
      encDepth: 220,
      interfaceOffset: '',
      ...overrides
    },
    { type: 'OSSE' }
  );
}

function edgeKey(a, b) {
  return a < b ? `${a},${b}` : `${b},${a}`;
}

function triangleArea2(vertices, a, b, c) {
  const ax = vertices[a * 3];
  const ay = vertices[a * 3 + 1];
  const az = vertices[a * 3 + 2];
  const bx = vertices[b * 3];
  const by = vertices[b * 3 + 1];
  const bz = vertices[b * 3 + 2];
  const cx = vertices[c * 3];
  const cy = vertices[c * 3 + 1];
  const cz = vertices[c * 3 + 2];

  const abx = bx - ax;
  const aby = by - ay;
  const abz = bz - az;
  const acx = cx - ax;
  const acy = cy - ay;
  const acz = cz - az;

  const nx = aby * acz - abz * acy;
  const ny = abz * acx - abx * acz;
  const nz = abx * acy - aby * acx;
  return Math.hypot(nx, ny, nz);
}

function collectEnclosureVertexSet(vertices, indices, enclosureRange) {
  const vertexSet = new Set();
  for (let t = enclosureRange.start; t < enclosureRange.end; t += 1) {
    const triOffset = t * 3;
    vertexSet.add(indices[triOffset]);
    vertexSet.add(indices[triOffset + 1]);
    vertexSet.add(indices[triOffset + 2]);
  }
  return vertexSet;
}

function countRearBoundaryEdges(vertices, indices, rearY, yEps = 1e-6) {
  const edgeCounts = new Map();
  for (let i = 0; i < indices.length; i += 3) {
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];
    edgeCounts.set(edgeKey(a, b), (edgeCounts.get(edgeKey(a, b)) || 0) + 1);
    edgeCounts.set(edgeKey(b, c), (edgeCounts.get(edgeKey(b, c)) || 0) + 1);
    edgeCounts.set(edgeKey(c, a), (edgeCounts.get(edgeKey(c, a)) || 0) + 1);
  }

  let rearBoundaryEdges = 0;
  for (const [key, count] of edgeCounts.entries()) {
    if (count !== 1) continue;
    const [sa, sb] = key.split(',').map(Number);
    const ya = vertices[sa * 3 + 1];
    const yb = vertices[sb * 3 + 1];
    if (Math.abs(ya - rearY) <= yEps && Math.abs(yb - rearY) <= yEps) {
      rearBoundaryEdges += 1;
    }
  }

  return rearBoundaryEdges;
}

function analyzeEnclosure(mesh) {
  const { vertices, indices, groups } = mesh;
  assert.ok(groups?.enclosure, 'Expected enclosure triangle group');
  const enclosureRange = groups.enclosure;

  const enclosureVertices = collectEnclosureVertexSet(vertices, indices, enclosureRange);
  let rearY = Infinity;
  for (const idx of enclosureVertices) {
    rearY = Math.min(rearY, vertices[idx * 3 + 1]);
  }

  const orientedEdges = new Map();
  let tinyTriangles = 0;
  const areaEps = 1e-10;
  for (let t = enclosureRange.start; t < enclosureRange.end; t += 1) {
    const triOffset = t * 3;
    const a = indices[triOffset];
    const b = indices[triOffset + 1];
    const c = indices[triOffset + 2];

    const area2 = triangleArea2(vertices, a, b, c);
    if (area2 <= areaEps) tinyTriangles += 1;

    const edges = [
      [a, b],
      [b, c],
      [c, a]
    ];

    for (const [u, v] of edges) {
      const k = edgeKey(u, v);
      const [s1, s2] = k.split(',').map(Number);
      const orientation = u === s1 && v === s2 ? 1 : -1;
      if (!orientedEdges.has(k)) orientedEdges.set(k, []);
      orientedEdges.get(k).push(orientation);
    }
  }

  let sameDirectionSharedEdges = 0;
  let nonManifoldSharedEdges = 0;
  for (const edgeOrientations of orientedEdges.values()) {
    if (edgeOrientations.length === 2) {
      if (edgeOrientations[0] === edgeOrientations[1]) {
        sameDirectionSharedEdges += 1;
      }
    } else if (edgeOrientations.length > 2) {
      nonManifoldSharedEdges += 1;
    }
  }

  return {
    rearBoundaryEdges: countRearBoundaryEdges(vertices, indices, rearY),
    sameDirectionSharedEdges,
    nonManifoldSharedEdges,
    tinyTriangles
  };
}

for (const encEdge of [0, 25]) {
  test(`enclosure regression checks pass (encEdge=${encEdge})`, () => {
    const params = makePreparedParams({ encEdge });
    const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
    const analysis = analyzeEnclosure(artifacts.mesh);

    assert.equal(
      analysis.rearBoundaryEdges,
      0,
      'Rear closure should not leave any boundary edges at rear-most enclosure plane'
    );
    assert.equal(
      analysis.sameDirectionSharedEdges,
      0,
      'Enclosure shared edges should have opposite winding between neighboring triangles'
    );
    assert.equal(
      analysis.nonManifoldSharedEdges,
      0,
      'Enclosure should not contain non-manifold shared edges'
    );
    assert.equal(
      analysis.tinyTriangles,
      0,
      'Enclosure should not contain zero-area or near-zero-area triangles'
    );
  });
}
