/**
 * Duplicate vertices along sharp dihedral edges so Three.js can shade each
 * smooth island independently. This catches mouth rims, enclosure corners,
 * throat caps, and other hard edges without relying on mesh group names.
 */
import { createPerfTimer } from '../logging/performance.js';

export function detachCreaseVertices(mesh = {}, thresholdDeg = 30) {
  const perf = createPerfTimer('detachCreaseVertices');
  const vertices = Array.isArray(mesh.vertices)
    ? mesh.vertices.slice()
    : Array.from(mesh.vertices || []);
  const indices = Array.isArray(mesh.indices)
    ? mesh.indices.slice()
    : Array.from(mesh.indices || []);
  const normals = mesh.normals
    ? Array.isArray(mesh.normals)
      ? mesh.normals.slice()
      : Array.from(mesh.normals)
    : null;
  const groups = mesh.groups || {};
  const triCount = Math.floor(indices.length / 3);

  if (triCount === 0) {
    perf.end({ empty: true });
    return { vertices, indices, groups, normals };
  }

  const faceNx = new Float64Array(triCount);
  const faceNy = new Float64Array(triCount);
  const faceNz = new Float64Array(triCount);

  for (let t = 0; t < triCount; t += 1) {
    const b = t * 3;
    const i0 = indices[b];
    const i1 = indices[b + 1];
    const i2 = indices[b + 2];
    const ax = vertices[i0 * 3];
    const ay = vertices[i0 * 3 + 1];
    const az = vertices[i0 * 3 + 2];
    const e1x = vertices[i1 * 3] - ax;
    const e1y = vertices[i1 * 3 + 1] - ay;
    const e1z = vertices[i1 * 3 + 2] - az;
    const e2x = vertices[i2 * 3] - ax;
    const e2y = vertices[i2 * 3 + 1] - ay;
    const e2z = vertices[i2 * 3 + 2] - az;
    let nx = e1y * e2z - e1z * e2y;
    let ny = e1z * e2x - e1x * e2z;
    let nz = e1x * e2y - e1y * e2x;
    const len = Math.hypot(nx, ny, nz);
    if (len > 0) {
      nx /= len;
      ny /= len;
      nz /= len;
    }
    faceNx[t] = nx;
    faceNy[t] = ny;
    faceNz[t] = nz;
  }
  perf.mark('face-normals', { triangleCount: triCount });

  const edgeToTris = new Map();
  for (let t = 0; t < triCount; t += 1) {
    const b = t * 3;
    const tri = [indices[b], indices[b + 1], indices[b + 2]];
    for (let e = 0; e < 3; e += 1) {
      const v0 = tri[e];
      const v1 = tri[(e + 1) % 3];
      const key = v0 < v1 ? v0 * 0x100000 + v1 : v1 * 0x100000 + v0;
      let list = edgeToTris.get(key);
      if (!list) {
        list = [];
        edgeToTris.set(key, list);
      }
      list.push(t);
    }
  }
  perf.mark('edge-adjacency', { edgeCount: edgeToTris.size });

  const cosThreshold = Math.cos((thresholdDeg * Math.PI) / 180);
  const creaseEdges = new Set();
  for (const [key, tris] of edgeToTris) {
    if (tris.length !== 2) continue;
    const [tA, tB] = tris;
    const dot = faceNx[tA] * faceNx[tB] + faceNy[tA] * faceNy[tB] + faceNz[tA] * faceNz[tB];
    if (dot < cosThreshold) {
      creaseEdges.add(key);
    }
  }

  if (creaseEdges.size === 0) {
    perf.end({ creaseEdgeCount: 0, vertexCount: vertices.length / 3, triangleCount: triCount });
    return { vertices, indices, groups, normals };
  }

  const vertexToTris = new Map();
  for (let t = 0; t < triCount; t += 1) {
    const b = t * 3;
    for (let j = 0; j < 3; j += 1) {
      const vi = indices[b + j];
      let list = vertexToTris.get(vi);
      if (!list) {
        list = [];
        vertexToTris.set(vi, list);
      }
      list.push(t);
    }
  }

  const creaseVertices = new Set();
  for (const key of creaseEdges) {
    const v0 = Math.floor(key / 0x100000);
    const v1 = key % 0x100000;
    creaseVertices.add(v0);
    creaseVertices.add(v1);
  }

  for (const v of creaseVertices) {
    const tris = vertexToTris.get(v);
    if (!tris || tris.length < 2) continue;

    const triSet = new Set(tris);
    const neighbors = new Map();
    for (const t of tris) {
      neighbors.set(t, []);
    }

    for (const t of tris) {
      const b = t * 3;
      const triVerts = [indices[b], indices[b + 1], indices[b + 2]];
      for (let e = 0; e < 3; e += 1) {
        const v0 = triVerts[e];
        const v1 = triVerts[(e + 1) % 3];
        if (v0 !== v && v1 !== v) continue;
        const eKey = v0 < v1 ? v0 * 0x100000 + v1 : v1 * 0x100000 + v0;
        if (creaseEdges.has(eKey)) continue;

        const edgeTris = edgeToTris.get(eKey);
        if (!edgeTris) continue;
        for (const otherTri of edgeTris) {
          if (otherTri !== t && triSet.has(otherTri)) {
            neighbors.get(t).push(otherTri);
          }
        }
      }
    }

    const visited = new Set();
    const islands = [];
    for (const t of tris) {
      if (visited.has(t)) continue;
      const island = [];
      const stack = [t];
      while (stack.length > 0) {
        const current = stack.pop();
        if (visited.has(current)) continue;
        visited.add(current);
        island.push(current);
        for (const next of neighbors.get(current)) {
          if (!visited.has(next)) stack.push(next);
        }
      }
      islands.push(island);
    }

    for (let islandIdx = 1; islandIdx < islands.length; islandIdx += 1) {
      const newIndex = vertices.length / 3;
      vertices.push(vertices[v * 3], vertices[v * 3 + 1], vertices[v * 3 + 2]);
      if (normals) {
        normals.push(normals[v * 3], normals[v * 3 + 1], normals[v * 3 + 2]);
      }

      for (const t of islands[islandIdx]) {
        const b = t * 3;
        for (let j = 0; j < 3; j += 1) {
          if (indices[b + j] === v) {
            indices[b + j] = newIndex;
          }
        }
      }

      let newVertexTris = vertexToTris.get(newIndex);
      if (!newVertexTris) {
        newVertexTris = [];
        vertexToTris.set(newIndex, newVertexTris);
      }
      for (const t of islands[islandIdx]) {
        newVertexTris.push(t);
      }
    }
  }

  const result = { vertices, indices, groups, normals };
  perf.end({
    creaseEdgeCount: creaseEdges.size,
    vertexCount: vertices.length / 3,
    triangleCount: triCount,
  });
  return result;
}

