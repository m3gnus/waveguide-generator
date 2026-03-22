import { showError, showMessage } from "../feedback.js";
import {
  getFrequencySpacing,
  getMeshValidationMode,
  getVerbose,
} from "../settings/simBasicSettings.js";
import {
  getUseBurtonMiller,
} from "../settings/simAdvancedSettings.js";
import {
  getCurrentSimulationManagementSettings,
  getTaskListMinRatingFilter,
  getTaskListSortPreference,
} from "../settings/simulationManagementSettings.js";
import {
  buildPolarStatePatchFromConfig,
  readPolarStateSettings,
} from "./polarSettings.js";
import { getDownloadSimMeshEnabled } from "../settings/modal.js";
import { allJobs, hasActiveJobs } from "./jobTracker.js";
import {
  updateStageUi,
  setProgressVisible,
  restoreConnectionStatus,
  formatElapsedDuration,
  resolveJobDurationMs,
  resetProgressAnnouncement,
} from "./progressUi.js";
import { clearPollTimer, setActiveJob } from "./jobOrchestration.js";
import { downloadMeshArtifact } from "./meshDownload.js";
import {
  summarizePersistedSimulationMeshStats,
  summarizeCanonicalSimulationMesh,
  validateSimulationConfig,
} from "../../modules/simulation/domain.js";
import {
  applySimulationJobScriptState,
  readSimulationState,
  updateSimulationStateParams,
} from "../../modules/simulation/state.js";
import { resolveClearedFailedJobIds } from "../../modules/simulation/jobs.js";
import { deleteTaskWorkspaceDirectory } from "./workspaceTasks.js";
import {
  clearSimulationControllerJobs,
  ensureSimulationControllerJobResults,
  submitSimulationControllerJob,
  recordSimulationControllerExport,
  recordSimulationControllerRating,
  removeSimulationControllerJob,
  stopSimulationControllerJob,
} from "./controller.js";

export { validateSimulationConfig };

const GEOMETRY_DIAGNOSTIC_ROWS = Object.freeze([
  ["throat_disc", "Throat Disc"],
  ["horn_wall", "Horn Wall"],
  ["inner_wall", "Inner Wall"],
  ["outer_wall", "Outer Wall"],
  ["mouth_rim", "Mouth Rim"],
  ["throat_return", "Throat Return"],
  ["rear_cap", "Rear Cap"],
  ["enc_front", "Enclosure Front"],
  ["enc_side", "Enclosure Side"],
  ["enc_rear", "Enclosure Rear"],
  ["enc_edge", "Enclosure Edge"],
]);

export function renderSimulationMeshDiagnostics(summary = null) {
  const container = document.getElementById("simulation-mesh-diagnostics");
  if (!container) {
    return;
  }

  if (!summary) {
    container.innerHTML =
      '<div class="simulation-mesh-diagnostics-placeholder">Mesh stats appear here before you submit. Updated with solver data once the job starts.</div>';
    return;
  }

  const provenance = summary.provenance === "backend" ? "backend" : "preview";
  const sourceLabel = provenance === "backend" ? "Solver Geometry" : "Preview Geometry";
  const activeGeometryRows = GEOMETRY_DIAGNOSTIC_ROWS.filter(
    ([identity]) => Number(summary.identityTriangleCounts?.[identity] ?? 0) > 0,
  );

  const identityRows = activeGeometryRows.map(
    ([identity, label]) => `
    <div class="simulation-mesh-diagnostics-region">
      <span class="simulation-mesh-diagnostics-tag-label">${label}</span>
      <span class="simulation-mesh-diagnostics-tag-count">${summary.identityTriangleCounts?.[identity] ?? 0} tris</span>
    </div>
  `,
  ).join("");
  const emptyStateMarkup =
    activeGeometryRows.length === 0
      ? '<div class="simulation-mesh-diagnostics-empty">No geometry regions were classified for this mesh.</div>'
      : "";
  const warnings = formatGeometryDiagnosticWarnings(summary);

  const warningMarkup =
    warnings.length > 0
      ? `<div class="simulation-mesh-diagnostics-warning">${warnings.map((warning) => escapeHtml(warning)).join("<br>")}</div>`
      : "";

  container.innerHTML = `
    <div class="simulation-mesh-diagnostics-header">
      <span class="simulation-mesh-diagnostics-header-title">${escapeHtml(sourceLabel)}</span>
      <span class="simulation-mesh-diagnostics-header-meta">${summary.vertexCount.toLocaleString()} verts</span>
      <span class="simulation-mesh-diagnostics-header-meta">${summary.triangleCount.toLocaleString()} tris</span>
    </div>
    <div class="simulation-mesh-diagnostics-body">
      <div class="simulation-mesh-diagnostics-section-label">Geometry Regions</div>
      <div class="simulation-mesh-diagnostics-tags">${identityRows}${emptyStateMarkup}</div>
    </div>
    ${warningMarkup}
  `;
}

