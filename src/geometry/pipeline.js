import { buildWaveguideMesh } from './engine/index.js';
import {
  SURFACE_TAGS,
  normalizeBuildParams,
  buildSurfaceTags,
  countFaceIdentityTriangles,
  countTags,
  buildBoundaryConditions
} from './tags.js';
import { assertBemMeshIntegrity } from './meshIntegrity.js';
import { mapVertexToAth, transformVerticesToAth } from './transforms.js';
import { isPreparedGeometryParams, prepareGeometryParams } from './params.js';

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

function createGeometryShape(preparedParams, options = {}) {
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

export function buildPreparedGeometryShape(preparedParams, options = {}) {
  return createGeometryShape(preparedParams, options);
}

export function buildGeometryShape(params, options = {}) {
  const preparedParams = isPreparedGeometryParams(params)
    ? params
    : prepareGeometryParams(params, { type: params?.type });
  return buildPreparedGeometryShape(preparedParams, options);
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

function buildMeshDataFromPreparedParams(preparedParams, options = {}) {
  const geometryShape = buildPreparedGeometryShape(preparedParams, options);
  const meshData = buildMeshDataFromShape(geometryShape, options);
  return { geometryShape, buildParams: geometryShape.buildParams, meshData };
}

function buildMeshData(params, options = {}) {
  if (isPreparedGeometryParams(params)) {
    return buildMeshDataFromPreparedParams(params, options);
  }
  const preparedParams = prepareGeometryParams(params, { type: params?.type });
  return buildMeshDataFromPreparedParams(preparedParams, options);
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
      requireSingleComponent: true,
      scale: buildParams.scale || 1
    });
  }

  const tagCounts = countTags(surfaceTags);
  const identityTriangleCounts = countFaceIdentityTriangles(meshData.groups, indices.length / 3);
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
      identityTriangleCounts,
      units: 'mm',
      unitScaleToMeter: 0.001 / (buildParams.scale || 1),
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

export function buildPreparedCanonicalMeshPayload(preparedParams, options = {}) {
  const { buildParams, meshData } = buildMeshDataFromPreparedParams(preparedParams, options);
  return buildSimulationPayloadFromMesh(meshData, buildParams, {
    validateIntegrity: options.validateIntegrity ?? true
  });
}

export function buildCanonicalMeshPayload(params, options = {}) {
  const { buildParams, meshData } = buildMeshData(params, options);
  return buildSimulationPayloadFromMesh(meshData, buildParams, {
    validateIntegrity: options.validateIntegrity ?? true
  });
}

export function buildPreparedGeometryMesh(preparedParams, options = {}) {
  const { meshData } = buildMeshDataFromPreparedParams(preparedParams, options);
  return buildGeometryMeshOutput(meshData);
}

export function buildGeometryMesh(params, options = {}) {
  const { meshData } = buildMeshData(params, options);
  return buildGeometryMeshOutput(meshData);
}

export function buildPreparedGeometryArtifacts(preparedParams, options = {}) {
  const { geometryShape, buildParams, meshData } = buildMeshDataFromPreparedParams(preparedParams, options);
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

export function buildGeometryArtifacts(params, options = {}) {
  if (isPreparedGeometryParams(params)) {
    return buildPreparedGeometryArtifacts(params, options);
  }
  const preparedParams = prepareGeometryParams(params, { type: params?.type });
  return buildPreparedGeometryArtifacts(preparedParams, options);
}
