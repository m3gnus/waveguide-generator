import { buildCanonicalMeshPayload, buildGeometryArtifacts } from '../../geometry/pipeline.js';
import { DesignModule } from '../design/index.js';

const GEOMETRY_MODULE_ID = 'geometry';
const GEOMETRY_IMPORT_STAGE = 'import';
const GEOMETRY_TASK_STAGE = 'task';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createGeometryImportEnvelope(params) {
  return Object.freeze({
    module: GEOMETRY_MODULE_ID,
    stage: GEOMETRY_IMPORT_STAGE,
    params
  });
}

function assertGeometryImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== GEOMETRY_MODULE_ID ||
    input.stage !== GEOMETRY_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error('Geometry module task requires input from GeometryModule.import() or GeometryModule.importPrepared().');
  }
}

function assertGeometryTaskEnvelope(result) {
  if (
    !isObject(result) ||
    result.module !== GEOMETRY_MODULE_ID ||
    result.stage !== GEOMETRY_TASK_STAGE ||
    !isObject(result.artifacts)
  ) {
    throw new Error('Geometry module output requires a result from GeometryModule.task().');
  }
}

export function importGeometryInput(rawParams = {}, options = {}) {
  const designTask = DesignModule.task(DesignModule.import(rawParams, options));
  return createGeometryImportEnvelope(DesignModule.output.preparedParams(designTask));
}

export function importPreparedGeometryInput(preparedParams = {}) {
  return createGeometryImportEnvelope(preparedParams);
}

export function runGeometryTask(input, options = {}) {
  assertGeometryImportEnvelope(input);
  return Object.freeze({
    module: GEOMETRY_MODULE_ID,
    stage: GEOMETRY_TASK_STAGE,
    input,
    artifacts: buildGeometryArtifacts(input.params, options)
  });
}

export function getGeometryArtifacts(result) {
  assertGeometryTaskEnvelope(result);
  return result.artifacts;
}

export function getGeometryMeshOutput(result) {
  return getGeometryArtifacts(result).mesh;
}

export function getGeometrySimulationOutput(result) {
  return getGeometryArtifacts(result).simulation;
}

export function getGeometryExportOutput(result) {
  return getGeometryArtifacts(result).export;
}

export function buildGeometryCanonicalOutput(input, options = {}) {
  assertGeometryImportEnvelope(input);
  return buildCanonicalMeshPayload(input.params, options);
}

export const GeometryModule = Object.freeze({
  id: GEOMETRY_MODULE_ID,
  import: importGeometryInput,
  importPrepared: importPreparedGeometryInput,
  task: runGeometryTask,
  output: Object.freeze({
    artifacts: getGeometryArtifacts,
    mesh: getGeometryMeshOutput,
    simulation: getGeometrySimulationOutput,
    export: getGeometryExportOutput,
    canonical: buildGeometryCanonicalOutput
  })
});