export function formatJobSummary(job) {
  const status = String(job.status || "").toLowerCase();
  const progress = Math.round((Number(job.progress) || 0) * 100);
  const detail = String(job.stageMessage || job.errorMessage || "").trim();
  const stage = String(job.stage || "").toLowerCase();

  if (status === "complete") {
    const duration = formatElapsedDuration(resolveJobDurationMs(job));
    return duration ? `Complete (${duration})` : "Complete";
  }
  if (status === "cancelled") return "Cancelled";
  if (stage === "cancelling" || job.cancellationRequested) {
    return detail || "Stopping...";
  }
  if (status === "queued") return "Queued";
  if (status === "running") {
    if (detail && !/simulation\s+running|running/i.test(detail)) {
      return detail;
    }
    return `Running (${progress}%)`;
  }
  if (status === "error") {
    if (detail && !/simulation\s+failed|error/i.test(detail)) {
      return `Failed: ${detail}`;
    }
    return "Failed";
  }

  return detail || `${String(job.status || "Unknown")}`;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatGeometryDiagnosticWarnings(summary = null) {
  const formatted = [];
  const warnings = Array.isArray(summary?.warnings) ? summary.warnings : [];
  const throatDiscCount = Number(summary?.identityTriangleCounts?.throat_disc ?? 0);

  for (const rawWarning of warnings) {
    const warning = String(rawWarning ?? "").trim();
    if (!warning) {
      continue;
    }
    if (/source surface tag/i.test(warning) || /no source surface tag/i.test(warning)) {
      formatted.push(
        throatDiscCount > 0
          ? "Throat Disc is present, but it is not classified as the source region."
          : "Throat Disc is missing from the mesh."
      );
      continue;
    }
    if (/face-identity diagnostics are unavailable/i.test(warning)) {
      formatted.push("Geometry region breakdown is unavailable for this job.");
      continue;
    }
    if (/unsupported surface tags/i.test(warning)) {
      formatted.push("Mesh contains unsupported surface classifications.");
      continue;
    }
    formatted.push(warning);
  }

  return Array.from(new Set(formatted));
}

function formatTimestampTooltip(job) {
  const raw = job.startedAt || job.queuedAt || job.createdAt;
  if (!raw) {
    return "Simulation start time unavailable";
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return "Simulation start time unavailable";
  }
  const formatted = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(parsed);
  return `Started: ${formatted}`;
}

function describeJobFeedSource(panel) {
  const mode = panel?.jobSourceMode === "folder" ? "folder" : "backend";
  return {
    mode,
    label: mode === "folder" ? "Folder Tasks" : "Backend Jobs",
    badge: mode === "folder" ? "Folder" : "",
  };
}

function renderRatingStars(job) {
  const currentRating = Number.isFinite(Number(job?.rating))
    ? Math.max(0, Math.min(5, Number(job.rating)))
    : 0;
  return `
    <div class="simulation-job-rating" aria-label="Task rating">
      ${Array.from({ length: 5 }, (_, index) => {
        const ratingValue = index + 1;
        const isActive = ratingValue <= currentRating;
        return `
          <button
            type="button"
            class="simulation-job-rating-star${isActive ? " is-active" : ""}"
            data-job-rating="${ratingValue}"
            data-job-id="${escapeHtml(job.id)}"
            aria-label="Rate ${escapeHtml(job.label || job.id)} ${ratingValue} out of 5"
            title="Rate ${ratingValue} out of 5"
          >${isActive ? "&#9733;" : "&#9734;"}</button>
        `;
      }).join("")}
    </div>
  `;
}

function syncJobListPreferenceControls() {
  const settings = getCurrentSimulationManagementSettings();
  const sortEl = document.getElementById("simulation-jobs-sort");
  if (sortEl && sortEl.value !== settings.defaultSort) {
    sortEl.value = settings.defaultSort;
  }

  const ratingEl = document.getElementById("simulation-jobs-min-rating");
  const ratingValue = String(settings.minRatingFilter);
  if (ratingEl && ratingEl.value !== ratingValue) {
    ratingEl.value = ratingValue;
  }
}

function readOutputNameAndCounter() {
  const name =
    (document.getElementById("export-prefix")?.value || "simulation").trim() ||
    "simulation";
  const counterEl = document.getElementById("export-counter");
  const counterRaw = Number(counterEl?.value);
  const counter =
    Number.isFinite(counterRaw) && counterRaw >= 1 ? Math.floor(counterRaw) : 1;
  return { name, counter };
}

function incrementOutputCounter() {
  const counterEl = document.getElementById("export-counter");
  if (!counterEl) return;
  const currentRaw = Number(counterEl.value);
  const current =
    Number.isFinite(currentRaw) && currentRaw >= 1 ? Math.floor(currentRaw) : 1;
  counterEl.value = String(current + 1);
}

function setSimulationInputsFromScript(script = {}) {
  const mappings = [
    ["freq-start", script.frequencyStart],
    ["freq-end", script.frequencyEnd],
    ["freq-steps", script.numFrequencies],
  ];

  for (const [id, value] of mappings) {
    if (value === undefined || value === null) continue;
    const el = document.getElementById(id);
    if (el) {
      el.value = String(value);
    }
  }

  if (script.outputName !== undefined) {
    const nameEl = document.getElementById("export-prefix");
    if (nameEl) {
      nameEl.value = String(script.outputName);
    }
  }
  if (script.counter !== undefined) {
    const counterEl = document.getElementById("export-counter");
    if (counterEl) {
      counterEl.value = String(script.counter);
    }
  }

  if (script.polarConfig) {
    const currentState = readSimulationState();
    updateSimulationStateParams(
      buildPolarStatePatchFromConfig(currentState?.params, script.polarConfig),
    );
  }
}

async function ensureJobResults(panel, jobId, { display = true } = {}) {
  const result = await ensureSimulationControllerJobResults(panel, jobId, {
    display,
    displayResults: (results) => {
      panel.displayResults(results);
    },
  });

  if (result.reason === "missing_job") {
    showError("Simulation task not found.");
    return null;
  }
  if (result.reason === "not_complete") {
    showError("Results are only available for completed simulations.");
    return null;
  }
  return result.results;
}

export function renderJobList(panel) {
  const list = document.getElementById("simulation-jobs-list");
  if (!list) return;

  const sourceEl = document.getElementById("simulation-jobs-source-label");
  const source = describeJobFeedSource(panel);
  if (sourceEl) {
    sourceEl.textContent = source.label;
  }

  syncJobListPreferenceControls();
  const jobs = allJobs(panel, {
    sortBy: getTaskListSortPreference(),
    minRating: getTaskListMinRatingFilter(),
  });
  if (jobs.length === 0) {
    list.innerHTML = `<div class="simulation-job-meta">No ${source.mode === "folder" ? "folder tasks" : "backend jobs"} yet.</div>`;
    return;
  }

  list.innerHTML = jobs
    .map((job) => {
      const statusClass =
        job.status === "running"
          ? "is-running"
          : job.status === "complete"
            ? "is-completed"
            : job.status === "error"
              ? "is-failed"
              : "";
      const canRerun =
        (job.status === "error" || job.status === "cancelled") && job.script;
      const canStop =
        (job.status === "queued" || job.status === "running") &&
        job.stage !== "cancelling";
      return `
    <div class="simulation-job-item ${panel.activeJobId === job.id ? "is-active" : ""} ${statusClass}" data-job-id="${job.id}">
      <div class="simulation-job-header">
        <div class="simulation-job-title" title="${escapeHtml(formatTimestampTooltip(job))}">
          <span>${escapeHtml(job.label || job.id.slice(0, 8))}</span>
          ${source.mode === "folder" ? `<button type="button" class="simulation-job-open-folder" data-job-action="open-folder" data-job-id="${job.id}" aria-label="Open in Finder" title="Open results folder in Finder"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/></svg></button>` : ""}
        </div>
        <button type="button" class="simulation-job-remove" data-job-action="remove" data-job-id="${job.id}" aria-label="Remove" title="Remove">&#x2715;</button>
      </div>
      <div class="simulation-job-status-row">
        <div class="simulation-job-meta">${escapeHtml(formatJobSummary(job))}</div>
        ${job.status === "complete" ? renderRatingStars(job) : ""}
      </div>
      <div class="simulation-job-actions">
        ${job.status === "complete" ? `<button type="button" class="btn-secondary button-compact" data-job-action="view" data-job-id="${job.id}" title="View results">View</button>` : ""}
        ${job.status === "complete" ? `<button type="button" class="btn-secondary button-compact" data-job-action="export" data-job-id="${job.id}" title="Export results">Export</button>` : ""}
        ${job.script ? `<button type="button" class="btn-secondary button-compact" data-job-action="load-script" data-job-id="${job.id}" title="Load parameters">Load</button>` : ""}
        ${canRerun ? `<button type="button" class="btn-secondary button-compact" data-job-action="redo" data-job-id="${job.id}" title="Rerun">Rerun</button>` : ""}
        ${canStop ? `<button type="button" class="btn-tertiary button-compact" data-job-action="stop" data-job-id="${job.id}" title="Stop">Stop</button>` : ""}
      </div>
    </div>
  `;
    })
    .join("");
}

export async function viewJobResults(panel, jobId) {
  const results = await ensureJobResults(panel, jobId, { display: true });
  if (!results) return;
  panel.openViewResults();
  panel.pollSimulationStatus();
}

export async function exportJobResults(panel, jobId) {
  const results = await ensureJobResults(panel, jobId, { display: true });
  if (!results) return;
  const job = panel.jobs?.get(jobId) || null;
  const bundle = await panel.exportResults({ job });
  if (
    bundle &&
    (bundle.exportedFiles.length > 0 || bundle.failures.length > 0)
  ) {
    await recordSimulationControllerExport(panel, jobId, {
      exportedFiles: bundle.exportedFiles,
      justCompleted: false,
    });
  }
  panel.pollSimulationStatus();
}

export function loadJobScript(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job?.script) {
    showError("No saved parameters found for this simulation.");
    return;
  }

  const script = job.script;
  applySimulationJobScriptState(script, {
    source: "simulation-job-load-script",
  });

  setSimulationInputsFromScript(script);
  showMessage(`Loaded parameters from ${job.label || jobId}.`, {
    type: "info",
    duration: 2500,
  });
}

