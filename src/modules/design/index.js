import { prepareGeometryParams } from '../../geometry/params.js';
import { normalizeQuadrants, resolveAutoQuadrants } from './symmetry.js';

const DESIGN_MODULE_ID = 'design';
const DESIGN_IMPORT_STAGE = 'import';
const DESIGN_TASK_STAGE = 'task';
const BACKEND_MESH_MIN_ANGULAR_SEGMENTS = 20;
const BACKEND_MESH_MIN_LENGTH_SEGMENTS = 10;

const DESIGN_INPUT_KINDS = Object.freeze({
  RAW: 'raw',
  PREPARED: 'prepared',
});

const BACKEND_MESH_DEFAULTS = Object.freeze({
  angularSegments: 100,
  lengthSegments: 20,
  throatResolution: 6,
  mouthResolution: 15,
  rearResolution: 40,
  encFrontResolution: '25,25,25,25',
  encBackResolution: '40,40,40,40',
  wallThickness: 6,
  scale: 1,
});

// Mesh resolution fields (throatResolution, mouthResolution, rearResolution,
// encFrontResolution, encBackResolution) represent mesh ELEMENT SIZE in mm,
// not element count. As element sizes, they MUST scale with geometry to
// maintain consistent mesh density (same element count per geometric feature).
// Scaling is handled in prepareBackendMeshSimulationParams (called by
// prepareBackendMeshExportParams)
// to ensure single-scaling for both simulation and export pipelines.

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function toFiniteNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function toPositiveNumber(value, fallback) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback;
}

function scaleResolutionValue(value, scale) {
  if (value === undefined || value === null || value === '') return value;

  if (typeof value === 'number') {
    return value > 0 ? value * scale : value;
  }

  const text = String(value).trim();
  if (!text) return value;
  const parts = text
    .split(',')
    .map((part) => part.trim())
    .filter((part) => part.length > 0);
  if (parts.length === 0) return value;

  const nums = parts.map((part) => Number(part));
  if (nums.some((n) => !Number.isFinite(n))) return value;

  return nums.map((n) => (n > 0 ? n * scale : n)).join(',');
}

function normalizeProfileCsvAngularSegments(value) {
  const count = Math.max(4, Math.round(Number(value) || 0));
  if (count % 4 === 0) return count;
  return Math.max(8, Math.ceil(count / 8) * 8);
}

function normalizeExportAngularSegments(value) {
  const rounded = Math.max(BACKEND_MESH_MIN_ANGULAR_SEGMENTS, Math.round(value));
  const snapped = Math.round(rounded / 4) * 4;
  return Math.max(4, snapped);
}

function createDesignImportEnvelope(kind, payload) {
  return Object.freeze({
    module: DESIGN_MODULE_ID,
    stage: DESIGN_IMPORT_STAGE,
    kind,
    ...payload,
  });
}

function assertDesignImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== DESIGN_MODULE_ID ||
    input.stage !== DESIGN_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error(
      'Design module task requires input from DesignModule.import(), DesignModule.importState(), or DesignModule.importPrepared().'
    );
  }
}

function assertDesignTaskEnvelope(result) {
  if (
    !isObject(result) ||
    result.module !== DESIGN_MODULE_ID ||
    result.stage !== DESIGN_TASK_STAGE ||
    !isObject(result.params)
  ) {
    throw new Error('Design module output requires a result from DesignModule.task().');
  }
}

export function importDesignInput(rawParams = {}, options = {}) {
  return createDesignImportEnvelope(DESIGN_INPUT_KINDS.RAW, {
    params: rawParams,
    options: { ...options },
  });
}

export function importDesignState(state, options = {}) {
  const params = isObject(state?.params) ? state.params : {};
  const type = options.type ?? state?.type;
  return importDesignInput(params, {
    ...options,
    type,
  });
}

export function importPreparedDesignInput(preparedParams = {}) {
  return createDesignImportEnvelope(DESIGN_INPUT_KINDS.PREPARED, {
    params: preparedParams,
    options: {},
  });
}

export function runDesignTask(input) {
  assertDesignImportEnvelope(input);

  const preparedParams =
    input.kind === DESIGN_INPUT_KINDS.PREPARED
      ? input.params
      : prepareGeometryParams(input.params, input.options);

  return Object.freeze({
    module: DESIGN_MODULE_ID,
    stage: DESIGN_TASK_STAGE,
    kind: input.kind,
    input,
    params: preparedParams,
  });
}

export function getPreparedDesignOutput(result) {
  assertDesignTaskEnvelope(result);
  return result.params;
}

