import { GlobalState } from '../../state.js';

function isObject(value) {
  return value !== null && typeof value === 'object';
}

export function readSimulationState() {
  return GlobalState.get();
}

export function updateSimulationStateParams(nextParams = {}) {
  if (!isObject(nextParams)) {
    return readSimulationState();
  }
  GlobalState.update(nextParams);
  return readSimulationState();
}

export function loadSimulationStateSnapshot(stateSnapshot, source = 'simulation-job-load-script') {
  if (!isObject(stateSnapshot) || !isObject(stateSnapshot.params)) {
    return null;
  }
  GlobalState.loadState(stateSnapshot, source);
  return readSimulationState();
}

export function applySimulationJobScriptState(script = {}, options = {}) {
  const source = typeof options.source === 'string' && options.source.trim()
    ? options.source
    : 'simulation-job-load-script';

  if (isObject(script.stateSnapshot) && isObject(script.stateSnapshot.params)) {
    loadSimulationStateSnapshot(script.stateSnapshot, source);
    return {
      mode: 'snapshot',
      params: script.stateSnapshot.params
    };
  }

  if (isObject(script.params)) {
    updateSimulationStateParams(script.params);
    return {
      mode: 'params',
      params: script.params
    };
  }

  return {
    mode: 'none',
    params: null
  };
}

export function buildQueuedSimulationJob({
  jobId,
  startedIso,
  outputName,
  counter,
  config,
  waveguidePayload,
  preparedParams,
  stateSnapshot
}) {
  return {
    id: jobId,
    status: 'queued',
    progress: 0,
    stage: 'queued',
    stageMessage: 'Job queued',
    createdAt: startedIso,
    queuedAt: startedIso,
    startedAt: startedIso,
    configSummary: {
      formula_type: waveguidePayload.formula_type,
      frequency_range: [config.frequencyStart, config.frequencyEnd],
      num_frequencies: config.numFrequencies,
      sim_type: '2'
    },
    hasResults: false,
    hasMeshArtifact: false,
    label: `${outputName}_${counter}`,
    errorMessage: null,
    rating: null,
    autoExportCompletedAt: null,
    exportedFiles: [],
    scriptSchemaVersion: 1,
    script: {
      outputName,
      counter,
      frequencyStart: config.frequencyStart,
      frequencyEnd: config.frequencyEnd,
      numFrequencies: config.numFrequencies,
      meshValidationMode: config.meshValidationMode,
      frequencySpacing: config.frequencySpacing,
      deviceMode: config.deviceMode,
      useOptimized: config.useOptimized,
      enableSymmetry: config.enableSymmetry,
      verbose: config.verbose,
      polarConfig: config.polarConfig,
      params: { ...preparedParams },
      stateSnapshot
    }
  };
}

export function buildCancelledSimulationJob(job, { message = 'Simulation cancelled by user', completedAt } = {}) {
  if (!job || typeof job !== 'object' || !job.id) {
    return null;
  }
  return {
    ...job,
    id: job.id,
    status: 'cancelled',
    stage: 'cancelled',
    stageMessage: message,
    errorMessage: message,
    completedAt: completedAt || new Date().toISOString(),
    cancellationRequested: false
  };
}

export function buildCancellationRequestedSimulationJob(
  job,
  { message = 'Cancellation requested. Waiting for backend worker to stop.' } = {}
) {
  if (!job || typeof job !== 'object' || !job.id) {
    return null;
  }
  return {
    ...job,
    id: job.id,
    status: job.status === 'queued' ? 'queued' : 'running',
    stage: 'cancelling',
    stageMessage: message,
    errorMessage: null,
    completedAt: null,
    cancellationRequested: true
  };
}

export function resolveClearedFailedJobIds(localFailedIds = [], response = {}) {
  if (Array.isArray(response?.deleted_ids) && response.deleted_ids.length > 0) {
    return response.deleted_ids;
  }
  if (Number(response?.deleted_count) > 0) {
    return [...localFailedIds];
  }
  return [];
}
