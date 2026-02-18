const DEGENERATE_AREA_EPSILON = 1e-10;
const GEOMETRIC_DUPLICATE_EPSILON = 1e-6;

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

function buildEdgeTopology(indices) {
  const triCount = indices.length / 3;
  const edgeUses = new Map();

  for (let t = 0; t < triCount; t += 1) {
    const off = t * 3;
    const tri = [indices[off], indices[off + 1], indices[off + 2]];
    const edges = [
      [tri[0], tri[1]],
      [tri[1], tri[2]],
      [tri[2], tri[0]]
    ];

    for (const [u, v] of edges) {
      if (u === v) continue;
      const key = edgeKey(u, v);
      const sign = u < v ? 1 : -1;
      if (!edgeUses.has(key)) edgeUses.set(key, []);
      edgeUses.get(key).push({ tri: t, sign });
    }
  }

  return edgeUses;
}

function countConnectedComponents(triCount, edgeUses) {
  const adjacency = Array.from({ length: triCount }, () => []);
  for (const uses of edgeUses.values()) {
    if (uses.length < 2) continue;
    for (let i = 0; i < uses.length; i += 1) {
      for (let j = i + 1; j < uses.length; j += 1) {
        adjacency[uses[i].tri].push(uses[j].tri);
        adjacency[uses[j].tri].push(uses[i].tri);
      }
    }
  }

  const visited = new Uint8Array(triCount);
  let components = 0;
  for (let t = 0; t < triCount; t += 1) {
    if (visited[t]) continue;
    components += 1;
    const stack = [t];
    visited[t] = 1;
    while (stack.length > 0) {
      const current = stack.pop();
      for (const next of adjacency[current]) {
        if (visited[next]) continue;
        visited[next] = 1;
        stack.push(next);
      }
    }
  }

  return components;
}

function computeSignedVolume(vertices, indices) {
  let volume6 = 0;
  for (let i = 0; i < indices.length; i += 3) {
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];

    const ax = vertices[a * 3];
    const ay = vertices[a * 3 + 1];
    const az = vertices[a * 3 + 2];
    const bx = vertices[b * 3];
    const by = vertices[b * 3 + 1];
    const bz = vertices[b * 3 + 2];
    const cx = vertices[c * 3];
    const cy = vertices[c * 3 + 1];
    const cz = vertices[c * 3 + 2];

    volume6 += ax * (by * cz - bz * cy);
    volume6 += ay * (bz * cx - bx * cz);
    volume6 += az * (bx * cy - by * cx);
  }
  return volume6 / 6;
}

function flipTriangle(indices, triIndex) {
  const off = triIndex * 3;
  const b = indices[off + 1];
  indices[off + 1] = indices[off + 2];
  indices[off + 2] = b;
}

function quantize(value, epsilon) {
  return Math.round(value / epsilon);
}

function indexTriangleKey(a, b, c) {
  const sorted = [a, b, c].sort((x, y) => x - y);
  return `${sorted[0]},${sorted[1]},${sorted[2]}`;
}

function geometricTriangleKey(vertices, a, b, c, epsilon) {
  const points = [a, b, c].map((idx) => {
    const x = quantize(vertices[idx * 3], epsilon);
    const y = quantize(vertices[idx * 3 + 1], epsilon);
    const z = quantize(vertices[idx * 3 + 2], epsilon);
    return `${x}/${y}/${z}`;
  });
  points.sort();
  return `${points[0]}|${points[1]}|${points[2]}`;
}

export function orientMeshConsistently(vertices, indices, { preferOutward = false } = {}) {
  const triCount = indices.length / 3;
  if (triCount <= 0) {
    return {
      components: 0,
      orientationConflicts: 0,
      trianglesFlipped: 0,
      globalFlipApplied: false
    };
  }

  const edgeUses = buildEdgeTopology(indices);
  const relations = Array.from({ length: triCount }, () => []);

  let boundaryEdges = 0;
  let nonManifoldEdges = 0;
  for (const uses of edgeUses.values()) {
    if (uses.length === 1) {
      boundaryEdges += 1;
      continue;
    }
    if (uses.length > 2) {
      nonManifoldEdges += 1;
      continue;
    }

    const [a, b] = uses;
    relations[a.tri].push({ other: b.tri, ownSign: a.sign, otherSign: b.sign });
    relations[b.tri].push({ other: a.tri, ownSign: b.sign, otherSign: a.sign });
  }

  const orientationState = new Int8Array(triCount);
  let components = 0;
  let orientationConflicts = 0;

  for (let start = 0; start < triCount; start += 1) {
    if (orientationState[start] !== 0) continue;
    components += 1;
    orientationState[start] = 1;
    const stack = [start];

    while (stack.length > 0) {
      const tri = stack.pop();
      const triState = orientationState[tri];
      for (const rel of relations[tri]) {
        const expected = -((rel.ownSign * triState) / rel.otherSign);
        if (orientationState[rel.other] === 0) {
          orientationState[rel.other] = expected;
          stack.push(rel.other);
        } else if (orientationState[rel.other] !== expected) {
          orientationConflicts += 1;
        }
      }
    }
  }

  let trianglesFlipped = 0;
  for (let t = 0; t < triCount; t += 1) {
    if (orientationState[t] !== -1) continue;
    flipTriangle(indices, t);
    trianglesFlipped += 1;
  }

  let globalFlipApplied = false;
  if (preferOutward && boundaryEdges === 0 && nonManifoldEdges === 0) {
    const signedVolume = computeSignedVolume(vertices, indices);
    if (signedVolume < 0) {
      for (let t = 0; t < triCount; t += 1) {
        flipTriangle(indices, t);
      }
      globalFlipApplied = true;
    }
  }

  return {
    components,
    orientationConflicts,
    trianglesFlipped,
    globalFlipApplied
  };
}

