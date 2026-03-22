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

function buildEdgeStats(indices, startTri = 0, endTri = null) {
  const triCount = indices.length / 3;
  const start = Math.max(0, startTri);
  const end = Math.min(endTri === null ? triCount : endTri, triCount);

  const edgeCounts = new Map();
  const orientedEdges = new Map();

  for (let t = start; t < end; t += 1) {
    const off = t * 3;
    const tri = [indices[off], indices[off + 1], indices[off + 2]];
    for (const [u, v] of [[tri[0], tri[1]], [tri[1], tri[2]], [tri[2], tri[0]]]) {
      const key = edgeKey(u, v);
      edgeCounts.set(key, (edgeCounts.get(key) || 0) + 1);
      const [a, b] = key.split(',').map(Number);
      const orientation = (u === a && v === b) ? 1 : -1;
      if (!orientedEdges.has(key)) orientedEdges.set(key, []);
      orientedEdges.get(key).push(orientation);
    }
  }

  return { edgeCounts, orientedEdges };
}

function countConnectedComponents(indices) {
  const triCount = indices.length / 3;
  const edgeToTriangles = new Map();

  for (let t = 0; t < triCount; t += 1) {
    const off = t * 3;
    const tri = [indices[off], indices[off + 1], indices[off + 2]];
    for (const [u, v] of [[tri[0], tri[1]], [tri[1], tri[2]], [tri[2], tri[0]]]) {
      const key = edgeKey(u, v);
      if (!edgeToTriangles.has(key)) edgeToTriangles.set(key, []);
      edgeToTriangles.get(key).push(t);
    }
  }

  const adjacency = Array.from({ length: triCount }, () => []);
  for (const tris of edgeToTriangles.values()) {
    for (let i = 0; i < tris.length; i += 1) {
      for (let j = i + 1; j < tris.length; j += 1) {
        adjacency[tris[i]].push(tris[j]);
        adjacency[tris[j]].push(tris[i]);
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

function countSharedEdges(rangeA, rangeB, indices) {
  if (!rangeA || !rangeB) return { shared: 0, sameDirection: 0, oppositeDirection: 0 };
  const a = buildEdgeStats(indices, rangeA.start, rangeA.end).orientedEdges;
  const b = buildEdgeStats(indices, rangeB.start, rangeB.end).orientedEdges;

  let shared = 0;
  let sameDirection = 0;
  let oppositeDirection = 0;

  for (const [key, listA] of a.entries()) {
    const listB = b.get(key);
    if (!listB) continue;
    shared += 1;
    for (const oa of listA) {
      for (const ob of listB) {
        if (oa === ob) sameDirection += 1;
        else oppositeDirection += 1;
      }
    }
  }

  return { shared, sameDirection, oppositeDirection };
}

export function validateMeshQuality(vertices, indices, groups = null) {
  const triCount = indices.length / 3;

  let degenerateTriangles = 0;
  for (let t = 0; t < triCount; t += 1) {
    const off = t * 3;
    if (triangleArea2(vertices, indices[off], indices[off + 1], indices[off + 2]) <= 1e-10) {
      degenerateTriangles += 1;
    }
  }

  const { edgeCounts, orientedEdges } = buildEdgeStats(indices);
  let boundaryEdges = 0;
  let nonManifoldEdges = 0;
  let sameDirectionSharedEdges = 0;

  for (const [key, count] of edgeCounts.entries()) {
    if (count === 1) boundaryEdges += 1;
    if (count > 2) nonManifoldEdges += 1;

    const orientations = orientedEdges.get(key) || [];
    if (orientations.length === 2 && orientations[0] === orientations[1]) {
      sameDirectionSharedEdges += 1;
    }
  }

  const components = countConnectedComponents(indices);

  const seamStats = countSharedEdges(groups?.horn, groups?.enclosure, indices);
  const sourceConnectivity = countSharedEdges(groups?.source, groups?.horn, indices);

  return {
    triCount,
    degenerateTriangles,
    boundaryEdges,
    nonManifoldEdges,
    sameDirectionSharedEdges,
    components,
    seam: seamStats,
    sourceConnectivity: sourceConnectivity.shared
  };
}
