import { exportProfilesCSV, exportSlicesCSV } from '../../export/profiles.js';
import { generateMWGConfigContent } from '../../export/mwgConfig.js';
import { exportSTLBinary } from '../../export/stl.browser.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';
import {
  buildCanonicalMeshPayloadFromShape,
  buildGeometryMeshFromShape,
} from '../../geometry/pipeline.js';
import { densifyForSmoothTessellation } from '../../geometry/tessellation.js';
import { mapVertexToAth, transformVerticesToAth } from '../../geometry/transforms.js';
import { GeometryModule } from '../geometry/index.js';
import { prepareBackendMeshExportParams, prepareProfileCsvParams } from '../design/index.js';
import { formatDependencyBlockMessage } from '../runtime/health.js';

const EXPORT_MODULE_ID = 'export';
const EXPORT_IMPORT_STAGE = 'import';
const EXPORT_TASK_STAGE = 'task';

const EXPORT_KINDS = Object.freeze({
  HORNLAB_MESHER_MESH: 'hornlab-mesher-mesh',
  STEP: 'step',
  STL: 'stl',
  PROFILE_CSV: 'profile-csv',
  CONFIG: 'config',
});
const DEFAULT_STEP_BUILD_TIMEOUT_MS = 120000;

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createExportImportEnvelope(kind, payload) {
  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_IMPORT_STAGE,
    kind,
    ...payload,
  });
}

function assertExportImportEnvelope(input, expectedKind = null) {
  if (
    !isObject(input) ||
    input.module !== EXPORT_MODULE_ID ||
    input.stage !== EXPORT_IMPORT_STAGE
  ) {
    throw new Error('Export module task requires input created by ExportModule import helpers.');
  }
  if (expectedKind && input.kind !== expectedKind) {
    throw new Error(
      `Export module task expected "${expectedKind}" input but received "${input.kind}".`
    );
  }
}

function assertExportTaskEnvelope(result, expectedKind = null) {
  if (
    !isObject(result) ||
    result.module !== EXPORT_MODULE_ID ||
    result.stage !== EXPORT_TASK_STAGE
  ) {
    throw new Error('Export module output requires a result from ExportModule.task().');
  }
  if (expectedKind && result.kind !== expectedKind) {
    throw new Error(
      `Export module output expected "${expectedKind}" result but received "${result.kind}".`
    );
  }
}

function requireBackendUrl(backendUrl) {
  const normalized = String(backendUrl || '').trim();
  if (!normalized) {
    throw new Error('Export module HornLab mesher task requires a backendUrl.');
  }
  return normalized;
}

async function fetchBackendJson(url, init = {}, { timeoutMs = 10000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  try {
    const response = await fetch(url, {
      ...init,
      signal: controller.signal,
    });
    const body = await response.json().catch(() => null);
    return { response, body };
  } finally {
    clearTimeout(timer);
  }
}

async function fetchBackendHealth(backendUrl, options = {}) {
  try {
    const { response, body } = await fetchBackendJson(`${backendUrl}/health`, {}, options);
    if (!response.ok) {
      return null;
    }
    return body;
  } catch {
    return null;
  }
}

function buildMeshBuildError(response, body) {
  const detail = body?.detail || response.statusText || `HTTP ${response.status}`;
  return `/api/mesh/build failed: ${detail}`;
}

function buildStepBuildError(response, body) {
  const detail = body?.detail || response.statusText || `HTTP ${response.status}`;
  return `/api/mesh/step failed: ${detail}`;
}

async function sleep(ms) {
  if (ms <= 0) {
    return;
  }
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function rotateVerticesForStl(vertices) {
  const rotated = new Float32Array(vertices.length);
  for (let i = 0; i < vertices.length; i += 3) {
    rotated[i] = Number(vertices[i]) || 0;
    rotated[i + 1] = -(Number(vertices[i + 2]) || 0);
    rotated[i + 2] = Number(vertices[i + 1]) || 0;
  }
  return rotated;
}

function buildExportArtifacts(mesh, payload) {
  const verticalOffset = Number(payload?.metadata?.verticalOffset || 0);
  return {
    mesh,
    export: {
      verticalOffset,
      mapVertexToAth,
      transformVerticesToAth,
      toAthVertices(vertices = payload.vertices, transformOptions = {}) {
        return transformVerticesToAth(vertices, {
          verticalOffset,
          offsetSign: 1,
          ...transformOptions,
        });
      },
    },
  };
}

async function runHornlabMesherMeshExportTask(input, options = {}) {
  assertExportImportEnvelope(input, EXPORT_KINDS.HORNLAB_MESHER_MESH);
  const backendUrl = requireBackendUrl(input.backendUrl);

  input.onStatus?.('Connecting to backend...');

  const healthTimeoutMs = options.healthTimeoutMs || 10000;
  let health = await fetchBackendHealth(backendUrl, { timeoutMs: healthTimeoutMs });
  if (!health) {
    await sleep(options.healthRetryDelayMs ?? 500);
    health = await fetchBackendHealth(backendUrl, { timeoutMs: healthTimeoutMs });
  }
  if (!health) {
    throw new Error(`Backend health check failed at ${backendUrl}.\nStart with: npm start`);
  }

  if (health?.mesherReady === false) {
    throw new Error(
      formatDependencyBlockMessage(health, {
        features: ['meshBuild'],
        fallback: 'HornLab mesher export is unavailable.',
      })
    );
  }

  input.onStatus?.('Building mesh (HornLab mesher)...');

  const mshVersion = options.mshVersion || '2.2';
  const meshParams = prepareBackendMeshExportParams(input.params);
  const requestPayload = buildWaveguidePayload(meshParams, mshVersion);

  let response;
  try {
    const { response: res, body } = await fetchBackendJson(
      `${backendUrl}/api/mesh/build`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
      },
      { timeoutMs: options.meshBuildTimeoutMs || 10000 }
    );

    if (!res.ok) {
      throw new Error(buildMeshBuildError(res, body));
    }

    response = body;
  } catch (err) {
    if (err.message?.includes('/api/mesh/build failed')) throw err;
    throw new Error(`/api/mesh/build request failed: ${err.message}`, {
      cause: err,
    });
  }

  if (
    !response ||
    response.generatedBy !== 'hornlab-waveguide-mesher' ||
    typeof response.msh !== 'string'
  ) {
    throw new Error('Invalid response from /api/mesh/build: expected HornLab mesher mesh data.');
  }

  const geometryTask = GeometryModule.task(GeometryModule.importPrepared(meshParams), {
    includeEnclosure: Number(meshParams.encDepth || 0) > 0,
  });
  const geometryShape = GeometryModule.output.shape(geometryTask);
  const payload = buildCanonicalMeshPayloadFromShape(geometryShape, {
    includeEnclosure: Number(meshParams.encDepth || 0) > 0,
    validateIntegrity: false,
  });
  const mesh = buildGeometryMeshFromShape(geometryShape, {
    includeEnclosure: Number(meshParams.encDepth || 0) > 0,
  });

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.HORNLAB_MESHER_MESH,
    input,
    result: {
      artifacts: buildExportArtifacts(mesh, payload),
      payload,
      msh: response.msh,
      meshStats: response.stats || null,
    },
  });
}