export async function rateJob(panel, jobId, rating) {
  const next = await recordSimulationControllerRating(panel, jobId, rating);
  if (!next) {
    showError("Simulation task not found.");
    return;
  }
  renderJobList(panel);
}

export async function redoJob(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job?.script) {
    showError("No saved parameters found for this simulation.");
    return;
  }
  loadJobScript(panel, jobId);

  // Remove the failed/cancelled job before re-running
  try {
    await panel.solver.deleteJob(jobId);
  } catch (_) {
    /* best-effort */
  }
  removeSimulationControllerJob(panel, jobId);
  renderJobList(panel);

  panel.runSimulation();
}

export async function removeJobFromFeed(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job) return;
  if (job.status === "queued" || job.status === "running") {
    showError("Stop the running simulation before removing it from the feed.");
    return;
  }
  if (
    !window.confirm(`Remove simulation "${job.label || jobId}" from the feed?`)
  ) {
    return;
  }

  try {
    await panel.solver.deleteJob(jobId);
  } catch (error) {
    showError(`Failed to remove simulation from database: ${error.message}`);
    return;
  }

  // Delete the task folder from the output directory
  await deleteTaskWorkspaceDirectory(job);

  if (!removeSimulationControllerJob(panel, jobId)) {
    return;
  }
  renderJobList(panel);
}

