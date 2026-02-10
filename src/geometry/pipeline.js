import { buildWaveguideMesh } from './engine/index.js';
import {
  SURFACE_TAGS,
  parseInterfaceOffset,
  shouldForceRearClosure,
  normalizeBuildParams,
  buildSurfaceTags,
  countTags,
  buildBoundaryConditions
} from './tags.js';
import { mapVertexToAth, transformVerticesToAth } from './transforms.js';
import { prepareGeometryParams } from './params.js';

function resolveBuildOptions(buildParams, options = {}) {
  return {
    includeEnclosure: options.includeEnclosure ?? Number(buildParams.encDepth || 0) > 0,
    collectGroups: true,
    adaptivePhi: options.adaptivePhi ?? false
  };
}

function getSymmetryAxes(quadrants) {
  const q = String(quadrants ?? '1234').trim();
  if (q === '14') return ['x'];
  if (q === '12') return ['z'];
  if (q === '1') return ['x', 'z'];
  return [];
}

function triangleLiesOnAxisPlane(vertices, a, b, c, axis, epsilon = 1e-7) {
  const coordOffset = axis === 'x' ? 0 : 2;
  const av = vertices[a * 3 + coordOffset];
  const bv = vertices[b * 3 + coordOffset];
  const cv = vertices[c * 3 + coordOffset];
  return Math.abs(av) <= epsilon && Math.abs(bv) <= epsilon && Math.abs(cv) <= epsilon;
}

function removeSplitPlaneTriangles(vertices, indices, surfaceTags, quadrants) {
  const symmetryAxes = getSymmetryAxes(quadrants);
  if (symmetryAxes.length === 0) {
    return {
      indices,
      surfaceTags,
      removedTriangles: 0
    };
  }

  const filteredIndices = [];
  const filteredSurfaceTags = [];
  let removedTriangles = 0;

  for (let i = 0; i < indices.length; i += 3) {
    const a = indices[i];
    const b = indices[i + 1];
    const c = indices[i + 2];
    const liesOnSplitPlane = symmetryAxes.some((axis) =>
      triangleLiesOnAxisPlane(vertices, a, b, c, axis)
    );

    if (liesOnSplitPlane) {
      removedTriangles += 1;
      continue;
    }

    filteredIndices.push(a, b, c);
    filteredSurfaceTags.push(surfaceTags[i / 3]);
  }

  return {
    indices: filteredIndices,
    surfaceTags: filteredSurfaceTags,
    removedTriangles
  };
}

function buildSimulationPayloadFromMesh(meshData, buildParams, { rearClosureForced = false } = {}) {
  const hasEnclosure = Boolean(meshData.groups?.enclosure);
  const interfaceOffset = parseInterfaceOffset(buildParams.interfaceOffset);
  const interfaceEnabled = hasEnclosure && interfaceOffset > 0;

  const vertices = Array.from(meshData.vertices);
  const indices = Array.from(meshData.indices);
  const surfaceTags = buildSurfaceTags(meshData, { interfaceEnabled });
  const filtered = removeSplitPlaneTriangles(vertices, indices, surfaceTags, buildParams.quadrants);

  if (surfaceTags.length !== indices.length / 3) {
    throw new Error('Mesh payload generation failed: surface tag count does not match triangle count.');
  }

  if (filtered.surfaceTags.length !== filtered.indices.length / 3) {
    throw new Error('Mesh payload generation failed: filtered surface tag count mismatch.');
  }

  const tagCounts = countTags(filtered.surfaceTags);
  if (tagCounts[SURFACE_TAGS.SOURCE] === 0) {
    throw new Error('Mesh payload generation failed: no source-tagged triangles were produced.');
  }

  return {
    vertices,
    indices: filtered.indices,
    surfaceTags: filtered.surfaceTags,
    format: 'msh',
    boundaryConditions: buildBoundaryConditions(),
    metadata: {
      ringCount: meshData.ringCount,
      lengthSteps: Number(buildParams.lengthSegments || 0),
      fullCircle: Boolean(meshData.fullCircle),
      hasEnclosure,
      interfaceEnabled,
      tagCounts,
      verticalOffset: Number(buildParams.verticalOffset || 0),
      rearClosureForced,
      splitPlaneTrianglesRemoved: filtered.removedTriangles
    }
  };
}

export function buildCanonicalMeshPayload(params, options = {}) {
  const preparedParams = prepareGeometryParams(params, { type: params?.type });
  const rearClosureForced = shouldForceRearClosure(preparedParams, options);
  const buildParams = normalizeBuildParams(preparedParams, options);
  const meshData = buildWaveguideMesh(buildParams, resolveBuildOptions(buildParams, options));
  return buildSimulationPayloadFromMesh(meshData, buildParams, { rearClosureForced });
}

export function buildGeometryArtifacts(params, options = {}) {
  const preparedParams = prepareGeometryParams(params, { type: params?.type });
  const rearClosureForced = shouldForceRearClosure(preparedParams, options);
  const buildParams = normalizeBuildParams(preparedParams, options);
  const meshData = buildWaveguideMesh(buildParams, resolveBuildOptions(buildParams, options));
  const simulation = buildSimulationPayloadFromMesh(meshData, buildParams, { rearClosureForced });

  return {
    mesh: {
      vertices: Array.from(meshData.vertices),
      indices: Array.from(meshData.indices),
      ringCount: meshData.ringCount,
      fullCircle: Boolean(meshData.fullCircle),
      groups: meshData.groups || {}
    },
    simulation,
    export: {
      verticalOffset: simulation.metadata.verticalOffset,
      mapVertexToAth,
      transformVerticesToAth,
      toAthVertices(vertices = simulation.vertices, transformOptions = {}) {
        return transformVerticesToAth(vertices, {
          verticalOffset: simulation.metadata.verticalOffset,
          offsetSign: 1,
          ...transformOptions
        });
      }
    }
  };
}
