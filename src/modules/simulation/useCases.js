import { DesignModule } from '../design/index.js';
import { SimulationModule } from './index.js';
import { GlobalState } from '../../state.js';

/**
 * Prepare the canonical simulation mesh payload.
 * Returns the payload for simulation or throws if invalid.
 */
export function prepareCanonicalSimulationMesh() {
  const designTask = DesignModule.task(
    DesignModule.importState(GlobalState.get(), {
      applyVerticalOffset: true
    })
  );
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const simulationTask = SimulationModule.task(SimulationModule.importDesign(designTask), {
    includeEnclosure: Number(preparedParams.encDepth || 0) > 0,
    adaptivePhi: false
  });
  const payload = SimulationModule.output.mesh(simulationTask);

  const vertexCount = payload.vertices.length / 3;
  const triangleCount = payload.indices.length / 3;

  const maxIndex = Math.max(...payload.indices);
  if (maxIndex >= vertexCount) {
    throw new Error(
      `Invalid mesh: max index ${maxIndex} >= vertex count ${vertexCount}. This indicates simulation mesh corruption.`
    );
  }

  return payload;
}

/**
 * Prepare an OCC adaptive solve request.
 * Returns { waveguidePayload, submitOptions, preparedParams }.
 */
export function prepareOccAdaptiveSolveRequest(options = {}) {
  const state = GlobalState.get();
  const designTask = DesignModule.task(
    DesignModule.importState(state, {
      applyVerticalOffset: true
    })
  );
  const preparedParams = DesignModule.output.simulationParams(designTask);
  const simulationInput = SimulationModule.importDesign(designTask);
  
  const { waveguidePayload, submitOptions } = SimulationModule.output.occAdaptive(simulationInput, {
    mshVersion: options.mshVersion || '2.2',
    simType: options.simType ?? 2
  });

  return { waveguidePayload, submitOptions, preparedParams, stateSnapshot: JSON.parse(JSON.stringify(state)) };
}

export function createSimulationClient() {
  return SimulationModule.output.client();
}

export function validateSimulationConfig(config = {}) {
  if (!Number.isFinite(config.frequencyStart) || !Number.isFinite(config.frequencyEnd)) {
    return 'Frequency range must contain valid numbers.';
  }
  if (!Number.isFinite(config.numFrequencies) || config.numFrequencies < 1) {
    return 'Number of frequencies must be at least 1.';
  }
  if (config.frequencyStart >= config.frequencyEnd) {
    return 'Start frequency must be less than end frequency.';
  }
  return null;
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
    exportedFiles: [],
    scriptSchemaVersion: 1,
    script: {
      outputName,
      counter,
      frequencyStart: config.frequencyStart,
      frequencyEnd: config.frequencyEnd,
      numFrequencies: config.numFrequencies,
      frequencySpacing: config.frequencySpacing,
      deviceMode: config.deviceMode,
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
    completedAt: completedAt || new Date().toISOString()
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
