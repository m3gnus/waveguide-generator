import { DesignModule } from '../design/index.js';

const PARAM_MODULE_ID = 'param';
const PARAM_IMPORT_STAGE = 'import';
const PARAM_TASK_STAGE = 'task';

const PARAM_INPUT_KINDS = Object.freeze({
  RAW: 'raw',
  PREPARED: 'prepared'
});

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createParamImportEnvelope(kind, payload) {
  return Object.freeze({
    module: PARAM_MODULE_ID,
    stage: PARAM_IMPORT_STAGE,
    kind,
    ...payload
  });
}

function assertParamImportEnvelope(input) {
  if (
    !isObject(input) ||
    input.module !== PARAM_MODULE_ID ||
    input.stage !== PARAM_IMPORT_STAGE ||
    !isObject(input.params)
  ) {
    throw new Error('Param module task requires input from ParamModule.import(), ParamModule.importState(), or ParamModule.importPrepared().');
  }
}

function assertParamTaskEnvelope(result) {
  if (
    !isObject(result) ||
    result.module !== PARAM_MODULE_ID ||
    result.stage !== PARAM_TASK_STAGE ||
    !isObject(result.params)
  ) {
    throw new Error('Param module output requires a result from ParamModule.task().');
  }
}

export function importParamInput(rawParams = {}, options = {}) {
  return createParamImportEnvelope(PARAM_INPUT_KINDS.RAW, {
    params: rawParams,
    options: { ...options }
  });
}

export function importParamState(state, options = {}) {
  const params = isObject(state?.params) ? state.params : {};
  const type = options.type ?? state?.type;
  return importParamInput(params, {
    ...options,
    type
  });
}

export function importPreparedParamInput(preparedParams = {}) {
  return createParamImportEnvelope(PARAM_INPUT_KINDS.PREPARED, {
    params: preparedParams,
    options: {}
  });
}

export function runParamTask(input) {
  assertParamImportEnvelope(input);

  const designTask = input.kind === PARAM_INPUT_KINDS.PREPARED
    ? DesignModule.task(DesignModule.importPrepared(input.params))
    : DesignModule.task(DesignModule.import(input.params, input.options));
  const preparedParams = DesignModule.output.preparedParams(designTask);

  return Object.freeze({
    module: PARAM_MODULE_ID,
    stage: PARAM_TASK_STAGE,
    kind: input.kind,
    input,
    params: preparedParams
  });
}

export function getPreparedParamOutput(result) {
  assertParamTaskEnvelope(result);
  return result.params;
}

export const ParamModule = Object.freeze({
  id: PARAM_MODULE_ID,
  import: importParamInput,
  importState: importParamState,
  importPrepared: importPreparedParamInput,
  task: runParamTask,
  output: Object.freeze({
    params: getPreparedParamOutput
  })
});
