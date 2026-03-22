import {
  createSimulationClient,
  prepareOccAdaptiveSolveRequest,
} from "../../modules/simulation/domain.js";
import { readSimulationState } from "../../modules/simulation/state.js";
import {
  readSimulationWorkspaceJobs,
  syncSimulationWorkspaceJobManifest,
} from "./workspaceTasks.js";
import { UiModule } from "../../modules/ui/index.js";
import {
  buildCancellationRequestedSimulationJob,
  buildCancelledSimulationJob,
  buildQueuedSimulationJob,
} from "../../modules/simulation/jobs.js";
import {
  allJobs,
  createJobTracker,
  removeJob,
  setJobsFromEntries,
  persistPanelJobs,
  toUiJob,
  upsertJob,
} from "./jobTracker.js";
import { setActiveJob } from "./jobOrchestration.js";
import { getCachedRuntimeHealth } from "../runtimeCapabilities.js";
import { getFeatureBlockedReason } from "../dependencyStatus.js";

const ACTIVE_STATUSES = new Set(["queued", "running"]);
const JOB_SOURCE_MODES = Object.freeze({
  BACKEND: "backend",
  FOLDER: "folder",
});

const DEFAULT_SIMULATION_PARAM_BINDINGS = Object.freeze([
  { id: "freq-start", key: "freqStart", parse: (value) => parseFloat(value) },
  { id: "freq-end", key: "freqEnd", parse: (value) => parseFloat(value) },
  { id: "freq-steps", key: "numFreqs", parse: (value) => parseInt(value, 10) },
]);

export const SIMULATION_CONTROLLER_FIELDS = Object.freeze([
  "solver",
  "currentJobId",
  "pollInterval",
  "connectionPollTimer",
  "lastResults",
  "jobs",
  "resultCache",
  "activeJobId",
  "pollTimer",
  "pollDelayMs",
  "pollBackoffMs",
  "consecutivePollFailures",
  "isPolling",
  "stageStatusActive",
  "completedStatusMessage",
  "simulationStartedAtMs",
  "lastSimulationDurationMs",
  "currentSmoothing",
  "currentDirectivityReferenceLevel",
  "simulationParamBindings",
  "jobSourceMode",
  "jobSourceLabel",
]);

function hasActiveJobs(controller) {
  return Array.from(controller.jobs.values()).some((job) =>
    ACTIVE_STATUSES.has(job.status),
  );
}

function syncCurrentJobId(controller) {
  controller.currentJobId = controller.activeJobId || null;
}

function setJobSourceMode(controller, mode) {
  const nextMode =
    mode === JOB_SOURCE_MODES.FOLDER
      ? JOB_SOURCE_MODES.FOLDER
      : JOB_SOURCE_MODES.BACKEND;
  controller.jobSourceMode = nextMode;
  controller.jobSourceLabel =
    nextMode === JOB_SOURCE_MODES.FOLDER ? "Folder Tasks" : "Backend Jobs";
}

function persistControllerJobs(controller) {
  persistPanelJobs(controller);
}

function cloneSimulationParamBindings() {
  return DEFAULT_SIMULATION_PARAM_BINDINGS.map((entry) => ({ ...entry }));
}

function normalizeExportPatch(exportPatch) {
  const normalizeArtifactFileName = (value) => {
    const text = String(value ?? "").trim();
    return text || null;
  };

  if (typeof exportPatch === "string") {
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
      exportedFiles: exportPatch
        .map((item) => String(item || "").trim())
        .filter(Boolean),
      autoExportCompletedAt: null,
      justCompleted: false,
      rawResultsFile: null,
      meshArtifactFile: null,
    };
  }

  if (!exportPatch || typeof exportPatch !== "object") {
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
      ? exportPatch.exportedFiles
          .map((item) => String(item || "").trim())
          .filter(Boolean)
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
      const value = String(item || "").trim();
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
    UiModule.task(UiModule.importSimulationPanel(panelAdapter)),
  );
}

