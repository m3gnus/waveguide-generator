// @ts-check

import {
  createSimulationClient,
  prepareOccAdaptiveSolveRequest
} from '../../modules/simulation/domain.js';
import {
  readSimulationState
} from '../../modules/simulation/useCases.js';
import {
  readSimulationWorkspaceJobs,
  syncSimulationWorkspaceJobManifest
} from '../../modules/simulation/workspaceTasks.js';
import { UiModule } from '../../modules/ui/index.js';
import {
  buildCancellationRequestedSimulationJob,
  buildCancelledSimulationJob,
  buildQueuedSimulationJob
} from '../../modules/simulation/useCases.js';
import {
  allJobs,
  createJobTracker,
  loadLocalIndex,
  mergeJobs,
  removeJob,
  setJobsFromEntries,
  persistPanelJobs,
  toUiJob,
  upsertJob
} from './jobTracker.js';
import { setActiveJob } from './jobOrchestration.js';

const ACTIVE_STATUSES = new Set(['queued', 'running']);
const JOB_SOURCE_MODES = Object.freeze({
  BACKEND: 'backend',
  FOLDER: 'folder'
});

const DEFAULT_SIMULATION_PARAM_BINDINGS = Object.freeze([
  { id: 'freq-start', key: 'freqStart', parse: (value) => parseFloat(value) },
  { id: 'freq-end', key: 'freqEnd', parse: (value) => parseFloat(value) },
  { id: 'freq-steps', key: 'numFreqs', parse: (value) => parseInt(value, 10) }
]);

export const SIMULATION_CONTROLLER_FIELDS = Object.freeze([
  'solver',
  'currentJobId',
  'pollInterval',
  'connectionPollTimer',
  'lastResults',
  'jobs',
  'resultCache',
  'activeJobId',
  'pollTimer',
  'pollDelayMs',
  'pollBackoffMs',
  'consecutivePollFailures',
  'isPolling',
  'stageStatusActive',
  'completedStatusMessage',
  'simulationStartedAtMs',
  'lastSimulationDurationMs',
  'currentSmoothing',
  'simulationParamBindings',
  'jobSourceMode',
  'jobSourceLabel'
]);

function hasActiveJobs(controller) {
  return Array.from(controller.jobs.values()).some((job) => ACTIVE_STATUSES.has(job.status));
}

function syncCurrentJobId(controller) {
  controller.currentJobId = controller.activeJobId || null;
}

function setJobSourceMode(controller, mode) {
  const nextMode = mode === JOB_SOURCE_MODES.FOLDER ? JOB_SOURCE_MODES.FOLDER : JOB_SOURCE_MODES.BACKEND;
  controller.jobSourceMode = nextMode;
  controller.jobSourceLabel = nextMode === JOB_SOURCE_MODES.FOLDER ? 'Folder Tasks' : 'Backend Jobs';
}

function persistControllerJobs(controller) {
  if (controller?.jobSourceMode === JOB_SOURCE_MODES.FOLDER) {
    return;
  }
  persistPanelJobs(controller);
}

function cloneSimulationParamBindings() {
  return DEFAULT_SIMULATION_PARAM_BINDINGS.map((entry) => ({ ...entry }));
}

function normalizeExportPatch(exportPatch) {
  if (typeof exportPatch === 'string') {
    return {
      exportedFiles: [exportPatch],
      autoExportCompletedAt: null,
      justCompleted: false
    };
  }

  if (Array.isArray(exportPatch)) {
    return {
      exportedFiles: exportPatch.map((item) => String(item || '').trim()).filter(Boolean),
      autoExportCompletedAt: null,
      justCompleted: false
    };
  }

  if (!exportPatch || typeof exportPatch !== 'object') {
    return {
      exportedFiles: [],
      autoExportCompletedAt: null,
      justCompleted: false
    };
  }

  return {
    exportedFiles: Array.isArray(exportPatch.exportedFiles)
      ? exportPatch.exportedFiles.map((item) => String(item || '').trim()).filter(Boolean)
      : [],
    autoExportCompletedAt: exportPatch.autoExportCompletedAt ?? null,
    justCompleted: exportPatch.justCompleted ?? false
  };
}