async function runStepExportTask(input, options = {}) {
  assertExportImportEnvelope(input, EXPORT_KINDS.STEP);
  const backendUrl = requireBackendUrl(input.backendUrl);

  input.onStatus?.('Connecting to backend...');

  const healthTimeoutMs = options.healthTimeoutMs || 10000;
  let health = await fetchBackendHealth(backendUrl, { timeoutMs: healthTimeoutMs });
  if (!health) {
    await sleep(options.healthRetryDelayMs ?? 500);
    health = await fetchBackendHealth(backendUrl, { timeoutMs: healthTimeoutMs });
  }
  if (!health) {
    throw new Error(`Backend health check failed at ${backendUrl}.\nStart with: npm start`);
  }

  if (health?.mesherReady === false) {
    throw new Error(
      formatDependencyBlockMessage(health, {
        features: ['meshBuild'],
        fallback: 'HornLab STEP export is unavailable.',
      })
    );
  }

  input.onStatus?.('Building inner-surface STEP...');

  const meshParams = prepareBackendMeshExportParams(input.params);
  const requestPayload = buildWaveguidePayload(meshParams, '2.2');
  requestPayload.enc_depth = 0;
  requestPayload.wall_thickness = 0;

  let response;
  try {
    const { response: res, body } = await fetchBackendJson(
      `${backendUrl}/api/mesh/step`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(requestPayload),
      },
      { timeoutMs: options.stepBuildTimeoutMs || DEFAULT_STEP_BUILD_TIMEOUT_MS }
    );

    if (!res.ok) {
      throw new Error(buildStepBuildError(res, body));
    }

    response = body;
  } catch (err) {
    if (err.message?.includes('/api/mesh/step failed')) throw err;
    throw new Error(`/api/mesh/step request failed: ${err.message}`, {
      cause: err,
    });
  }

  if (
    !response ||
    response.generatedBy !== 'hornlab-waveguide-mesher' ||
    typeof response.step !== 'string'
  ) {
    throw new Error('Invalid response from /api/mesh/step: expected HornLab mesher STEP data.');
  }

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.STEP,
    input,
    files: [
      {
        content: response.step,
        fileName: `${input.baseName}.step`,
        saveOptions: {
          contentType: 'model/step',
          typeInfo: {
            description: 'STEP Surface',
            accept: { 'model/step': ['.step', '.stp'] },
          },
        },
        stats: response.stats || null,
      },
    ],
  });
}