export async function clearFailedSimulations(panel) {
  const localFailedIds = allJobs(panel)
    .filter((job) => job.status === "error")
    .map((job) => job.id);

  if (localFailedIds.length === 0) {
    showMessage("No failed simulations to clear.", {
      type: "info",
      duration: 2200,
    });
    return;
  }

  let deletedIds = [];
  try {
    const response = await panel.solver.clearFailedJobs();
    deletedIds = resolveClearedFailedJobIds(localFailedIds, response);
  } catch (error) {
    showError(
      `Failed to clear failed simulations from backend: ${error.message}`,
    );
    return;
  }

  const removed = clearSimulationControllerJobs(panel, deletedIds);
  renderJobList(panel);
  showMessage(
    removed > 0
      ? `Deleted ${removed} failed simulation${removed === 1 ? "" : "s"} from database.`
      : "No failed simulations found in database.",
    { type: "info", duration: 2200 },
  );
}

export async function stopSimulation(panel) {
  const targetJobId = panel.activeJobId || panel.currentJobId;
  const { stopError, cancelledJob } = await stopSimulationControllerJob(
    panel,
    targetJobId,
  );
  if (stopError) {
    console.warn("Failed to call stop API:", stopError);
    showError(`Failed to stop simulation: ${stopError.message}`);
    return;
  }
  renderJobList(panel);

  if (cancelledJob?.status === "cancelled") {
    panel.completedStatusMessage = null;
    panel.simulationStartedAtMs = null;
    panel.lastSimulationDurationMs = null;
    updateStageUi(panel, {
      progress: 0,
      stage: "cancelled",
      message: cancelledJob.stageMessage || "Simulation cancelled by user",
    });
    showMessage("Simulation cancelled.", { type: "info", duration: 2000 });
  } else if (cancelledJob) {
    setProgressVisible(true);
    updateStageUi(panel, {
      progress: Number(cancelledJob.progress) || 0,
      stage: cancelledJob.stage || "cancelling",
      message:
        cancelledJob.stageMessage ||
        "Cancellation requested. Waiting for backend worker to stop.",
    });
    showMessage("Cancellation requested. Waiting for backend worker to stop.", {
      type: "info",
      duration: 2400,
    });
  }
  if (!hasActiveJobs(panel)) {
    setTimeout(() => {
      setProgressVisible(false);
    }, 1000);
    clearPollTimer(panel);
    setActiveJob(panel, null);
    restoreConnectionStatus(panel);
  }
}