function mergeUniqueStrings(...lists) {
  const seen = new Set();
  const merged = [];
  for (const list of lists) {
    for (const item of list || []) {
      const value = String(item || '').trim();
      if (!value || seen.has(value)) {
        continue;
      }
      seen.add(value);
      merged.push(value);
    }
  }
  return merged;
}

function createSimulationPanelUiCoordinator(panelAdapter) {
  return UiModule.output.simulationPanel(
    UiModule.task(UiModule.importSimulationPanel(panelAdapter))
  );
}

export function createSimulationControllerStore({ solver = createSimulationClient() } = {}) {
  return {
    solver,
    currentJobId: null,
    pollInterval: null,
    connectionPollTimer: null,
    lastResults: null,
    jobs: new Map(),
    resultCache: new Map(),
    activeJobId: null,
    pollTimer: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
    consecutivePollFailures: 0,
    isPolling: false,
    stageStatusActive: false,
    completedStatusMessage: null,
    simulationStartedAtMs: null,
    lastSimulationDurationMs: null,
    currentSmoothing: 'none',
    simulationParamBindings: cloneSimulationParamBindings(),
    jobSourceMode: JOB_SOURCE_MODES.BACKEND,
    jobSourceLabel: 'Backend Jobs'
  };
}

export function createSimulationPanelRuntime(
  panelAdapter,
  {
    solver = createSimulationClient(),
    createUiCoordinator = createSimulationPanelUiCoordinator
  } = {}
) {
  const controller = createSimulationControllerStore({ solver });
  bindSimulationControllerState(panelAdapter, controller);

  return {
    controller,
    uiCoordinator: typeof createUiCoordinator === 'function'
      ? createUiCoordinator(panelAdapter)
      : null
  };
}

export function bindSimulationControllerState(panelAdapter, controller) {
  for (const key of SIMULATION_CONTROLLER_FIELDS) {
    Object.defineProperty(panelAdapter, key, {
      configurable: true,
      enumerable: true,
      get() {
        return controller[key];
      },
      set(nextValue) {
        controller[key] = nextValue;
      }
    });
  }
}

export async function restoreSimulationPanelRuntime(
  runtime,
  callbacks = {}
) {
  return restoreSimulationControllerJobs(runtime?.controller, callbacks);
}

export function disposeSimulationPanelRuntime(runtime) {
  const controller = runtime?.controller;
  if (controller?.pollTimer) {
    clearTimeout(controller.pollTimer);
    controller.pollTimer = null;
    controller.pollInterval = null;
    controller.isPolling = false;
  }

  if (controller?.connectionPollTimer) {
    clearTimeout(controller.connectionPollTimer);
    controller.connectionPollTimer = null;
  }

  if (runtime?.uiCoordinator) {
    runtime.uiCoordinator.dispose();
  }
}

export async function ensureSimulationControllerJobResults(
  controller,
  jobId,
  { display = true, displayResults = null } = {}
) {
  const job = controller?.jobs?.get(jobId);
  if (!job) {
    return { ok: false, reason: 'missing_job', results: null, job: null };
  }

  setActiveJob(controller, jobId);

  if (controller.resultCache?.has(jobId)) {
    const cached = controller.resultCache.get(jobId);
    controller.lastResults = cached;
    if (display && typeof displayResults === 'function') {
      displayResults(cached);
    }
    return { ok: true, reason: 'cached', results: cached, job };
  }

  if (job.status !== 'complete') {
    return { ok: false, reason: 'not_complete', results: null, job };
  }

  const results = await controller.solver.getResults(jobId);
  controller.resultCache.set(jobId, results);
  controller.lastResults = results;
  if (display && typeof displayResults === 'function') {
    displayResults(results);
  }
  return { ok: true, reason: 'fetched', results, job: controller.jobs.get(jobId) || job };
}

