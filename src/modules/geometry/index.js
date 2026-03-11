import { buildGeometryShape } from '../../geometry/pipeline.js';
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

function assertDesignTaskEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== DesignModule.id ||
    input.stage !== 'task'
  ) {
    throw new Error('Geometry module design import requires a result from DesignModule.task().');
  }
}

function assertGeometryImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== GEOMETRY_MODULE_ID ||
    input.stage !== GEOMETRY_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error('Geometry module task requires input from GeometryModule.import(), GeometryModule.importPrepared(), or GeometryModule.importDesign().');
  }
}

function assertGeometryTaskEnvelope(result) {
  if (
    !isObject(result) ||
    result.module !== GEOMETRY_MODULE_ID ||
    result.stage !== GEOMETRY_TASK_STAGE ||
    !isObject(result.geometryShape)
  ) {
    throw new Error('Geometry module output requires a result from GeometryModule.task().');
  }
}

export function importGeometryInput(rawParams = {}, options = {}) {
  return importDesignGeometryInput(
    DesignModule.task(DesignModule.import(rawParams, options))
  );
}

export function importPreparedGeometryInput(preparedParams = {}) {
  return createGeometryImportEnvelope(preparedParams);
}

export function importDesignGeometryInput(designTask) {
  assertDesignTaskEnvelope(designTask);
  return createGeometryImportEnvelope(DesignModule.output.preparedParams(designTask));
}

export function runGeometryTask(input, options = {}) {
  assertGeometryImportEnvelope(input);
  return Object.freeze({
    module: GEOMETRY_MODULE_ID,
    stage: GEOMETRY_TASK_STAGE,
    input,
    geometryShape: buildGeometryShape(input.params, options)
  });
}

export function getGeometryOutput(result) {
  assertGeometryTaskEnvelope(result);
  return result.geometryShape;
}

export function getGeometryShapeOutput(result) {
  return getGeometryOutput(result);
}

export const GeometryModule = Object.freeze({
  id: GEOMETRY_MODULE_ID,
  import: importGeometryInput,
  importPrepared: importPreparedGeometryInput,
  importDesign: importDesignGeometryInput,
  task: runGeometryTask,
  output: Object.freeze({
    geometry: getGeometryOutput,
    shape: getGeometryShapeOutput
  })
});
