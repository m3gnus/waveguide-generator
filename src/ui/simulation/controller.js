import {
  createSimulationClient,
  prepareHornlabMesherSolveRequest,
} from '../../modules/simulation/domain.js';
import { readSimulationState } from '../../modules/simulation/state.js';
import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';
import {
  readSimulationWorkspaceJobs,
  syncSimulationWorkspaceJobManifest,
} from './workspaceTasks.js';
import { UiModule } from '../../modules/ui/index.js';
import {
  buildCancellationRequestedSimulationJob,
  buildCancelledSimulationJob,
  buildQueuedSimulationJob,
} from '../../modules/simulation/jobs.js';
import {
  createJobTracker,
  foldLocalJobMetadataIntoRemote,
  removeJob,
  setJobsFromEntries,
  persistPanelJobs,
  toUiJob,
  upsertJob,
} from './jobTracker.js';
import { setActiveJob } from './jobOrchestration.js';
import { getCachedRuntimeHealth } from '../runtimeCapabilities.js';
import { getFeatureBlockedReason } from '../dependencyStatus.js';

const ACTIVE_STATUSES = new Set(['queued', 'running']);
const JOB_SOURCE_MODES = Object.freeze({
  BACKEND: 'backend',
  FOLDER: 'folder',
});

const DEFAULT_SIMULATION_PARAM_BINDINGS = Object.freeze([
  { id: 'freq-start', key: 'freqStart', parse: (value) => parseFloat(value) },
  { id: 'freq-end', key: 'freqEnd', parse: (value) => parseFloat(value) },
  { id: 'freq-steps', key: 'numFreqs', parse: (value) => parseInt(value, 10) },
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
  'progressHideTimer',
  'pollDelayMs',
  'pollBackoffMs',
  'consecutivePollFailures',
  'isPolling',
  'stageStatusActive',
  'completedStatusMessage',
  'simulationStartedAtMs',
  'lastSimulationDurationMs',
  'currentSmoothing',
  'currentDirectivityReferenceLevel',
  'simulationParamBindings',
  'jobSourceMode',
  'jobSourceLabel',
]);

function isActiveJobStatus(status) {
  return ACTIVE_STATUSES.has(
    String(status || '')
      .trim()
      .toLowerCase()
  );
}

function findActiveJob(controller) {
  return Array.from(controller.jobs.values()).find((job) => isActiveJobStatus(job.status)) || null;
}

function hasActiveJobs(controller) {
  return findActiveJob(controller) !== null;
}

function syncCurrentJobId(controller) {
  controller.currentJobId = controller.activeJobId || null;
}

function resolveActiveJobSelection(controller) {
  const current = controller.activeJobId
    ? controller.jobs.get(controller.activeJobId) || null
    : null;
  const fallbackActiveJob = findActiveJob(controller);

  if (!current) {
    setActiveJob(controller, fallbackActiveJob?.id || null);
    return fallbackActiveJob;
  }

  if (isActiveJobStatus(current.status) || current.justCompleted) {
    setActiveJob(controller, current.id);
    return current;
  }

  if (fallbackActiveJob) {
    setActiveJob(controller, fallbackActiveJob.id);
    return fallbackActiveJob;
  }

  setActiveJob(controller, current.id);
  return current;
}

function setJobSourceMode(controller, mode) {
  const nextMode =
    mode === JOB_SOURCE_MODES.FOLDER ? JOB_SOURCE_MODES.FOLDER : JOB_SOURCE_MODES.BACKEND;
  controller.jobSourceMode = nextMode;
  controller.jobSourceLabel =
    nextMode === JOB_SOURCE_MODES.FOLDER ? 'Folder Tasks' : 'Backend Jobs';
}

function persistControllerJobs(controller) {
  persistPanelJobs(controller);
}

function cloneSimulationParamBindings() {
  return DEFAULT_SIMULATION_PARAM_BINDINGS.map((entry) => ({ ...entry }));
}

