import { buildWaveguideMesh } from './engine/index.js';
import {
  SURFACE_TAGS,
  normalizeBuildParams,
  buildSurfaceTags,
  countTags,
  buildBoundaryConditions
} from './tags.js';
import { assertBemMeshIntegrity } from './meshIntegrity.js';
import { mapVertexToAth, transformVerticesToAth } from './transforms.js';
import { prepareGeometryParams } from './params.js';

function resolveBuildOptions(buildParams, options = {}) {
  return {
    includeEnclosure: options.includeEnclosure ?? Number(buildParams.encDepth || 0) > 0,
    collectGroups: true,
    adaptivePhi: options.adaptivePhi ?? false
  };
}

function buildMeshData(params, options = {}) {
  const preparedParams = prepareGeometryParams(params, { type: params?.type });
  const buildParams = normalizeBuildParams(preparedParams, options);
  const meshData = buildWaveguideMesh(buildParams, resolveBuildOptions(buildParams, options));
  return { buildParams, meshData };
}

function buildGeometryMeshOutput(meshData) {
  return {
    vertices: Array.from(meshData.vertices),
    indices: Array.from(meshData.indices),
    ringCount: meshData.ringCount,
    fullCircle: Boolean(meshData.fullCircle),
    groups: meshData.groups || {}
  };
}

function buildSimulationPayloadFromMesh(
  meshData,
  buildParams,
  { validateIntegrity = true } = {}
) {
  const hasEnclosure = Boolean(meshData.groups?.enclosure);

  const vertices = Array.from(meshData.vertices);
  const indices = Array.from(meshData.indices);
  const surfaceTags = buildSurfaceTags(meshData);

  if (surfaceTags.length !== indices.length / 3) {
    throw new Error('Mesh payload generation failed: surface tag count does not match triangle count.');
  }

  if (validateIntegrity) {
    const requireClosed = Boolean(meshData.fullCircle && (meshData.groups?.freestandingWall || meshData.groups?.enclosure));
    assertBemMeshIntegrity(vertices, indices, {
      requireClosed,
      requireSingleComponent: true
    });
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
      lengthSteps: Number(buildParams.lengthSegments || 0),
      fullCircle: Boolean(meshData.fullCircle),
      hasEnclosure,
      tagCounts,
      units: 'mm',
      unitScaleToMeter: 0.001,
      verticalOffset: Number(buildParams.verticalOffset || 0)
    }
  };
}

export function buildCanonicalMeshPayload(params, options = {}) {
  const { buildParams, meshData } = buildMeshData(params, options);
  return buildSimulationPayloadFromMesh(meshData, buildParams, {
    validateIntegrity: options.validateIntegrity ?? true
  });
}

export function buildGeometryMesh(params, options = {}) {
  const { meshData } = buildMeshData(params, options);
  return buildGeometryMeshOutput(meshData);
}

export function buildGeometryArtifacts(params, options = {}) {
  const { buildParams, meshData } = buildMeshData(params, options);
  const simulation = buildSimulationPayloadFromMesh(meshData, buildParams, {
    validateIntegrity: options.validateIntegrity === true
  });

  return {
    mesh: buildGeometryMeshOutput(meshData),
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
