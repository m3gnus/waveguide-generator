import { DesignModule, prepareOccSimulationParams } from '../design/index.js';
import {
  buildCanonicalMeshPayloadFromShape
} from '../../geometry/pipeline.js';
import { BemSolver } from '../../solver/index.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';
import { GeometryModule } from '../geometry/index.js';

const SIMULATION_MODULE_ID = 'simulation';
const SIMULATION_IMPORT_STAGE = 'import';
const SIMULATION_TASK_STAGE = 'task';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createSimulationImportEnvelope(params) {
  return Object.freeze({
    module: SIMULATION_MODULE_ID,
    stage: SIMULATION_IMPORT_STAGE,
    params
  });
}

function assertDesignTaskEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== DesignModule.id ||
    input.stage !== 'task'
  ) {
    throw new Error('Simulation module design import requires a result from DesignModule.task().');
  }
}

function assertSimulationImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== SIMULATION_MODULE_ID ||
    input.stage !== SIMULATION_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error('Simulation module task requires input from SimulationModule.import(), SimulationModule.importPrepared(), or SimulationModule.importDesign().');
  }
}

function assertSimulationTaskEnvelope(result) {
  if (
    !isObject(result) ||
    result.module !== SIMULATION_MODULE_ID ||
    result.stage !== SIMULATION_TASK_STAGE ||
    !isObject(result.mesh)
  ) {
    throw new Error('Simulation module output requires a result from SimulationModule.task().');
  }
}

export function importSimulationInput(rawParams = {}, options = {}) {
  return importDesignSimulationInput(
    DesignModule.task(DesignModule.import(rawParams, options))
  );
}

export function importPreparedSimulationInput(preparedParams = {}) {
  return createSimulationImportEnvelope(preparedParams);
}

export function importDesignSimulationInput(designTask) {
  assertDesignTaskEnvelope(designTask);
  return createSimulationImportEnvelope(DesignModule.output.simulationParams(designTask));
}

export function runSimulationTask(input, options = {}) {
  assertSimulationImportEnvelope(input);
  const geometryTask = GeometryModule.task(GeometryModule.importPrepared(input.params), {
    includeEnclosure: options.includeEnclosure ?? Number(input.params.encDepth || 0) > 0,
    adaptivePhi: options.adaptivePhi ?? false
  });
  const geometryShape = GeometryModule.output.shape(geometryTask);

  const mesh = buildCanonicalMeshPayloadFromShape(geometryShape, {
    includeEnclosure: options.includeEnclosure ?? Number(input.params.encDepth || 0) > 0,
    adaptivePhi: options.adaptivePhi ?? false,
    validateIntegrity: options.validateIntegrity === true
  });

  return Object.freeze({
    module: SIMULATION_MODULE_ID,
    stage: SIMULATION_TASK_STAGE,
    input,
    mesh
  });
}

export function getSimulationMeshOutput(result) {
  assertSimulationTaskEnvelope(result);
  return result.mesh;
}

export function buildOccAdaptiveSimulationOutput(input, options = {}) {
  assertSimulationImportEnvelope(input);

  const mshVersion = options.mshVersion || '2.2';
  const simType = options.simType ?? 2;
  const occParams = prepareOccSimulationParams(input.params);
  const waveguidePayload = buildWaveguidePayload(occParams, mshVersion);
  waveguidePayload.sim_type = simType;

  return Object.freeze({
    waveguidePayload,
    submitOptions: {
      mesh: {
        strategy: 'occ_adaptive',
        waveguide_params: waveguidePayload
      }
    }
  });
}

export function createSimulationClient() {
  return new BemSolver();
}

export const SimulationModule = Object.freeze({
  id: SIMULATION_MODULE_ID,
  import: importSimulationInput,
  importPrepared: importPreparedSimulationInput,
  importDesign: importDesignSimulationInput,
  task: runSimulationTask,
  output: Object.freeze({
    client: createSimulationClient,
    mesh: getSimulationMeshOutput,
    occAdaptive: buildOccAdaptiveSimulationOutput
  })
});
