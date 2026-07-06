import { DesignModule, prepareBackendMeshSimulationParams } from '../design/index.js';
import { BemSolver } from '../../solver/index.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';

const SIMULATION_MODULE_ID = 'simulation';
const SIMULATION_IMPORT_STAGE = 'import';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createSimulationImportEnvelope(params) {
  return Object.freeze({
    module: SIMULATION_MODULE_ID,
    stage: SIMULATION_IMPORT_STAGE,
    params,
  });
}

function assertDesignTaskEnvelope(input) {
  if (!isObject(input) || input.module !== DesignModule.id || input.stage !== 'task') {
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
    throw new Error(
      'Simulation module task requires input from SimulationModule.import(), SimulationModule.importPrepared(), or SimulationModule.importDesign().'
    );
  }
}

export function importSimulationInput(rawParams = {}, options = {}) {
  return importDesignSimulationInput(DesignModule.task(DesignModule.import(rawParams, options)));
}

export function importPreparedSimulationInput(preparedParams = {}) {
  return createSimulationImportEnvelope(preparedParams);
}

export function importDesignSimulationInput(designTask) {
  assertDesignTaskEnvelope(designTask);
  return createSimulationImportEnvelope(DesignModule.output.simulationParams(designTask));
}

export function buildHornlabMesherSimulationOutput(input, options = {}) {
  assertSimulationImportEnvelope(input);

  const mshVersion = options.mshVersion || '2.2';
  const meshParams = prepareBackendMeshSimulationParams(input.params);
  const simType = Object.prototype.hasOwnProperty.call(options, 'simType')
    ? options.simType
    : (meshParams.simType ?? input.params.simType ?? 2);
  const solverMode = Object.prototype.hasOwnProperty.call(options, 'solverMode')
    ? options.solverMode
    : (meshParams.solverMode ?? input.params.solverMode ?? 'auto');
  const waveguidePayload = buildWaveguidePayload(meshParams, mshVersion);
  waveguidePayload.sim_type = simType;
  waveguidePayload.solver_mode = solverMode;

  return Object.freeze({
    waveguidePayload,
    submitOptions: {
      mesh: {
        strategy: 'hornlab_mesher',
        waveguide_params: waveguidePayload,
      },
    },
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
  output: Object.freeze({
    client: createSimulationClient,
    hornlabMesher: buildHornlabMesherSimulationOutput,
  }),
});