function normalizeExportPatch(exportPatch) {
  const normalizeArtifactFileName = (value) => {
    const text = String(value ?? '').trim();
    return text || null;
  };

  if (typeof exportPatch === 'string') {
    return {
      exportedFiles: [exportPatch],
      autoExportCompletedAt: null,
      justCompleted: false,
      rawResultsFile: null,
      meshArtifactFile: null,
    };
  }

  if (Array.isArray(exportPatch)) {
    return {
      exportedFiles: exportPatch.map((item) => String(item || '').trim()).filter(Boolean),
      autoExportCompletedAt: null,
      justCompleted: false,
      rawResultsFile: null,
      meshArtifactFile: null,
    };
  }

  if (!exportPatch || typeof exportPatch !== 'object') {
    return {
      exportedFiles: [],
      autoExportCompletedAt: null,
      justCompleted: false,
      rawResultsFile: null,
      meshArtifactFile: null,
    };
  }

  return {
    exportedFiles: Array.isArray(exportPatch.exportedFiles)
      ? exportPatch.exportedFiles.map((item) => String(item || '').trim()).filter(Boolean)
      : [],
    autoExportCompletedAt: exportPatch.autoExportCompletedAt ?? null,
    justCompleted: exportPatch.justCompleted ?? false,
    rawResultsFile: normalizeArtifactFileName(exportPatch.rawResultsFile),
    meshArtifactFile: normalizeArtifactFileName(exportPatch.meshArtifactFile),
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
    progressHideTimer: null,
    pollDelayMs: 1000,
    pollBackoffMs: 1000,
    consecutivePollFailures: 0,
    isPolling: false,
    stageStatusActive: false,
    completedStatusMessage: null,
    simulationStartedAtMs: null,
    lastSimulationDurationMs: null,
    currentSmoothing: 'none',
    currentDirectivityReferenceLevel: -6,
    simulationParamBindings: cloneSimulationParamBindings(),
    jobSourceMode: JOB_SOURCE_MODES.BACKEND,
    jobSourceLabel: 'Backend Jobs',
  };
}

export function createSimulationPanelRuntime(
  panelAdapter,
  {
    solver = createSimulationClient(),
    createUiCoordinator = createSimulationPanelUiCoordinator,
  } = {}
) {
  const controller = createSimulationControllerStore({ solver });
  bindSimulationControllerState(panelAdapter, controller);

  return {
    controller,
    uiCoordinator:
      typeof createUiCoordinator === 'function' ? createUiCoordinator(panelAdapter) : null,
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
      },
    });
  }
}

export async function restoreSimulationPanelRuntime(runtime, callbacks = {}) {
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

  if (controller?.progressHideTimer !== null && controller?.progressHideTimer !== undefined) {
    clearTimeout(controller.progressHideTimer);
    controller.progressHideTimer = null;
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
  { display = true, displayResults = null, activate = true, updateLastResults = true } = {}
) {
  const job = controller?.jobs?.get(jobId);
  if (!job) {
    return { ok: false, reason: 'missing_job', results: null, job: null };
  }

  if (activate) {
    setActiveJob(controller, jobId);
  }

  if (controller.resultCache?.has(jobId)) {
    const cached = controller.resultCache.get(jobId);
    if (updateLastResults) {
      controller.lastResults = cached;
    }
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
  if (updateLastResults) {
    controller.lastResults = results;
  }
  if (display && typeof displayResults === 'function') {
    displayResults(results);
  }
  return {
    ok: true,
    reason: 'fetched',
    results,
    job: controller.jobs.get(jobId) || job,
  };
}

export async function recordSimulationControllerExport(controller, jobId, exportPatch) {
  const current = controller?.jobs?.get(jobId);
  if (!current) {
    return null;
  }

  const normalizedPatch = normalizeExportPatch(exportPatch);
  const next = upsertJob(controller, {
    ...current,
    id: current.id,
    exportedFiles: mergeUniqueStrings(current.exportedFiles, normalizedPatch.exportedFiles),
    autoExportCompletedAt:
      normalizedPatch.autoExportCompletedAt ?? current.autoExportCompletedAt ?? null,
    justCompleted: normalizedPatch.justCompleted,
    rawResultsFile: normalizedPatch.rawResultsFile ?? current.rawResultsFile ?? null,
    meshArtifactFile: normalizedPatch.meshArtifactFile ?? current.meshArtifactFile ?? null,
  });
  persistControllerJobs(controller);
  if (next) {
    await syncSimulationWorkspaceJobManifest(next, {
      exportedFiles: next.exportedFiles,
      autoExportCompletedAt: next.autoExportCompletedAt,
      rawResultsFile: next.rawResultsFile ?? null,
      meshArtifactFile: next.meshArtifactFile ?? null,
    });
    await persistJobMetadataToBackend(controller, next);
  }
  return next;
}

export async function recordSimulationControllerRating(controller, jobId, rating) {
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
    rating: normalizedRating,
  });
  persistControllerJobs(controller);
  if (next) {
    await syncSimulationWorkspaceJobManifest(next, { rating: next.rating });
    await persistJobMetadataToBackend(controller, next);
  }
  return next;
}