export function getExportDesignOutput(result) {
  return getPreparedDesignOutput(result);
}

export function getSimulationDesignOutput(result) {
  return getPreparedDesignOutput(result);
}

export function prepareBackendMeshSimulationParams(preparedParams = {}) {
  const base = isObject(preparedParams) ? preparedParams : {};
  const scale = toPositiveNumber(base.scale, BACKEND_MESH_DEFAULTS.scale);
  const quadrants =
    String(base.quadrants ?? '')
      .trim()
      .toLowerCase() === 'auto'
      ? resolveAutoQuadrants(base)
      : normalizeQuadrants(base.quadrants);

  return Object.freeze({
    ...base,
    angularSegments: Math.max(
      BACKEND_MESH_MIN_ANGULAR_SEGMENTS,
      Math.round(toFiniteNumber(base.angularSegments, BACKEND_MESH_DEFAULTS.angularSegments))
    ),
    lengthSegments: Math.max(
      BACKEND_MESH_MIN_LENGTH_SEGMENTS,
      Math.round(toFiniteNumber(base.lengthSegments, BACKEND_MESH_DEFAULTS.lengthSegments))
    ),
    quadrants,
    throatResolution:
      toPositiveNumber(base.throatResolution, BACKEND_MESH_DEFAULTS.throatResolution) * scale,
    mouthResolution:
      toPositiveNumber(base.mouthResolution, BACKEND_MESH_DEFAULTS.mouthResolution) * scale,
    rearResolution:
      toPositiveNumber(base.rearResolution, BACKEND_MESH_DEFAULTS.rearResolution) * scale,
    wallThickness: toFiniteNumber(base.wallThickness, BACKEND_MESH_DEFAULTS.wallThickness),
    encFrontResolution: scaleResolutionValue(
      base.encFrontResolution != null
        ? String(base.encFrontResolution)
        : BACKEND_MESH_DEFAULTS.encFrontResolution,
      scale
    ),
    encBackResolution: scaleResolutionValue(
      base.encBackResolution != null
        ? String(base.encBackResolution)
        : BACKEND_MESH_DEFAULTS.encBackResolution,
      scale
    ),
  });
}

export function prepareBackendMeshExportParams(preparedParams = {}) {
  const simParams = prepareBackendMeshSimulationParams(preparedParams);
  const hasEnclosure = Number(simParams.encDepth || 0) > 0;

  return Object.freeze({
    ...simParams,
    angularSegments: normalizeExportAngularSegments(simParams.angularSegments),
    lengthSegments: Math.max(
      BACKEND_MESH_MIN_LENGTH_SEGMENTS,
      Math.round(toPositiveNumber(simParams.lengthSegments, BACKEND_MESH_DEFAULTS.lengthSegments))
    ),
    wallThickness: hasEnclosure
      ? simParams.wallThickness
      : toPositiveNumber(simParams.wallThickness, 5),
  });
}

export function getBackendMeshSimulationDesignOutput(result) {
  return prepareBackendMeshSimulationParams(getPreparedDesignOutput(result));
}

export function getBackendMeshExportDesignOutput(result) {
  return prepareBackendMeshExportParams(getPreparedDesignOutput(result));
}

export function prepareProfileCsvParams(preparedParams = {}) {
  const base = isObject(preparedParams) ? preparedParams : {};
  return Object.freeze({
    ...base,
    angularSegments: normalizeProfileCsvAngularSegments(base.angularSegments),
    lengthSegments: Math.max(1, Math.round(Number(base.lengthSegments) || 40)),
  });
}

export function getProfileCsvDesignOutput(result) {
  return prepareProfileCsvParams(getPreparedDesignOutput(result));
}

export function resolveAutoQuadrantsForState(state = {}) {
  const designTask = runDesignTask(
    importDesignState(state, {
      applyVerticalOffset: true,
    })
  );
  return resolveAutoQuadrants(getPreparedDesignOutput(designTask));
}

export const DesignModule = Object.freeze({
  id: DESIGN_MODULE_ID,
  import: importDesignInput,
  importState: importDesignState,
  importPrepared: importPreparedDesignInput,
  task: runDesignTask,
  output: Object.freeze({
    preparedParams: getPreparedDesignOutput,
    exportParams: getExportDesignOutput,
    simulationParams: getSimulationDesignOutput,
    backendMeshSimulationParams: getBackendMeshSimulationDesignOutput,
    backendMeshExportParams: getBackendMeshExportDesignOutput,
    profileCsvParams: getProfileCsvDesignOutput,
  }),
  resolveAutoQuadrantsForState,
});
