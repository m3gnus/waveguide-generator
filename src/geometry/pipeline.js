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

const GEOMETRY_SHAPE_KIND = 'waveguide-shape';

function resolveBuildOptions(buildParams, options = {}) {
  return {
    includeEnclosure: options.includeEnclosure ?? Number(buildParams.encDepth || 0) > 0,
    collectGroups: true,
    adaptivePhi: options.adaptivePhi ?? false
  };
}

function assertGeometryShape(shape) {
  if (
    !shape
    || typeof shape !== 'object'
    || shape.kind !== GEOMETRY_SHAPE_KIND
    || !shape.buildParams
    || typeof shape.buildParams !== 'object'
  ) {
    throw new Error('Invalid geometry shape: expected a value produced by buildGeometryShape().');
  }
}

export function buildGeometryShape(params, options = {}) {
  const preparedParams = prepareGeometryParams(params, { type: params?.type });
  const buildParams = normalizeBuildParams(preparedParams, options);
  const buildOptions = resolveBuildOptions(buildParams, options);

  return {
    kind: GEOMETRY_SHAPE_KIND,
    buildParams,
    tessellation: {
      includeEnclosure: Boolean(buildOptions.includeEnclosure),
      adaptivePhi: Boolean(buildOptions.adaptivePhi)
    }
  };
}

function buildMeshDataFromShape(shape, options = {}) {
  assertGeometryShape(shape);
  const buildParams = shape.buildParams;
  const shapeOptions = shape.tessellation || {};
  const buildOptions = resolveBuildOptions(buildParams, {
    includeEnclosure: options.includeEnclosure ?? shapeOptions.includeEnclosure,
    adaptivePhi: options.adaptivePhi ?? shapeOptions.adaptivePhi
  });

  return buildWaveguideMesh(buildParams, buildOptions);
}

function buildMeshData(params, options = {}) {
  const geometryShape = buildGeometryShape(params, options);
  const meshData = buildMeshDataFromShape(geometryShape, options);
  return { geometryShape, buildParams: geometryShape.buildParams, meshData };
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

export function buildGeometryMeshFromShape(shape, options = {}) {
  const meshData = buildMeshDataFromShape(shape, options);
  return buildGeometryMeshOutput(meshData);
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

export function buildCanonicalMeshPayloadFromShape(shape, options = {}) {
  assertGeometryShape(shape);
  const meshData = buildMeshDataFromShape(shape, options);
  return buildSimulationPayloadFromMesh(meshData, shape.buildParams, {
    validateIntegrity: options.validateIntegrity ?? true
  });
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
  const { geometryShape, buildParams, meshData } = buildMeshData(params, options);
  const simulation = buildSimulationPayloadFromMesh(meshData, buildParams, {
    validateIntegrity: options.validateIntegrity === true
  });

  return {
    geometry: geometryShape,
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
