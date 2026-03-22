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
