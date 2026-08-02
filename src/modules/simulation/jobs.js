import { resolveDatedSolveLabel } from './naming.js';

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

// The full design state captured at submit time. Geometry exports for a job
// must come from this snapshot — the editor state may have diverged since the
// job ran, so it is never an acceptable substitute. `script.params` (prepared
// mesher params) is a derived form and is not equivalent either.
export function resolveJobDesignStateSnapshot(job) {
  const script = job?.script ?? job?.scriptSnapshot ?? null;
  const snapshot = script?.stateSnapshot;
  if (
    !isPlainObject(snapshot) ||
    typeof snapshot.type !== 'string' ||
    !isPlainObject(snapshot.params)
  ) {
    return null;
  }
  return snapshot;
}

export function buildQueuedSimulationJob({
  jobId,
  startedIso,
  outputName,
  counter,
  config,
  waveguidePayload,
  preparedParams,
  stateSnapshot,
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
    },
    hasResults: false,
    hasMeshArtifact: false,
    label: resolveDatedSolveLabel({ outputName, counter, timestamp: startedIso }),
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
      verbose: config.verbose,
      polarConfig: config.polarConfig,
      params: { ...preparedParams },
      stateSnapshot,
    },
  };
}

export function buildCancelledSimulationJob(
  job,
  { message = 'Simulation cancelled by user', completedAt } = {}
) {
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
    cancellationRequested: false,
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
    cancellationRequested: true,
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
