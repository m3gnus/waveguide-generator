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
      sim_type: '2',
      enable_symmetry: config.enableSymmetry
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
      advancedSettings: config.advancedSettings ? { ...config.advancedSettings } : null,
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
