import { buildHornMesh } from './meshBuilder.js';
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

function resolveBuildOptions(buildParams, options = {}) {
  return {
    includeEnclosure: options.includeEnclosure ?? Number(buildParams.encDepth || 0) > 0,
    includeRearShape: options.includeRearShape ?? true,
    collectGroups: true
  };
}

function buildSimulationPayloadFromMesh(meshData, buildParams, { rearClosureForced = false } = {}) {
  const hasEnclosure = Boolean(meshData.groups?.enclosure);
  const interfaceOffset = parseInterfaceOffset(buildParams.interfaceOffset);
  const interfaceEnabled = hasEnclosure && interfaceOffset > 0;

  const vertices = Array.from(meshData.vertices);
  const indices = Array.from(meshData.indices);
  const surfaceTags = buildSurfaceTags(meshData, { interfaceEnabled });

  if (surfaceTags.length !== indices.length / 3) {
    throw new Error('Mesh payload generation failed: surface tag count does not match triangle count.');
  }

  const tagCounts = countTags(surfaceTags);
  if (tagCounts[SURFACE_TAGS.SOURCE] === 0) {
    throw new Error('Mesh payload generation failed: no source-tagged triangles were produced.');
  }

  return {
    vertices,
    indices,
    surfaceTags,
    format: 'msh',
    boundaryConditions: buildBoundaryConditions(),
    metadata: {
      ringCount: meshData.ringCount,
      fullCircle: Boolean(meshData.fullCircle),
      hasEnclosure,
      interfaceEnabled,
      tagCounts,
      verticalOffset: Number(buildParams.verticalOffset || 0),
      rearClosureForced
    }
  };
}

export function buildCanonicalMeshPayload(params, options = {}) {
  const rearClosureForced = shouldForceRearClosure(params, options);
  const buildParams = normalizeBuildParams(params, options);
  const meshData = buildHornMesh(buildParams, resolveBuildOptions(buildParams, options));
  return buildSimulationPayloadFromMesh(meshData, buildParams, { rearClosureForced });
}

export function buildGeometryArtifacts(params, options = {}) {
  const rearClosureForced = shouldForceRearClosure(params, options);
  const buildParams = normalizeBuildParams(params, options);
  const meshData = buildHornMesh(buildParams, resolveBuildOptions(buildParams, options));
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