export function createSimulationControllerStore({
  solver = createSimulationClient(),
} = {}) {
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
    currentSmoothing: "none",
    currentDirectivityReferenceLevel: -6,
    simulationParamBindings: cloneSimulationParamBindings(),
    jobSourceMode: JOB_SOURCE_MODES.BACKEND,
    jobSourceLabel: "Backend Jobs",
  };
}

export function createSimulationPanelRuntime(
  panelAdapter,
  {
    solver = createSimulationClient(),
    createUiCoordinator = createSimulationPanelUiCoordinator,
  } = {},
) {
  const controller = createSimulationControllerStore({ solver });
  bindSimulationControllerState(panelAdapter, controller);

  return {
    controller,
    uiCoordinator:
      typeof createUiCoordinator === "function"
        ? createUiCoordinator(panelAdapter)
        : null,
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
  { display = true, displayResults = null } = {},
) {
  const job = controller?.jobs?.get(jobId);
  if (!job) {
    return { ok: false, reason: "missing_job", results: null, job: null };
  }

  setActiveJob(controller, jobId);

  if (controller.resultCache?.has(jobId)) {
    const cached = controller.resultCache.get(jobId);
    controller.lastResults = cached;
    if (display && typeof displayResults === "function") {
      displayResults(cached);
    }
    return { ok: true, reason: "cached", results: cached, job };
  }

  if (job.status !== "complete") {
    return { ok: false, reason: "not_complete", results: null, job };
  }

  const results = await controller.solver.getResults(jobId);
  controller.resultCache.set(jobId, results);
  controller.lastResults = results;
  if (display && typeof displayResults === "function") {
    displayResults(results);
  }
  return {
    ok: true,
    reason: "fetched",
    results,
    job: controller.jobs.get(jobId) || job,
  };
}

export async function recordSimulationControllerExport(
  controller,
  jobId,
  exportPatch,
) {
  const current = controller?.jobs?.get(jobId);
  if (!current) {
    return null;
  }

  const normalizedPatch = normalizeExportPatch(exportPatch);
  const next = upsertJob(controller, {
    ...current,
    id: current.id,
    exportedFiles: mergeUniqueStrings(
      current.exportedFiles,
      normalizedPatch.exportedFiles,
    ),
    autoExportCompletedAt:
      normalizedPatch.autoExportCompletedAt ??
      current.autoExportCompletedAt ??
      null,
    justCompleted: normalizedPatch.justCompleted,
    rawResultsFile:
      normalizedPatch.rawResultsFile ?? current.rawResultsFile ?? null,
    meshArtifactFile:
      normalizedPatch.meshArtifactFile ?? current.meshArtifactFile ?? null,
  });
  persistControllerJobs(controller);
  if (next) {
    await syncSimulationWorkspaceJobManifest(next, {
      exportedFiles: next.exportedFiles,
      autoExportCompletedAt: next.autoExportCompletedAt,
      rawResultsFile: next.rawResultsFile ?? null,
      meshArtifactFile: next.meshArtifactFile ?? null,
    });
  }
  return next;
}

export async function recordSimulationControllerRating(
  controller,
  jobId,
  rating,
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
    rating: normalizedRating,
  });
  persistControllerJobs(controller);
  if (next) {
    await syncSimulationWorkspaceJobManifest(next, { rating: next.rating });
  }
  return next;
}

export function prepareSimulationControllerSubmission(options = {}) {
  return prepareOccAdaptiveSolveRequest(readSimulationState(), {
    mshVersion: options.mshVersion || "2.2",
    simType: options.simType ?? 2,
  });
}