export function prepareSimulationControllerSubmission(options = {}) {
  const requestOptions = {
    mshVersion: options.mshVersion || '2.2',
  };
  if (Object.prototype.hasOwnProperty.call(options, 'simType')) {
    requestOptions.simType = options.simType;
  }
  if (Object.prototype.hasOwnProperty.call(options, 'solverMode')) {
    requestOptions.solverMode = options.solverMode;
  }
  return prepareHornlabMesherSolveRequest(readSimulationState(), requestOptions);
}

export async function submitSimulationControllerJob(
  controller,
  {
    config,
    meshData,
    outputName,
    counter,
    submission = prepareSimulationControllerSubmission(),
  } = {}
) {
  const health = await controller.solver.getHealthStatus();

  const solverReady = Boolean(
    health?.solverReady ||
    health?.solverBackends?.metal?.ready ||
    health?.solverBackends?.bempp?.ready
  );

  if (!solverReady || !health?.mesherReady) {
    const cachedHealth = getCachedRuntimeHealth() || health;
    const blockedReason = getFeatureBlockedReason(cachedHealth, 'bem-solve');
    throw new Error(
      blockedReason || 'Metal BEM or Bempp and HornLab mesher must be ready to run simulation.'
    );
  }

  const waveguidePayload = { ...submission.waveguidePayload };

  const submitOptions = {
    ...submission.submitOptions,
    mesh: {
      ...(submission.submitOptions?.mesh || {}),
      waveguide_params: waveguidePayload,
    },
  };
  const submitConfig = {
    ...config,
    simulationType: config?.simulationType ?? waveguidePayload.sim_type ?? '2',
    solverMode: config?.solverMode ?? waveguidePayload.solver_mode ?? 'auto',
  };
  const { preparedParams, stateSnapshot } = submission;
  const startedIso = new Date().toISOString();
  const jobId = await controller.solver.submitSimulation(submitConfig, meshData, submitOptions);
  const createdJob = await queueSimulationControllerJob(controller, {
    jobId,
    startedIso,
    outputName,
    counter,
    config: submitConfig,
    waveguidePayload,
    preparedParams,
    stateSnapshot,
  });

  return {
    health,
    jobId,
    createdJob,
  };
}

function resolveControllerBackendUrl(controller) {
  return controller?.solver?.backendUrl || DEFAULT_BACKEND_URL;
}

async function persistJobMetadataToBackend(controller, job) {
  const metadata = {};
  if (job.label) metadata.label = job.label;
  if (job.script) metadata.script_snapshot = job.script;
  if (job.rating !== undefined) metadata.rating = job.rating;
  if (Array.isArray(job.exportedFiles)) metadata.exported_files = job.exportedFiles;
  if (job.autoExportCompletedAt !== undefined) {
    metadata.auto_export_completed_at = job.autoExportCompletedAt;
  }
  if (job.rawResultsFile !== undefined) metadata.raw_results_file = job.rawResultsFile;
  if (job.meshArtifactFile !== undefined) metadata.mesh_artifact_file = job.meshArtifactFile;
  if (Object.keys(metadata).length === 0) return true;

  const backendUrl = resolveControllerBackendUrl(controller);
  try {
    const response = await fetch(`${backendUrl}/api/jobs/${encodeURIComponent(job.id)}/metadata`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(metadata),
      signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) {
      throw new Error(`metadata update failed (${response.status})`);
    }
    return true;
  } catch (err) {
    console.warn('Failed to persist job metadata to backend:', err);
    return false;
  }
}

export async function queueSimulationControllerJob(controller, jobInput) {
  const createdJob = upsertJob(controller, buildQueuedSimulationJob(jobInput));
  setActiveJob(controller, jobInput?.jobId);
  persistControllerJobs(controller);
  if (createdJob) {
    await syncSimulationWorkspaceJobManifest(createdJob);
    // Persist label and script snapshot to backend so they survive page reloads.
    if (createdJob.id) {
      void persistJobMetadataToBackend(controller, createdJob);
    }
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
    stopResult,
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
    message,
  });
  if (pendingJob) {
    upsertJob(controller, pendingJob);
  }
  persistControllerJobs(controller);
  return pendingJob;
}

