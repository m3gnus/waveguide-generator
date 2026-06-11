import { buildGeometryMeshFromShape } from '../../geometry/pipeline.js';
import { analyzeBemMeshIntegrity } from '../../geometry/meshIntegrity.js';
import { GeometryModule } from './index.js';
import { DesignModule } from '../design/index.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';
import { prepareViewportTessellationParams } from '../../geometry/tessellation.js';
import { tessellateViewportGeometry } from '../../geometry/viewportTessellator.js';
import { createPerfTimer } from '../../logging/performance.js';

function requireViewportState(state) {
  if (!state || typeof state !== 'object') {
    throw new Error('Geometry viewport use cases require an explicit application state snapshot.');
  }
  return state;
}

function assertViewportGeometryResponse(payload) {
  if (!payload || typeof payload !== 'object') {
    throw new Error('Backend viewport geometry response must be an object.');
  }
  if (!payload.grid || typeof payload.grid !== 'object') {
    throw new Error('Backend viewport geometry response is missing the point grid.');
  }
}

/**
 * Prepare mesh data for viewport rendering.
 *
 * Both variants use bounded render-only tessellation. User `Mesh.*Segments`
 * control solve/export sampling and must not make the live geometry preview
 * look artificially faceted.
 */
export function prepareViewportMesh(state, { variant = 'grid' } = {}) {
  const perf = createPerfTimer(`prepareViewportMesh:${variant}`);
  const viewportState = requireViewportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(viewportState, {
      applyVerticalOffset: true,
    })
  );
  const preparedParams = DesignModule.output.preparedParams(designTask);
  perf.mark('design-params');
  const useAdaptive = variant === 'smooth';
  const geometryParams = prepareViewportTessellationParams(preparedParams, { variant });
  perf.mark('viewport-tessellation');
  const geometryTask = GeometryModule.task(GeometryModule.importPrepared(geometryParams), {
    adaptivePhi: useAdaptive,
  });
  const geometryShape = GeometryModule.output.shape(geometryTask);
  perf.mark('geometry-shape');
  const { vertices, indices, groups, normals } = buildGeometryMeshFromShape(geometryShape, {
    adaptivePhi: useAdaptive,
  });
  perf.end({
    vertexCount: vertices.length / 3,
    triangleCount: indices.length / 3,
  });

  return { vertices, indices, groups, normals, preparedParams, variant };
}

export function validateViewportMesh(mesh = {}, options = {}) {
  const perf = createPerfTimer('validateViewportMesh');
  const { strict = false } = options;
  const vertices = mesh.vertices || [];
  const indices = mesh.indices || [];

  if (indices.length === 0) {
    perf.end({ empty: true });
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
    perf.end({ ok: false, strict: true });
    throw new Error(`Viewport mesh integrity violation:\n  - ${errors.join('\n  - ')}`);
  }

  const result = { ok: errors.length === 0, errors, report };
  perf.end({
    ok: result.ok,
    vertexCount,
    triangleCount: indices.length / 3,
  });
  return result;
}

/**
 * Prepare viewport mesh data through the Python mesher geometry pipeline.
 *
 * The backend (`POST /api/mesh/viewport`) returns the canonical mesher point
 * grids plus enclosure profile rings (no Gmsh); the browser tessellates them
 * into render triangles. Sampling density follows the same per-variant
 * viewport tessellation values as the local JS engine fallback.
 */
export async function prepareBackendViewportMesh(
  state,
  { variant = 'grid', backendUrl = DEFAULT_BACKEND_URL, fetchImpl = fetch, signal } = {}
) {
  const perf = createPerfTimer(`prepareBackendViewportMesh:${variant}`);
  const viewportState = requireViewportState(state);
  const designTask = DesignModule.task(
    DesignModule.importState(viewportState, {
      applyVerticalOffset: true,
    })
  );
  const preparedParams = DesignModule.output.preparedParams(designTask);
  const requestParams = DesignModule.output.backendMeshSimulationParams(designTask);
  const viewportParams = prepareViewportTessellationParams(requestParams, { variant });
  const requestPayload = buildWaveguidePayload(viewportParams, '2.2');
  perf.mark('request-payload');

  const response = await fetchImpl(`${backendUrl}/api/mesh/viewport`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(requestPayload),
    signal,
  });
  if (!response.ok) {
    const text = await response.text().catch(() => '');
    throw new Error(`Backend viewport geometry failed (${response.status}): ${text}`);
  }
  const payload = await response.json();
  perf.mark('fetch');
  assertViewportGeometryResponse(payload);
  const { vertices, indices, groups } = tessellateViewportGeometry(payload);
  perf.end({
    vertexCount: vertices.length / 3,
    triangleCount: indices.length / 3,
  });

  return {
    vertices,
    indices,
    groups,
    metadata: payload.metadata || {},
    preparedParams,
    variant,
  };
}