function normalizeTriangleRange(range, triangleCount) {
  const start = Number(range?.start);
  const end = Number(range?.end);

  if (!Number.isInteger(start) || !Number.isInteger(end)) {
    return null;
  }
  if (start < 0 || end <= start || end > triangleCount) {
    return null;
  }

  return { start, end };
}

/**
 * Duplicate any horn mouth ring vertices that are shared with enclosure triangles,
 * so that computeVertexNormals() produces independent normals at the hard baffle seam.
 */
export function detachEnclosureSeamVertices(mesh = {}) {
  const vertices = Array.isArray(mesh.vertices) ? mesh.vertices : Array.from(mesh.vertices || []);
  const indices = Array.isArray(mesh.indices) ? mesh.indices : Array.from(mesh.indices || []);
  const triangleCount = Math.floor(indices.length / 3);

  const hornRange = normalizeTriangleRange(
    mesh.groups?.horn ?? mesh.groups?.horn_wall,
    triangleCount
  );
  const enclosureRange = normalizeTriangleRange(mesh.groups?.enclosure, triangleCount);

  if (!hornRange || !enclosureRange) {
    return {
      vertices,
      indices,
      groups: mesh.groups || {},
      detachedVertexCount: 0,
    };
  }

  // Collect all vertex indices used by horn triangles
  const hornVertexSet = new Set();
  for (let t = hornRange.start; t < hornRange.end; t += 1) {
    const base = t * 3;
    hornVertexSet.add(indices[base]);
    hornVertexSet.add(indices[base + 1]);
    hornVertexSet.add(indices[base + 2]);
  }

  const detachedVertices = vertices.slice();
  const detachedIndices = indices.slice();
  const remapped = new Map();

  for (
    let triangleIndex = enclosureRange.start;
    triangleIndex < enclosureRange.end;
    triangleIndex += 1
  ) {
    const base = triangleIndex * 3;
    for (let offset = 0; offset < 3; offset += 1) {
      const sourceIndex = detachedIndices[base + offset];
      if (!Number.isInteger(sourceIndex) || sourceIndex < 0) continue;
      if (!hornVertexSet.has(sourceIndex)) continue;

      let detachedIndex = remapped.get(sourceIndex);
      if (detachedIndex === undefined) {
        detachedIndex = detachedVertices.length / 3;
        detachedVertices.push(
          vertices[sourceIndex * 3],
          vertices[sourceIndex * 3 + 1],
          vertices[sourceIndex * 3 + 2]
        );
        remapped.set(sourceIndex, detachedIndex);
      }

      detachedIndices[base + offset] = detachedIndex;
    }
  }

  return {
    vertices: detachedVertices,
    indices: detachedIndices,
    groups: mesh.groups || {},
    detachedVertexCount: remapped.size,
  };
}

export function detachThroatDiscVertices(mesh = {}) {
  const vertices = Array.isArray(mesh.vertices) ? mesh.vertices : Array.from(mesh.vertices || []);
  const indices = Array.isArray(mesh.indices) ? mesh.indices : Array.from(mesh.indices || []);
  const triangleCount = Math.floor(indices.length / 3);
  const throatDiscRange = normalizeTriangleRange(mesh.groups?.throat_disc, triangleCount);

  if (!throatDiscRange) {
    return {
      vertices,
      indices,
      groups: mesh.groups || {},
      detachedVertexCount: 0,
    };
  }

  const detachedVertices = vertices.slice();
  const detachedIndices = indices.slice();
  const remapped = new Map();

  for (
    let triangleIndex = throatDiscRange.start;
    triangleIndex < throatDiscRange.end;
    triangleIndex += 1
  ) {
    const base = triangleIndex * 3;
    for (let offset = 0; offset < 3; offset += 1) {
      const sourceIndex = detachedIndices[base + offset];
      if (
        !Number.isInteger(sourceIndex) ||
        sourceIndex < 0 ||
        sourceIndex * 3 + 2 >= vertices.length
      ) {
        continue;
      }

      let detachedIndex = remapped.get(sourceIndex);
      if (detachedIndex === undefined) {
        detachedIndex = detachedVertices.length / 3;
        detachedVertices.push(
          vertices[sourceIndex * 3],
          vertices[sourceIndex * 3 + 1],
          vertices[sourceIndex * 3 + 2]
        );
        remapped.set(sourceIndex, detachedIndex);
      }

      detachedIndices[base + offset] = detachedIndex;
    }
  }

  return {
    vertices: detachedVertices,
    indices: detachedIndices,
    groups: mesh.groups || {},
    detachedVertexCount: remapped.size,
  };
}