export function analyzeBemMeshIntegrity(
  vertices,
  indices,
  {
    requireClosed = false,
    requireSingleComponent = true,
    geometricEpsilon = GEOMETRIC_DUPLICATE_EPSILON
  } = {}
) {
  const triCount = indices.length / 3;
  const edgeUses = buildEdgeTopology(indices);

  let degenerateTriangles = 0;
  let boundaryEdges = 0;
  let nonManifoldEdges = 0;
  let sameDirectionSharedEdges = 0;

  for (let t = 0; t < triCount; t += 1) {
    const off = t * 3;
    const a = indices[off];
    const b = indices[off + 1];
    const c = indices[off + 2];
    if (triangleArea2(vertices, a, b, c) <= DEGENERATE_AREA_EPSILON) {
      degenerateTriangles += 1;
    }
  }

  for (const uses of edgeUses.values()) {
    if (uses.length === 1) boundaryEdges += 1;
    if (uses.length > 2) nonManifoldEdges += 1;
    if (uses.length === 2 && uses[0].sign === uses[1].sign) {
      sameDirectionSharedEdges += 1;
    }
  }

  let duplicateTrianglesByIndex = 0;
  let duplicateTrianglesByGeometry = 0;
  const indexSeen = new Map();
  const geometrySeen = new Map();

  for (let t = 0; t < triCount; t += 1) {
    const off = t * 3;
    const a = indices[off];
    const b = indices[off + 1];
    const c = indices[off + 2];

    const indexKey = indexTriangleKey(a, b, c);
    const indexCount = (indexSeen.get(indexKey) || 0) + 1;
    indexSeen.set(indexKey, indexCount);
    if (indexCount > 1) duplicateTrianglesByIndex += 1;

    const geomKey = geometricTriangleKey(vertices, a, b, c, geometricEpsilon);
    const geomCount = (geometrySeen.get(geomKey) || 0) + 1;
    geometrySeen.set(geomKey, geomCount);
    if (geomCount > 1) duplicateTrianglesByGeometry += 1;
  }

  const components = countConnectedComponents(triCount, edgeUses);
  const errors = [];

  if (degenerateTriangles > 0) {
    errors.push(`Degenerate triangle check failed: ${degenerateTriangles} triangle(s) have near-zero area.`);
  }
  if (nonManifoldEdges > 0) {
    errors.push(`Manifold check failed: ${nonManifoldEdges} edge(s) are shared by more than two triangles.`);
  }
  if (sameDirectionSharedEdges > 0) {
    errors.push(`Surface orientation consistency failed: ${sameDirectionSharedEdges} shared edge(s) have same-direction triangle winding.`);
  }
  if (requireClosed && boundaryEdges > 0) {
    errors.push(`Watertightness check failed: ${boundaryEdges} boundary edge(s) found.`);
  }
  if (duplicateTrianglesByIndex > 0 || duplicateTrianglesByGeometry > 0) {
    errors.push(
      `Duplicate/coincident surface check failed: ${duplicateTrianglesByIndex} duplicate index triangle(s), ${duplicateTrianglesByGeometry} duplicate geometric triangle(s).`
    );
  }
  if (requireSingleComponent && components > 1) {
    errors.push(`Connectivity check failed: mesh contains ${components} disconnected triangle components.`);
  }

  return {
    triCount,
    degenerateTriangles,
    boundaryEdges,
    nonManifoldEdges,
    sameDirectionSharedEdges,
    duplicateTrianglesByIndex,
    duplicateTrianglesByGeometry,
    components,
    errors
  };
}

export function assertBemMeshIntegrity(vertices, indices, options = {}) {
  const report = analyzeBemMeshIntegrity(vertices, indices, options);
  if (report.errors.length > 0) {
    throw new Error(`BEM mesh integrity validation failed:\n- ${report.errors.join('\n- ')}`);
  }
  return report;
}