export async function runSimulation(panel) {
  panel.completedStatusMessage = null;

  // Get simulation settings
  const config = {
    frequencyStart: Number(document.getElementById("freq-start").value),
    frequencyEnd: Number(document.getElementById("freq-end").value),
    numFrequencies: Number(document.getElementById("freq-steps").value),
    meshValidationMode: getMeshValidationMode(),
    frequencySpacing: getFrequencySpacing(),
    verbose: getVerbose(),
    advancedSettings: {
      useBurtonMiller: getUseBurtonMiller(),
    },
  };

  const polarSettings = readPolarStateSettings(readSimulationState()?.params);
  if (!polarSettings.ok) {
    showError(polarSettings.validationError);
    return;
  }
  config.polarConfig = {
    angle_range: polarSettings.angleRangeArray,
    norm_angle: polarSettings.normAngle,
    distance: polarSettings.distance,
    inclination: polarSettings.diagonalAngle,
    enabled_axes: polarSettings.enabledAxes,
    observation_origin: polarSettings.observationOrigin,
  };

  // Validate settings
  const validationError = validateSimulationConfig(config);
  if (validationError) {
    showError(validationError);
    return;
  }
  // Show progress
  panel.simulationStartedAtMs = Date.now();
  panel.lastSimulationDurationMs = null;
  resetProgressAnnouncement();
  setProgressVisible(true);
  updateStageUi(panel, {
    progress: 0.05,
    stage: "mesh_generation",
    message: "Preparing simulation mesh",
  });

  try {
    // Get current mesh data
    const meshData = await panel.prepareMeshForSimulation();
    renderSimulationMeshDiagnostics({
      ...summarizeCanonicalSimulationMesh(meshData),
      provenance: "preview",
    });

    updateStageUi(panel, {
      progress: 0.2,
      stage: "mesh_generation",
      message: "Mesh ready, submitting to BEM solver",
    });

    const { name: outputName, counter } = readOutputNameAndCounter();
    await submitSimulationControllerJob(panel, {
      config,
      meshData,
      outputName,
      counter,
    });
    incrementOutputCounter();
    renderJobList(panel);

    updateStageUi(panel, {
      progress: 0.3,
      stage: "initializing",
      message: "Job accepted by backend",
    });

    panel.pollSimulationStatus();

    // Non-blocking: download simulation mesh artifact if toggle is on
    if (getDownloadSimMeshEnabled() && panel.activeJobId) {
      downloadMeshArtifact(panel.activeJobId, panel.solver.backendUrl).catch(
        (err) => {
          console.warn(
            "Mesh artifact download failed (non-blocking):",
            err.message,
          );
        },
      );
    }
  } catch (error) {
    console.error("Simulation error:", error);
    panel.completedStatusMessage = null;
    panel.simulationStartedAtMs = null;
    panel.lastSimulationDurationMs = null;
    updateStageUi(panel, {
      progress: 1,
      stage: "error",
      message: error.message,
    });
    showError(`Simulation failed: ${error.message}`);

    setTimeout(() => {
      setProgressVisible(false);
      restoreConnectionStatus(panel);
    }, 3000);
  }
}

export async function runMockSimulation(config) {
  // Simulate processing time
  return new Promise((resolve, reject) => {
    setTimeout(() => {
      resolve();
    }, 2000);
  });
}

export function renderBackendSimulationMeshDiagnostics(meshStats = null) {
  if (!meshStats) {
    return;
  }
  renderSimulationMeshDiagnostics(
    summarizePersistedSimulationMeshStats(meshStats),
  );
}
