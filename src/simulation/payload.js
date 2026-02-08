import { buildHornMesh } from '../geometry/index.js';

export const SURFACE_TAGS = Object.freeze({
  WALL: 1,
  SOURCE: 2,
  SECONDARY: 3,
  INTERFACE: 4
});

function parseInterfaceOffset(value) {
  if (value === undefined || value === null) return 0;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  const text = String(value).trim();
  if (!text) return 0;
  const first = text.split(',')[0].trim();
  const numeric = Number(first);
  return Number.isFinite(numeric) ? numeric : 0;
}

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

function applyTagRange(tags, range, tagValue) {
  if (!range) return;
  const start = Math.max(0, Number(range.start || 0));
  const end = Math.min(tags.length, Number(range.end || 0));
  if (!(end > start)) return;
  for (let i = start; i < end; i += 1) {
    tags[i] = tagValue;
  }
}

function countTags(surfaceTags) {
  const counts = {
    [SURFACE_TAGS.WALL]: 0,
    [SURFACE_TAGS.SOURCE]: 0,
    [SURFACE_TAGS.SECONDARY]: 0,
    [SURFACE_TAGS.INTERFACE]: 0
  };
  for (let i = 0; i < surfaceTags.length; i += 1) {
    const tag = Number(surfaceTags[i]);
    if (counts[tag] !== undefined) {
      counts[tag] += 1;
    }
  }
  return counts;
}

function buildSurfaceTags(meshData, { interfaceEnabled = false } = {}) {
  const triCount = meshData.indices.length / 3;
  const tags = new Array(triCount).fill(SURFACE_TAGS.WALL);

  if (meshData.groups?.source) {
    applyTagRange(tags, meshData.groups.source, SURFACE_TAGS.SOURCE);
  } else {
    const sourceTriCount = Math.min(Math.max(16, Number(meshData.ringCount || 0)), triCount);
    for (let i = 0; i < sourceTriCount; i += 1) {
      tags[i] = SURFACE_TAGS.SOURCE;
    }
  }

  if (interfaceEnabled) {
    applyTagRange(tags, meshData.groups?.enclosure, SURFACE_TAGS.SECONDARY);
    applyTagRange(tags, meshData.groups?.interface, SURFACE_TAGS.INTERFACE);
  }

  return tags;
}

export function buildCanonicalMeshPayload(params, options = {}) {
  const rearClosureForced = shouldForceRearClosure(params, options);
  const buildParams = normalizeBuildParams(params, options);
  const meshData = buildHornMesh(buildParams, {
    includeEnclosure: options.includeEnclosure ?? Number(buildParams.encDepth || 0) > 0,
    includeRearShape: options.includeRearShape ?? true,
    collectGroups: true
  });

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
    boundaryConditions: {
      throat: { type: 'velocity', surfaceTag: SURFACE_TAGS.SOURCE, value: 1.0 },
      wall: { type: 'neumann', surfaceTag: SURFACE_TAGS.WALL, value: 0.0 },
      mouth: { type: 'robin', surfaceTag: SURFACE_TAGS.WALL, impedance: 'spherical' }
    },
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
