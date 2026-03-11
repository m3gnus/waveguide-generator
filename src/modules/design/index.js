import { ParamModule } from '../param/index.js';

const DESIGN_MODULE_ID = 'design';
const DESIGN_IMPORT_STAGE = 'import';
const DESIGN_TASK_STAGE = 'task';

const DESIGN_INPUT_KINDS = Object.freeze({
  RAW: 'raw',
  PREPARED: 'prepared'
});

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createDesignImportEnvelope(kind, payload) {
  return Object.freeze({
    module: DESIGN_MODULE_ID,
    stage: DESIGN_IMPORT_STAGE,
    kind,
    ...payload
  });
}

function assertDesignImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== DESIGN_MODULE_ID ||
    input.stage !== DESIGN_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error('Design module task requires input from DesignModule.import(), DesignModule.importState(), or DesignModule.importPrepared().');
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
    options: { ...options }
  });
}

export function importDesignState(state, options = {}) {
  const params = isObject(state?.params) ? state.params : {};
  const type = options.type ?? state?.type;
  return importDesignInput(params, {
    ...options,
    type
  });
}

export function importPreparedDesignInput(preparedParams = {}) {
  return createDesignImportEnvelope(DESIGN_INPUT_KINDS.PREPARED, {
    params: preparedParams,
    options: {}
  });
}

export function runDesignTask(input) {
  assertDesignImportEnvelope(input);

  const preparedParams = input.kind === DESIGN_INPUT_KINDS.PREPARED
    ? input.params
    : ParamModule.output.params(
      ParamModule.task(
        ParamModule.import(input.params, input.options)
      )
    );

  return Object.freeze({
    module: DESIGN_MODULE_ID,
    stage: DESIGN_TASK_STAGE,
    kind: input.kind,
    input,
    params: preparedParams
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

export const DesignModule = Object.freeze({
  id: DESIGN_MODULE_ID,
  import: importDesignInput,
  importState: importDesignState,
  importPrepared: importPreparedDesignInput,
  task: runDesignTask,
  output: Object.freeze({
    preparedParams: getPreparedDesignOutput,
    exportParams: getExportDesignOutput,
    simulationParams: getSimulationDesignOutput
  })
});