export async function submitSimulationControllerJob(
  controller,
  {
    config,
    meshData,
    outputName,
    counter,
    submission = prepareSimulationControllerSubmission(),
  } = {},
) {
  const health = await controller.solver.getHealthStatus();

  if (!health?.solverReady || !health?.occBuilderReady) {
    const cachedHealth = getCachedRuntimeHealth() || health;
    const blockedReason = getFeatureBlockedReason(cachedHealth, "bem-solve");
    throw new Error(
      blockedReason ||
        "Backend solver and OCC mesher must be ready to run adaptive BEM simulation.",
    );
  }

  const { waveguidePayload, submitOptions, preparedParams, stateSnapshot } =
    submission;
  const startedIso = new Date().toISOString();
  const jobId = await controller.solver.submitSimulation(
    config,
    meshData,
    submitOptions,
  );
  const createdJob = await queueSimulationControllerJob(controller, {
    jobId,
    startedIso,
    outputName,
    counter,
    config,
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
  {
    message = "Cancellation requested. Waiting for backend worker to stop.",
  } = {},
) {
  if (!jobId || !controller?.jobs?.has(jobId)) {
    persistControllerJobs(controller);
    return null;
  }

  const pendingJob = buildCancellationRequestedSimulationJob(
    controller.jobs.get(jobId),
    {
      message,
    },
  );
  if (pendingJob) {
    upsertJob(controller, pendingJob);
  }
  persistControllerJobs(controller);
  return pendingJob;
}

export function applyStoppedSimulationControllerJob(
  controller,
  jobId,
  stopResult = {},
) {
  const responseStatus = String(stopResult?.status || "")
    .trim()
    .toLowerCase();
  if (responseStatus === "cancelled") {
    return cancelSimulationControllerJob(controller, jobId);
  }
  return requestSimulationControllerJobCancellation(controller, jobId, {
    message:
      String(stopResult?.message || "").trim() ||
      "Cancellation requested. Waiting for backend worker to stop.",
  });
}

export async function reconcileSimulationControllerRemoteJobs(
  controller,
  { onManifestSyncError = null } = {},
) {
  // Only check status for jobs already tracked locally (queued/running).
  const ACTIVE_STATUSES = new Set(["queued", "running"]);
  const activeEntries = Array.from(controller.jobs.values()).filter((job) =>
    ACTIVE_STATUSES.has(job.status),
  );

  for (const localJob of activeEntries) {
    try {
      const remote = await controller.solver.getJobStatus(localJob.id);
      const updated = toUiJob({ ...remote, id: localJob.id });
      if (updated?.id) {
        // Only sync workspace manifest when job state materially changes
        // (status transition or completion), not on every poll tick.
        const statusChanged = localJob.status !== updated.status;
        const justCompleted = updated.status === "complete" && localJob.status !== "complete";

        upsertJob(controller, updated);

        if (statusChanged || justCompleted) {
          syncSimulationWorkspaceJobManifest(updated).catch((error) => {
            if (typeof onManifestSyncError === "function") {
              onManifestSyncError(error, updated);
            }
          });
        }
      }
    } catch (error) {
      console.warn(`Status check failed for job ${localJob.id}:`, error);
    }
  }

  setActiveJob(controller, controller.activeJobId || null);
  persistControllerJobs(controller);

  const activeJob = controller.activeJobId
    ? controller.jobs.get(controller.activeJobId) || null
    : null;
  return {
    activeJob,
    anyActive: hasActiveJobs(controller),
  };
}

export async function restoreSimulationControllerJobs(
  controller,
  {
    onJobsUpdated = () => {},
    onStartPolling = () => {},
    onRecoverFromManifests = () => {},
  } = {},
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
  controller.consecutivePollFailures =
    Number(tracker.consecutivePollFailures) || 0;
  controller.isPolling = tracker.isPolling;
  syncCurrentJobId(controller);

  // Always use folder as the single source of truth for job history.
  const workspace = await readSimulationWorkspaceJobs();
  setJobSourceMode(controller, JOB_SOURCE_MODES.FOLDER);

  if (workspace.repaired || workspace.warnings.length > 0) {
    onRecoverFromManifests();
  }

  setJobsFromEntries(controller, workspace.items);
  syncCurrentJobId(controller);
  onJobsUpdated();

  if (controller.activeJobId || hasActiveJobs(controller)) {
    onStartPolling();
  }
}
