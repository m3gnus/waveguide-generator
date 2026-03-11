import { DesignModule } from '../design/index.js';
import { SimulationModule } from './index.js';
import { GlobalState } from '../../state.js';
import { getSelectedFolderHandle } from '../../ui/workspace/folderWorkspace.js';
import {
  buildTaskIndexEntriesFromJobs,
  loadTaskIndex,
  rebuildIndexFromManifests,
  writeTaskIndex
} from '../../ui/workspace/taskIndex.js';
import { updateTaskManifestForJob } from '../../ui/workspace/taskManifest.js';

let pendingSimulationWorkspaceIndexSync = Promise.resolve({
  synced: false,
  available: false,
  items: []
});

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function normalizeWarningList(...values) {
  return values.flat().map((value) => String(value || '').trim()).filter(Boolean);
}

const CANONICAL_TAG_ORDER = Object.freeze([1, 2, 3, 4]);

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

/**
 * Prepare the canonical simulation mesh payload.
 * Returns the payload for simulation or throws if invalid.
 */
export function prepareCanonicalSimulationMesh() {
  const designTask = DesignModule.task(
    DesignModule.importState(readSimulationState(), {
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

export function summarizeCanonicalSimulationMesh(meshData = {}) {
  const vertices = Array.isArray(meshData?.vertices) ? meshData.vertices : [];
  const indices = Array.isArray(meshData?.indices) ? meshData.indices : [];
  const surfaceTags = Array.isArray(meshData?.surfaceTags) ? meshData.surfaceTags : [];
  const warnings = [];

  if (vertices.length % 3 !== 0) {
    warnings.push('Vertex array length is not divisible by 3.');
  }
  if (indices.length % 3 !== 0) {
    warnings.push('Triangle index array length is not divisible by 3.');
  }

  const vertexCount = Math.floor(vertices.length / 3);
  const triangleCount = Math.floor(indices.length / 3);
  const tagCounts = Object.fromEntries(CANONICAL_TAG_ORDER.map((tag) => [tag, 0]));
  const unsupportedTags = new Set();

  for (const rawTag of surfaceTags) {
    const tag = Number(rawTag);
    if (Object.hasOwn(tagCounts, tag)) {
      tagCounts[tag] += 1;
    } else {
      unsupportedTags.add(tag);
    }
  }

  if (surfaceTags.length !== triangleCount) {
    warnings.push(
      `Surface tag count ${surfaceTags.length} does not match triangle count ${triangleCount}.`
    );
  }
  if (tagCounts[2] === 0) {
    warnings.push('Source surface tag (2) missing from the canonical simulation mesh.');
  }
  if (unsupportedTags.size > 0) {
    warnings.push(`Unsupported surface tags present: ${Array.from(unsupportedTags).sort((a, b) => a - b).join(', ')}.`);
  }

  return {
    vertexCount,
    triangleCount,
    tagCounts,
    warnings,
    ok: warnings.length === 0
  };
}

/**
 * Prepare an OCC adaptive solve request.
 * Returns { waveguidePayload, submitOptions, preparedParams }.
 */
export function prepareOccAdaptiveSolveRequest(options = {}) {
  const state = readSimulationState();
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

export async function readSimulationWorkspaceJobs() {
  const folderHandle = getSelectedFolderHandle();
  if (!folderHandle) {
    return {
      items: [],
      available: false,
      repaired: false,
      warnings: []
    };
  }

  const indexResult = await loadTaskIndex(folderHandle);
  if (indexResult.items.length > 0) {
    return {
      items: indexResult.items,
      available: true,
      repaired: false,
      warnings: normalizeWarningList(indexResult.warning)
    };
  }

  const rebuilt = await rebuildIndexFromManifests(folderHandle);
  if (rebuilt.items.length > 0) {
    await writeTaskIndex(folderHandle, rebuilt.items);
  }

  return {
    items: rebuilt.items,
    available: true,
    repaired: rebuilt.items.length > 0,
    warnings: normalizeWarningList(indexResult.warning, rebuilt.warnings)
  };
}

export function syncSimulationWorkspaceIndex(jobEntries = []) {
  const folderHandle = getSelectedFolderHandle();
  if (!folderHandle) {
    return Promise.resolve({
      synced: false,
      available: false,
      items: []
    });
  }

  const items = buildTaskIndexEntriesFromJobs(jobEntries);
  pendingSimulationWorkspaceIndexSync = pendingSimulationWorkspaceIndexSync.then(async () => {
    try {
      await writeTaskIndex(folderHandle, items);
      return {
        synced: true,
        available: true,
        items
      };
    } catch (error) {
      console.warn('Simulation workspace index sync failed:', error);
      return {
        synced: false,
        available: true,
        items,
        error
      };
    }
  });

  return pendingSimulationWorkspaceIndexSync;
}

export async function syncSimulationWorkspaceJobManifest(job, updates = null) {
  const folderHandle = getSelectedFolderHandle();
  if (!folderHandle || !job?.id) {
    return null;
  }

  const nextUpdates = isObject(updates) ? updates : {};
  const result = await updateTaskManifestForJob(folderHandle, job, nextUpdates);
  if (result.warning) {
    console.warn(result.warning);
  }
  return result.manifest;
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