function runStlExportTask(input) {
  assertExportImportEnvelope(input, EXPORT_KINDS.STL);

  const geometryParams = densifyForSmoothTessellation(input.params);
  const geometryTask = GeometryModule.task(GeometryModule.importPrepared(geometryParams), {
    includeEnclosure: false,
    adaptivePhi: true,
  });
  const geometryShape = GeometryModule.output.shape(geometryTask);
  const { vertices, indices } = buildGeometryMeshFromShape(geometryShape, {
    includeEnclosure: false,
    adaptivePhi: true,
  });
  const stlBinary = exportSTLBinary(
    rotateVerticesForStl(Float32Array.from(vertices)),
    Uint32Array.from(indices),
    input.modelName
  );

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.STL,
    input,
    files: [
      {
        content: stlBinary,
        fileName: `${input.baseName}.stl`,
        saveOptions: {
          contentType: 'application/sla',
          typeInfo: {
            description: 'STL Model',
            accept: { 'model/stl': ['.stl'] },
          },
        },
      },
    ],
  });
}

function runProfileCsvExportTask(input) {
  assertExportImportEnvelope(input, EXPORT_KINDS.PROFILE_CSV);

  const csvParams = prepareProfileCsvParams(input.params);
  const meshParams = {
    angularSegments: csvParams.angularSegments,
    lengthSegments: csvParams.lengthSegments,
  };

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.PROFILE_CSV,
    input,
    files: [
      {
        content: exportProfilesCSV(input.vertices, meshParams),
        fileName: `${input.baseName}_profiles.csv`,
        saveOptions: {
          contentType: 'text/csv',
          typeInfo: {
            description: 'Angular Profiles',
            accept: { 'text/csv': ['.csv'] },
          },
        },
      },
      {
        content: exportSlicesCSV(input.vertices, meshParams),
        fileName: `${input.baseName}_slices.csv`,
        saveOptions: {
          contentType: 'text/csv',
          typeInfo: {
            description: 'Length Slices',
            accept: { 'text/csv': ['.csv'] },
          },
        },
      },
    ],
  });
}

function runConfigExportTask(input) {
  assertExportImportEnvelope(input, EXPORT_KINDS.CONFIG);

  return Object.freeze({
    module: EXPORT_MODULE_ID,
    stage: EXPORT_TASK_STAGE,
    kind: EXPORT_KINDS.CONFIG,
    input,
    files: [
      {
        content: generateMWGConfigContent(input.params),
        fileName: `${input.baseName}.txt`,
        saveOptions: {
          contentType: 'text/plain',
          typeInfo: {
            description: 'Parameter Config',
            accept: { 'text/plain': ['.txt'] },
          },
        },
      },
    ],
  });
}

export function importHornlabMesherMeshBuild(preparedParams, { backendUrl, onStatus } = {}) {
  return createExportImportEnvelope(EXPORT_KINDS.HORNLAB_MESHER_MESH, {
    params: preparedParams,
    backendUrl,
    onStatus,
  });
}

export function importStepExport(
  preparedParams,
  { backendUrl, baseName = 'waveguide', onStatus } = {}
) {
  return createExportImportEnvelope(EXPORT_KINDS.STEP, {
    params: preparedParams,
    backendUrl,
    baseName,
    onStatus,
  });
}

export function importStlExport(
  preparedParams,
  { baseName = 'waveguide', modelName = 'MWG Horn' } = {}
) {
  return createExportImportEnvelope(EXPORT_KINDS.STL, {
    params: preparedParams,
    baseName,
    modelName,
  });
}

export function importProfileCsvExport(preparedParams, { vertices, baseName = 'waveguide' } = {}) {
  return createExportImportEnvelope(EXPORT_KINDS.PROFILE_CSV, {
    params: preparedParams,
    vertices,
    baseName,
  });
}

export function importConfigExport({ params, baseName = 'waveguide' }) {
  return createExportImportEnvelope(EXPORT_KINDS.CONFIG, {
    params,
    baseName,
  });
}

export function runExportTask(input, options = {}) {
  assertExportImportEnvelope(input);

  switch (input.kind) {
    case EXPORT_KINDS.HORNLAB_MESHER_MESH:
      return runHornlabMesherMeshExportTask(input, options);
    case EXPORT_KINDS.STEP:
      return runStepExportTask(input, options);
    case EXPORT_KINDS.STL:
      return runStlExportTask(input);
    case EXPORT_KINDS.PROFILE_CSV:
      return runProfileCsvExportTask(input);
    case EXPORT_KINDS.CONFIG:
      return runConfigExportTask(input);
    default:
      throw new Error(`Unsupported export module task: ${input.kind}`);
  }
}

export function getExportFiles(result) {
  assertExportTaskEnvelope(result);
  return Array.isArray(result.files) ? result.files : [];
}

export function getHornlabMesherMeshBuildResult(result) {
  assertExportTaskEnvelope(result, EXPORT_KINDS.HORNLAB_MESHER_MESH);
  return result.result;
}

export const ExportModule = Object.freeze({
  id: EXPORT_MODULE_ID,
  importHornlabMesherMeshBuild,
  importStep: importStepExport,
  importStl: importStlExport,
  importProfileCsv: importProfileCsvExport,
  importConfig: importConfigExport,
  task: runExportTask,
  output: Object.freeze({
    files: getExportFiles,
    hornlabMesherMesh: getHornlabMesherMeshBuildResult,
  }),
});
