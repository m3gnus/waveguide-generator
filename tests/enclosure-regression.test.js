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

function buildEdgeMaps(indices, triRange = null) {
  const edgeCounts = new Map();
  const orientedEdges = new Map();

  const start = triRange ? triRange.start : 0;
  const end = triRange ? triRange.end : indices.length / 3;
  for (let t = start; t < end; t += 1) {
    const triOffset = t * 3;
    const tri = [indices[triOffset], indices[triOffset + 1], indices[triOffset + 2]];
    const edges = [
      [tri[0], tri[1]],
      [tri[1], tri[2]],
      [tri[2], tri[0]]
    ];
    for (const [u, v] of edges) {
      const k = edgeKey(u, v);
      edgeCounts.set(k, (edgeCounts.get(k) || 0) + 1);
      const [s1, s2] = k.split(',').map(Number);
      const orientation = u === s1 && v === s2 ? 1 : -1;
      if (!orientedEdges.has(k)) orientedEdges.set(k, []);
      orientedEdges.get(k).push(orientation);
    }
  }

  return { edgeCounts, orientedEdges };
}

function countConnectedComponents(indices) {
  const triCount = indices.length / 3;
  const edgeToTriangles = new Map();
  for (let t = 0; t < triCount; t += 1) {
    const triOffset = t * 3;
    const tri = [indices[triOffset], indices[triOffset + 1], indices[triOffset + 2]];
    for (const [u, v] of [[tri[0], tri[1]], [tri[1], tri[2]], [tri[2], tri[0]]]) {
      const k = edgeKey(u, v);
      if (!edgeToTriangles.has(k)) edgeToTriangles.set(k, []);
      edgeToTriangles.get(k).push(t);
    }
  }

  const adjacency = Array.from({ length: triCount }, () => []);
  for (const list of edgeToTriangles.values()) {
    for (let i = 0; i < list.length; i += 1) {
      for (let j = i + 1; j < list.length; j += 1) {
        adjacency[list[i]].push(list[j]);
        adjacency[list[j]].push(list[i]);
      }
    }
  }

  const visited = new Uint8Array(triCount);
  let components = 0;
  for (let i = 0; i < triCount; i += 1) {
    if (visited[i]) continue;
    components += 1;
    const stack = [i];
    visited[i] = 1;
    while (stack.length > 0) {
      const t = stack.pop();
      for (const next of adjacency[t]) {
        if (!visited[next]) {
          visited[next] = 1;
          stack.push(next);
        }
      }
    }
  }

  return components;
}

function analyzeHornEnclosureSeam(indices, groups) {
  assert.ok(groups?.horn, 'Expected horn triangle group');
  assert.ok(groups?.enclosure, 'Expected enclosure triangle group');

  const hornEdges = buildEdgeMaps(indices, groups.horn).orientedEdges;
  const enclosureEdges = buildEdgeMaps(indices, groups.enclosure).orientedEdges;
  let sharedEdges = 0;
  let sameDirection = 0;
  let oppositeDirection = 0;

  for (const [k, hornOrientations] of hornEdges.entries()) {
    const enclosureOrientations = enclosureEdges.get(k);
    if (!enclosureOrientations) continue;
    sharedEdges += 1;
    for (const h of hornOrientations) {
      for (const e of enclosureOrientations) {
        if (h === e) sameDirection += 1;
        else oppositeDirection += 1;
      }
    }
  }

  return { sharedEdges, sameDirection, oppositeDirection };
}