export async function recordSimulationControllerExport(
  controller,
  jobId,
  exportPatch
) {
  const current = controller?.jobs?.get(jobId);
  if (!current) {
    return null;
  }

  const normalizedPatch = normalizeExportPatch(exportPatch);
  const next = upsertJob(controller, {
    ...current,
    id: current.id,
    exportedFiles: mergeUniqueStrings(current.exportedFiles, normalizedPatch.exportedFiles),
    autoExportCompletedAt: normalizedPatch.autoExportCompletedAt ?? current.autoExportCompletedAt ?? null,
    justCompleted: normalizedPatch.justCompleted
  });
  persistControllerJobs(controller);
  if (next) {
    await syncSimulationWorkspaceJobManifest(next, {
      exportedFiles: next.exportedFiles,
      autoExportCompletedAt: next.autoExportCompletedAt
    });
  }
  return next;
}

export async function recordSimulationControllerRating(
  controller,
  jobId,
  rating
) {
  const current = controller?.jobs?.get(jobId);
  if (!current) {
    return null;
  }

  const numericRating = Number(rating);
  const normalizedRating = Number.isFinite(numericRating)
    ? Math.max(0, Math.min(5, Math.round(numericRating)))
    : null;

  const next = upsertJob(controller, {
    ...current,
    id: current.id,
    rating: normalizedRating
  });
  persistControllerJobs(controller);
  if (next) {
    await syncSimulationWorkspaceJobManifest(next, { rating: next.rating });
  }
  return next;
}

export function prepareSimulationControllerSubmission(
  options = {}
) {
  return prepareOccAdaptiveSolveRequest(readSimulationState(), {
    mshVersion: options.mshVersion || '2.2',
    simType: options.simType ?? 2
  });
}

export async function submitSimulationControllerJob(
  controller,
  {
    config,
    meshData,
    outputName,
    counter,
    submission = prepareSimulationControllerSubmission()
  } = {}
) {
  const health = await controller.solver.getHealthStatus();
  if (!health?.solverReady || !health?.occBuilderReady) {
    throw new Error('Backend solver and OCC mesher must be ready to run adaptive BEM simulation.');
  }

  const {
    waveguidePayload,
    submitOptions,
    preparedParams,
    stateSnapshot
  } = submission;
  const startedIso = new Date().toISOString();
  const jobId = await controller.solver.submitSimulation(config, meshData, submitOptions);
  const createdJob = await queueSimulationControllerJob(controller, {
    jobId,
    startedIso,
    outputName,
    counter,
    config,
    waveguidePayload,
    preparedParams,
    stateSnapshot
  });

  return {
    health,
    jobId,
    createdJob
  };
}

export async function queueSimulationControllerJob(controller, jobInput) {
  const createdJob = upsertJob(controller, buildQueuedSimulationJob(jobInput));
  setActiveJob(controller, jobInput?.jobId);
  persistControllerJobs(controller);
  if (createdJob) {
    await syncSimulationWorkspaceJobManifest(createdJob);
  }
  return createdJob;
}

export function removeSimulationControllerJob(controller, jobId) {
  const removed = removeJob(controller, jobId);
  if (removed) {
    persistControllerJobs(controller);
  }
  return removed;
}

export async function stopSimulationControllerJob(controller, jobId) {
  let stopError = null;
  let stopResult = null;

  if (jobId) {
    try {
      stopResult = await controller.solver.stopJob(jobId);
    } catch (error) {
      stopError = error;
    }
  }

  const cancelledJob = stopError
    ? null
    : applyStoppedSimulationControllerJob(controller, jobId, stopResult);
  return {
    cancelledJob,
    stopError,
    stopResult
  };
}

export function clearSimulationControllerJobs(controller, jobIds = []) {
  let removed = 0;
  for (const jobId of jobIds) {
    if (removeJob(controller, jobId)) {
      removed += 1;
    }
  }
  persistControllerJobs(controller);
  return removed;
}

export function cancelSimulationControllerJob(controller, jobId) {
  if (!jobId || !controller?.jobs?.has(jobId)) {
    persistControllerJobs(controller);
    return null;
  }

  const cancelledJob = buildCancelledSimulationJob(controller.jobs.get(jobId));
  if (cancelledJob) {
    upsertJob(controller, cancelledJob);
  }
  persistControllerJobs(controller);
  return cancelledJob;
}

