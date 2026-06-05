import { buildGeometryMeshFromShape } from '../../geometry/pipeline.js';
import { analyzeBemMeshIntegrity } from '../../geometry/meshIntegrity.js';
import { GeometryModule } from './index.js';
import { DesignModule } from '../design/index.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';

const SMOOTH_LENGTH_MIN = 60;
const SMOOTH_LENGTH_MAX = 160;
const SMOOTH_LENGTH_MULTIPLIER = 3;
const SMOOTH_ANGULAR_MIN = 100;
const SMOOTH_ANGULAR_MAX = 240;
const SMOOTH_ANGULAR_MULTIPLIER = 2;
const SMOOTH_CORNER_MIN = 4;
const SMOOTH_CORNER_MAX = 12;
const SMOOTH_CORNER_MULTIPLIER = 2;

function requireViewportState(state) {
  if (!state || typeof state !== 'object') {
    throw new Error('Geometry viewport use cases require an explicit application state snapshot.');
  }
  return state;
}

function clampInt(value, min, max) {
  const n = Math.round(Number(value) || 0);
  if (n < min) return min;
  if (n > max) return max;
  return n;
}

function snapMultipleOfFour(value) {
  return Math.max(4, Math.round(value / 4) * 4);
}

function densifyForSmoothVariant(preparedParams) {
  const userLength = Number(preparedParams.lengthSegments) || 0;
  const userAngular = Number(preparedParams.angularSegments) || 0;
  const userCorner = Number(preparedParams.cornerSegments) || 0;

  const lengthSegments = clampInt(
    Math.max(userLength * SMOOTH_LENGTH_MULTIPLIER, SMOOTH_LENGTH_MIN),
    SMOOTH_LENGTH_MIN,
    SMOOTH_LENGTH_MAX
  );
  const angularRaw = Math.max(userAngular * SMOOTH_ANGULAR_MULTIPLIER, SMOOTH_ANGULAR_MIN);
  const angularSegments = clampInt(
    snapMultipleOfFour(angularRaw),
    SMOOTH_ANGULAR_MIN,
    SMOOTH_ANGULAR_MAX
  );
  const cornerSegments = clampInt(
    Math.max(userCorner * SMOOTH_CORNER_MULTIPLIER, SMOOTH_CORNER_MIN),
    SMOOTH_CORNER_MIN,
    SMOOTH_CORNER_MAX
  );

  return {
    ...preparedParams,
    lengthSegments,
    angularSegments,
    cornerSegments,
  };
}

function assertViewportMeshResponse(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('Backend viewport mesh response must be an object.');
  }
  if (!Array.isArray(payload.vertices) || !Array.isArray(payload.indices)) {
    throw new Error('Backend viewport mesh response is missing vertices/indices arrays.');
  }
  if (payload.vertices.length % 3 !== 0 || payload.indices.length % 3 !== 0) {
    throw new Error('Backend viewport mesh response has invalid vertex or index array length.');
  }
}

/**
 * Prepare mesh data for viewport rendering.
 *
 * `grid` preserves the user's configured segment counts for wire display
 * modes. `smooth` raises sparse preview meshes to a bounded render-only
 * density and enables adaptive angular rings where the geometry engine can
 * support them.
 */
export function prepareViewportMesh(state, { variant = 'grid' } = {}) {
  const viewportState = requireViewportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(viewportState, {
      applyVerticalOffset: true,
    })
  );
  const preparedParams = DesignModule.output.preparedParams(designTask);
  const useAdaptive = variant === 'smooth';
  const geometryParams = useAdaptive ? densifyForSmoothVariant(preparedParams) : preparedParams;
  const geometryTask = GeometryModule.task(GeometryModule.importPrepared(geometryParams), {
    adaptivePhi: useAdaptive,
  });
  const geometryShape = GeometryModule.output.shape(geometryTask);
  const { vertices, indices, groups, normals } = buildGeometryMeshFromShape(geometryShape, {
    adaptivePhi: useAdaptive,
  });

  return { vertices, indices, groups, normals, preparedParams, variant };
}

export function validateViewportMesh(mesh = {}, options = {}) {
  const { strict = false } = options;
  const vertices = mesh.vertices || [];
  const indices = mesh.indices || [];

  if (indices.length === 0) {
    return { ok: true, errors: [], report: null };
  }

  const errors = [];
  const vertexCount = vertices.length / 3;
  let maxIndex = -1;
  for (let i = 0; i < indices.length; i += 1) {
    if (indices[i] > maxIndex) maxIndex = indices[i];
  }
  if (maxIndex >= vertexCount) {
    errors.push(`Index out of range: max index ${maxIndex} >= vertex count ${vertexCount}`);
  }

  const report = analyzeBemMeshIntegrity(vertices, indices, {
    requireClosed: false,
    requireSingleComponent: false,
  });
  for (const message of report.errors) {
    errors.push(message);
  }

  if (errors.length > 0 && strict) {
    throw new Error(`Viewport mesh integrity violation:\n  - ${errors.join('\n  - ')}`);
  }

  return { ok: errors.length === 0, errors, report };
}

/**
 * Prepare viewport mesh data through the Python mesher geometry pipeline.
 * The backend returns a HornLab mesher Gmsh surface mesh converted to
 * millimetres for display.
 */
export async function prepareBackendViewportMesh(
  state,
  { backendUrl = DEFAULT_BACKEND_URL, fetchImpl = fetch } = {}
) {
  const viewportState = requireViewportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(viewportState, {
      applyVerticalOffset: true,
    })
  );
  const preparedParams = DesignModule.output.preparedParams(designTask);
  const requestParams = DesignModule.output.backendMeshSimulationParams(designTask);
  const requestPayload = buildWaveguidePayload(requestParams, '2.2');

  const response = await fetchImpl(`${backendUrl}/api/mesh/viewport`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestPayload),
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Backend viewport mesh failed (${response.status}): ${text}`);
  }
  const payload = await response.json();
  assertViewportMeshResponse(payload);
  return {
    vertices: payload.vertices,
    indices: payload.indices,
    groups: payload.groups || {},
    surfaceTags: payload.surfaceTags || [],
    metadata: payload.metadata || {},
    preparedParams,
  };
}
