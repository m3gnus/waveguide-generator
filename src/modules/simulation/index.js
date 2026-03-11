import { GeometryModule } from '../geometry/index.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';

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

function assertSimulationImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== SIMULATION_MODULE_ID ||
    input.stage !== SIMULATION_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error('Simulation module task requires input from SimulationModule.import() or SimulationModule.importPrepared().');
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
  const geometryInput = GeometryModule.import(rawParams, options);
  return createSimulationImportEnvelope(geometryInput.params);
}

export function importPreparedSimulationInput(preparedParams = {}) {
  return createSimulationImportEnvelope(preparedParams);
}

export function runSimulationTask(input, options = {}) {
  assertSimulationImportEnvelope(input);

  const geometryTask = GeometryModule.task(GeometryModule.importPrepared(input.params), {
    includeEnclosure: options.includeEnclosure ?? Number(input.params.encDepth || 0) > 0,
    adaptivePhi: options.adaptivePhi ?? false
  });

  return Object.freeze({
    module: SIMULATION_MODULE_ID,
    stage: SIMULATION_TASK_STAGE,
    input,
    mesh: GeometryModule.output.simulation(geometryTask)
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
  const waveguidePayload = buildWaveguidePayload(input.params, mshVersion);
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

export const SimulationModule = Object.freeze({
  id: SIMULATION_MODULE_ID,
  import: importSimulationInput,
  importPrepared: importPreparedSimulationInput,
  task: runSimulationTask,
  output: Object.freeze({
    mesh: getSimulationMeshOutput,
    occAdaptive: buildOccAdaptiveSimulationOutput
  })
});