export function requestSimulationControllerJobCancellation(
  controller,
  jobId,
  { message = 'Cancellation requested. Waiting for backend worker to stop.' } = {}
) {
  if (!jobId || !controller?.jobs?.has(jobId)) {
    persistControllerJobs(controller);
    return null;
  }

  const pendingJob = buildCancellationRequestedSimulationJob(controller.jobs.get(jobId), {
    message
  });
  if (pendingJob) {
    upsertJob(controller, pendingJob);
  }
  persistControllerJobs(controller);
  return pendingJob;
}

export function applyStoppedSimulationControllerJob(controller, jobId, stopResult = {}) {
  const responseStatus = String(stopResult?.status || '').trim().toLowerCase();
  if (responseStatus === 'cancelled') {
    return cancelSimulationControllerJob(controller, jobId);
  }
  return requestSimulationControllerJobCancellation(controller, jobId, {
    message: String(stopResult?.message || '').trim()
      || 'Cancellation requested. Waiting for backend worker to stop.'
  });
}

export async function reconcileSimulationControllerRemoteJobs(
  controller,
  {
    listQuery = { limit: 200, offset: 0 },
    onManifestSyncError = null
  } = {}
) {
  const payload = await controller.solver.listJobs(listQuery);
  const remoteItems = Array.isArray(payload?.items)
    ? payload.items.map((item) => toUiJob(item)).filter((item) => item?.id)
    : [];
  const merged = mergeJobs(allJobs(controller), remoteItems);
  setJobsFromEntries(controller, merged);
  setActiveJob(controller, controller.activeJobId || null);
  persistControllerJobs(controller);

  for (const item of remoteItems) {
    syncSimulationWorkspaceJobManifest(item).catch((error) => {
      if (typeof onManifestSyncError === 'function') {
        onManifestSyncError(error, item);
      }
    });
  }

  const activeJob = controller.activeJobId ? (controller.jobs.get(controller.activeJobId) || null) : null;
  return {
    remoteItems,
    activeJob,
    anyActive: hasActiveJobs(controller)
  };
}

export async function restoreSimulationControllerJobs(
  controller,
  {
    onJobsUpdated = () => {},
    onStartPolling = () => {},
    onRecoverFromManifests = () => {}
  } = {}
) {
  if (controller.pollTimer) {
    clearTimeout(controller.pollTimer);
  }

  const tracker = createJobTracker();
  controller.jobs = tracker.jobs;
  controller.resultCache = tracker.resultCache;
  controller.activeJobId = tracker.activeJobId;
  controller.pollTimer = tracker.pollTimer;
  controller.pollDelayMs = tracker.pollDelayMs;
  controller.pollBackoffMs = tracker.pollBackoffMs;
  controller.consecutivePollFailures = Number(tracker.consecutivePollFailures) || 0;
  controller.isPolling = tracker.isPolling;
  syncCurrentJobId(controller);

  const local = loadLocalIndex();
  let seedItems = local;
  const workspace = await readSimulationWorkspaceJobs();
  const useFolderSource = workspace.available;

  setJobSourceMode(controller, useFolderSource ? JOB_SOURCE_MODES.FOLDER : JOB_SOURCE_MODES.BACKEND);
  if (useFolderSource) {
    seedItems = workspace.items;
  }
  if (workspace.repaired || workspace.warnings.length > 0) {
    onRecoverFromManifests();
  }

  setJobsFromEntries(controller, seedItems);
  syncCurrentJobId(controller);
  onJobsUpdated();

  if (useFolderSource) {
    return;
  }

  try {
    const remote = await controller.solver.listJobs({ limit: 200, offset: 0 });
    const merged = mergeJobs(seedItems, remote.items || []);
    setJobsFromEntries(controller, merged);
    syncCurrentJobId(controller);
    persistControllerJobs(controller);

    onJobsUpdated();
    if (controller.activeJobId || hasActiveJobs(controller)) {
      onStartPolling();
    }
  } catch (_error) {
    persistControllerJobs(controller);
    onJobsUpdated();
  }
}