export function applyStoppedSimulationControllerJob(controller, jobId, stopResult = {}) {
  const responseStatus = String(stopResult?.status || '')
    .trim()
    .toLowerCase();
  if (responseStatus === 'cancelled') {
    return cancelSimulationControllerJob(controller, jobId);
  }
  return requestSimulationControllerJobCancellation(controller, jobId, {
    message:
      String(stopResult?.message || '').trim() ||
      'Cancellation requested. Waiting for backend worker to stop.',
  });
}

export async function reconcileSimulationControllerRemoteJobs(
  controller,
  { onManifestSyncError = null } = {}
) {
  // Only check status for jobs already tracked locally (queued/running).
  const activeEntries = Array.from(controller.jobs.values()).filter((job) =>
    isActiveJobStatus(job.status)
  );

  for (const localJob of activeEntries) {
    try {
      const remote = await controller.solver.getJobStatus(localJob.id);
      const updated = toUiJob({ ...remote, id: localJob.id });
      if (updated?.id) {
        // Only sync workspace manifest when job state materially changes
        // (status transition or completion), not on every poll tick.
        const statusChanged = localJob.status !== updated.status;
        const justCompleted = updated.status === 'complete' && localJob.status !== 'complete';

        const next = upsertJob(controller, {
          ...updated,
          justCompleted: justCompleted || localJob.justCompleted === true,
        });

        if (statusChanged || justCompleted) {
          syncSimulationWorkspaceJobManifest(next || updated).catch((error) => {
            if (typeof onManifestSyncError === 'function') {
              onManifestSyncError(error, next || updated);
            }
          });
        }
      }
    } catch (error) {
      console.warn(`Status check failed for job ${localJob.id}:`, error);
    }
  }

  const activeJob = resolveActiveJobSelection(controller);
  persistControllerJobs(controller);

  return {
    activeJob,
    anyActive: hasActiveJobs(controller),
  };
}

export async function restoreSimulationControllerJobs(
  controller,
  { onJobsUpdated = () => {}, onStartPolling = () => {}, onRecoverFromManifests = () => {} } = {}
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

  // Restore all jobs from the backend database (survives page reloads).
  setJobSourceMode(controller, JOB_SOURCE_MODES.BACKEND);
  let backendRequestSucceeded = false;

  if (typeof controller.solver?.listJobs === 'function') {
    try {
      const pageSize = 200;
      const items = [];
      let offset = 0;
      while (true) {
        const response = await controller.solver.listJobs({
          limit: pageSize,
          offset,
        });
        const pageItems = Array.isArray(response?.items) ? response.items : [];
        items.push(...pageItems);
        const total = Number(response?.total);
        offset += pageItems.length;
        if (
          pageItems.length === 0 ||
          pageItems.length < pageSize ||
          (Number.isFinite(total) && offset >= total)
        ) {
          break;
        }
      }
      // A refresh must not discard local-only job metadata (label, script)
      // that is still being PATCHed to the backend.
      setJobsFromEntries(
        controller,
        foldLocalJobMetadataIntoRemote(Array.from(controller.jobs.values()), items)
      );
      setJobSourceMode(controller, JOB_SOURCE_MODES.BACKEND);
      backendRequestSucceeded = true;
    } catch (error) {
      console.warn('[SimController] Failed to restore jobs from backend:', error);
    }
  }

  // Fall back to workspace manifests only when the backend could not be queried.
  if (!backendRequestSucceeded) {
    const workspace = await readSimulationWorkspaceJobs();
    if (workspace.repaired || workspace.warnings.length > 0) {
      onRecoverFromManifests();
    }
    setJobsFromEntries(controller, workspace.items);
    setJobSourceMode(
      controller,
      workspace.available ? JOB_SOURCE_MODES.FOLDER : JOB_SOURCE_MODES.BACKEND
    );
  }

  syncCurrentJobId(controller);
  onJobsUpdated();

  if (controller.activeJobId || hasActiveJobs(controller)) {
    onStartPolling();
  }
}
