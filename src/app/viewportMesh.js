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
      detachedVertexCount: 0
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

  for (let triangleIndex = enclosureRange.start; triangleIndex < enclosureRange.end; triangleIndex += 1) {
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
    detachedVertexCount: remapped.size
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
      detachedVertexCount: 0
    };
  }

  const detachedVertices = vertices.slice();
  const detachedIndices = indices.slice();
  const remapped = new Map();

  for (let triangleIndex = throatDiscRange.start; triangleIndex < throatDiscRange.end; triangleIndex += 1) {
    const base = triangleIndex * 3;
    for (let offset = 0; offset < 3; offset += 1) {
      const sourceIndex = detachedIndices[base + offset];
      if (!Number.isInteger(sourceIndex) || sourceIndex < 0 || (sourceIndex * 3 + 2) >= vertices.length) {
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
    detachedVertexCount: remapped.size
  };
}
