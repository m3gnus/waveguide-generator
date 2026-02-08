import { buildHornMesh } from '../geometry/index.js';

export const SURFACE_TAGS = Object.freeze({
  WALL: 1,
  SOURCE: 2,
  SECONDARY: 3,
  INTERFACE: 4
});

function shouldForceRearClosure(params, options) {
  if (options.forceRearClosure === false) return false;
  if (Number(params.encDepth || 0) > 0) return false;
  return Number(params.wallThickness || 0) > 0 && Number(params.rearShape || 0) === 0;
}

function normalizeBuildParams(params, options) {
  const next = { ...params };
  if (shouldForceRearClosure(next, options)) {
    // Freestanding exports need a physical rear closure when wall thickness is used.
    next.rearShape = 2;
  }
  return next;
}

function buildSurfaceTags(meshData) {
  const triCount = meshData.indices.length / 3;
  const tags = new Array(triCount).fill(SURFACE_TAGS.WALL);

  const sourceTriCount = Math.min(Math.max(16, Number(meshData.ringCount || 0)), triCount);
  for (let i = 0; i < sourceTriCount; i += 1) {
    tags[i] = SURFACE_TAGS.SOURCE;
  }

  if (meshData.groups?.enclosure) {
    const start = Math.max(0, Number(meshData.groups.enclosure.start || 0));
    const end = Math.min(triCount, Number(meshData.groups.enclosure.end || 0));
    for (let i = start; i < end; i += 1) {
      tags[i] = SURFACE_TAGS.SECONDARY;
    }
  }

  return tags;
}

export function buildCanonicalMeshPayload(params, options = {}) {
  const buildParams = normalizeBuildParams(params, options);
  const meshData = buildHornMesh(buildParams, {
    includeEnclosure: options.includeEnclosure ?? Number(buildParams.encDepth || 0) > 0,
    includeRearShape: options.includeRearShape ?? true,
    collectGroups: true
  });

  const vertices = Array.from(meshData.vertices);
  const indices = Array.from(meshData.indices);
  const surfaceTags = buildSurfaceTags(meshData);

  if (surfaceTags.length !== indices.length / 3) {
    throw new Error('Mesh payload generation failed: surface tag count does not match triangle count.');
  }

  return {
    vertices,
    indices,
    surfaceTags,
    format: 'msh',
    boundaryConditions: {
      throat: { type: 'velocity', surfaceTag: SURFACE_TAGS.SOURCE, value: 1.0 },
      wall: { type: 'neumann', surfaceTag: SURFACE_TAGS.WALL, value: 0.0 },
      mouth: { type: 'robin', surfaceTag: SURFACE_TAGS.WALL, impedance: 'spherical' }
    },
    metadata: {
      ringCount: meshData.ringCount,
      fullCircle: Boolean(meshData.fullCircle),
      hasEnclosure: Boolean(meshData.groups?.enclosure)
    }
  };
}