function analyzeSourceConnectivity(indices, groups) {
  assert.ok(groups?.source, 'Expected source triangle group');
  assert.ok(groups?.horn, 'Expected horn triangle group');

  const sourceEdgeCounts = buildEdgeMaps(indices, groups.source).edgeCounts;
  const hornEdgeCounts = buildEdgeMaps(indices, groups.horn).edgeCounts;

  let sharedEdges = 0;
  for (const k of sourceEdgeCounts.keys()) {
    if (hornEdgeCounts.has(k)) sharedEdges += 1;
  }

  return { sharedEdges };
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

  const { orientedEdges } = buildEdgeMaps(indices, enclosureRange);
  let tinyTriangles = 0;
  const areaEps = 1e-10;
  for (let t = enclosureRange.start; t < enclosureRange.end; t += 1) {
    const triOffset = t * 3;
    const a = indices[triOffset];
    const b = indices[triOffset + 1];
    const c = indices[triOffset + 2];

    const area2 = triangleArea2(vertices, a, b, c);
    if (area2 <= areaEps) tinyTriangles += 1;

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

  const rearCoordSet = new Set();
  for (const idx of enclosureVertices) {
    const y = vertices[idx * 3 + 1];
    if (Math.abs(y - rearY) > 1e-6) continue;
    const x = vertices[idx * 3];
    const z = vertices[idx * 3 + 2];
    rearCoordSet.add(`${x.toFixed(6)},${z.toFixed(6)}`);
  }

  return {
    rearBoundaryEdges: countRearBoundaryEdges(vertices, indices, rearY),
    sameDirectionSharedEdges,
    nonManifoldSharedEdges,
    tinyTriangles,
    rearUniqueCoords: rearCoordSet.size
  };
}

for (const quadrants of ['1234', '1', '12', '14']) {
  for (const encEdge of [0, 25]) {
    test(`enclosure regression checks pass (quadrants=${quadrants}, encEdge=${encEdge})`, () => {
      const params = makePreparedParams({ quadrants, encEdge });
    const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
    assert.ok(
      artifacts.mesh.groups?.enclosure?.end > artifacts.mesh.groups?.enclosure?.start,
      'Enclosure generation should emit at least one enclosure triangle'
    );
    const analysis = analyzeEnclosure(artifacts.mesh);
    const seam = analyzeHornEnclosureSeam(artifacts.mesh.indices, artifacts.mesh.groups);
    const sourceConnectivity = analyzeSourceConnectivity(artifacts.mesh.indices, artifacts.mesh.groups);
    const componentCount = countConnectedComponents(artifacts.mesh.indices);

    assert.equal(
      analysis.rearBoundaryEdges,
      0,
      'Rear closure should not leave any boundary edges at rear-most enclosure plane'
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
    assert.equal(
      seam.sameDirection,
      0,
      'Horn/enclosure seam shared edges should have opposite orientation'
    );
    assert.ok(
      seam.sharedEdges > 0,
      'Horn/enclosure seam should share at least one edge'
    );
    assert.ok(
      seam.oppositeDirection > 0,
      'Horn/enclosure seam should include opposite-direction shared edges'
    );
    assert.equal(
      componentCount,
      1,
      'Enclosure-enabled mesh should be a single connected component'
    );
    assert.ok(
      sourceConnectivity.sharedEdges > 0,
      'Source surface should share boundary edges with horn throat'
    );
    if (encEdge > 0 && quadrants === '1234') {
      assert.ok(
        analysis.rearUniqueCoords > 8,
        'Rounded case should retain multiple distinct rear-loop points, not collapse to corner-only topology'
      );
    }
    });
  }
}

test('interfaceOffset does not push enclosure ahead of mouth plane', () => {
  const params = makePreparedParams({
    quadrants: '1',
    encEdge: 15,
    interfaceOffset: '10',
    encDepth: 220
  });
  const artifacts = buildGeometryArtifacts(params, { includeEnclosure: true });
  const { vertices, indices, groups, ringCount } = artifacts.mesh;

  const mouthStart = Number(params.lengthSegments) * ringCount;
  let mouthY = -Infinity;
  for (let i = 0; i < ringCount; i += 1) {
    mouthY = Math.max(mouthY, vertices[(mouthStart + i) * 3 + 1]);
  }

  const enclosureVerts = collectEnclosureVertexSet(vertices, indices, groups.enclosure);
  let enclosureMaxY = -Infinity;
  for (const idx of enclosureVerts) {
    enclosureMaxY = Math.max(enclosureMaxY, vertices[idx * 3 + 1]);
  }

  assert.ok(
    enclosureMaxY <= mouthY + 1e-6,
    `Enclosure protrudes ahead of mouth plane (maxY=${enclosureMaxY}, mouthY=${mouthY})`
  );
});
