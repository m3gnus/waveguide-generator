import { showError, showMessage } from '../feedback.js';
import { GlobalState } from '../../state.js';
import { prepareGeometryParams } from '../../geometry/index.js';
import { buildWaveguidePayload } from '../../solver/waveguidePayload.js';
import { syncPolarControlsFromBlocks } from './polarSettings.js';
import { readPolarUiSettings } from './polarSettings.js';
import {
  allJobs,
  hasActiveJobs,
  persistPanelJobs,
  removeJob,
  toUiJob,
  upsertJob
} from './jobTracker.js';

const STAGE_LABELS = {
  mesh_generation: 'Mesh generation',
  queued: 'Queued',
  initializing: 'Initializing solver',
  mesh_prepare: 'Preparing backend mesh',
  mesh_ready: 'Mesh ready',
  solver_setup: 'BEM setup',
  bem_solve: 'BEM solve',
  frequency_solve: 'BEM solve',
  directivity: 'Spectra and directivity',
  finalizing: 'Finalizing results',
  complete: 'Complete',
  cancelled: 'Cancelled',
  error: 'Error'
};

function normalizeStage(stage) {
  return typeof stage === 'string' && stage.trim() ? stage.trim() : 'bem_solve';
}

function stageStep(stage) {
  const key = normalizeStage(stage);
  if (
    key === 'mesh_generation' ||
    key === 'mesh_prepare' ||
    key === 'mesh_ready' ||
    key === 'solver_setup' ||
    key === 'initializing'
  ) {
    return 1;
  }
  if (key === 'queued') return 1;
  if (key === 'directivity') return 3;
  if (key === 'finalizing' || key === 'complete' || key === 'cancelled' || key === 'error') return 4;
  return 2;
}

function setProgressVisible(progressDiv, visible) {
  if (!progressDiv) return;
  if (visible) {
    progressDiv.classList.remove('is-hidden');
    progressDiv.style.display = 'block';
  } else {
    progressDiv.classList.add('is-hidden');
    progressDiv.style.display = 'none';
  }
}

function formatElapsedDuration(durationMs) {
  const numericDuration = Number(durationMs);
  if (!Number.isFinite(numericDuration) || numericDuration < 0) {
    return null;
  }

  const totalSeconds = numericDuration / 1000;
  if (totalSeconds < 10) {
    return `${totalSeconds.toFixed(1)}s`;
  }

  if (totalSeconds < 60) {
    return `${Math.round(totalSeconds)}s`;
  }

  const roundedSeconds = Math.round(totalSeconds);
  const hours = Math.floor(roundedSeconds / 3600);
  const minutes = Math.floor((roundedSeconds % 3600) / 60);
  const seconds = roundedSeconds % 60;

  if (hours > 0) {
    return `${hours}h ${minutes}m ${seconds}s`;
  }
  return `${minutes}m ${seconds}s`;
}

function resolveStageDetail(stage, message, pct) {
  const key = normalizeStage(stage);
  const raw = typeof message === 'string' ? message.trim() : '';

  if (key === 'directivity') {
    if (!raw || /computing spectra\/directivity/i.test(raw)) {
      return `Generating polar maps (horizontal/vertical/diagonal) and deriving DI from solved frequencies (${pct}%).`;
    }
    return raw;
  }

  if (key === 'frequency_solve' || key === 'bem_solve') {
    if (raw) return raw;
    return `Solving BEM system across requested frequencies (${pct}%).`;
  }

  if (key === 'solver_setup') {
    return raw || 'Preparing solver operators and boundary conditions.';
  }

  if (key === 'mesh_generation' || key === 'mesh_prepare') {
    return raw || 'Building canonical mesh payload and validating tags.';
  }

  if (key === 'finalizing') {
    return raw || 'Packaging solver output and preparing charts.';
  }

  return raw;
}

function updateProgressUi(progressFill, progressText, {
  progress = 0,
  stage = 'bem_solve',
  message = ''
} = {}) {
  const clamped = Math.max(0, Math.min(1, Number(progress) || 0));
  const pct = Math.round(clamped * 100);
  const key = normalizeStage(stage);
  const label = STAGE_LABELS[key] || key;
  const detail = resolveStageDetail(key, message, pct);
  const progressBar = document.getElementById('simulation-progressbar');

  if (progressFill) {
    progressFill.style.width = `${pct}%`;
  }
  if (progressBar) {
    progressBar.setAttribute('aria-valuenow', String(pct));
    progressBar.setAttribute('aria-valuetext', `Stage: ${label}. ${pct}% complete.`);
  }
  if (progressText) {
    progressText.textContent = detail || '';
  }
}

function updateConnectionStageUi(panel, {
  progress = 0,
  stage = 'bem_solve',
  message = ''
} = {}) {
  const statusDot = document.getElementById('solver-status');
  const statusText = document.getElementById('solver-status-text');
  const statusHelp = document.getElementById('solver-status-help');
  const clamped = Math.max(0, Math.min(1, Number(progress) || 0));
  const pct = Math.round(clamped * 100);
  const key = normalizeStage(stage);
  const step = stageStep(key);
  const label = STAGE_LABELS[key] || key;
  const detail = resolveStageDetail(key, message, pct);

  if (statusDot) {
    statusDot.className = key === 'error' || key === 'cancelled'
      ? 'status-dot disconnected'
      : 'status-dot connected';
  }
  if (statusText) {
    statusText.textContent = `Stage ${step}/4: ${label} (${pct}%)`;
  }
  if (statusHelp) {
    if (detail) {
      statusHelp.textContent = detail;
      statusHelp.classList.remove('is-hidden');
    } else {
      statusHelp.classList.add('is-hidden');
    }
  }
  if (panel) {
    panel.stageStatusActive = true;
  }
}

function updateStageUi(panel, progressFill, progressText, payload) {
  updateProgressUi(progressFill, progressText, payload);
  updateConnectionStageUi(panel, payload);
}

function restoreConnectionStatus(panel) {
  if (!panel) return;
  panel.stageStatusActive = false;
  panel.checkSolverConnection();
}

function setActiveJob(panel, jobId) {
  panel.activeJobId = jobId || null;
  panel.currentJobId = panel.activeJobId;
}

function setPollTimer(panel, timer) {
  panel.pollTimer = timer;
  panel.pollInterval = timer;
}

function clearPollTimer(panel) {
  if (panel.pollTimer) {
    clearTimeout(panel.pollTimer);
  }
  panel.pollTimer = null;
  panel.pollInterval = null;
}

function formatJobSummary(job) {
  const status = String(job.status || '').toLowerCase();
  const progress = Math.round((Number(job.progress) || 0) * 100);
  const detail = String(job.stageMessage || job.errorMessage || '').trim();

  if (status === 'complete') return 'Complete';
  if (status === 'cancelled') return 'Cancelled';
  if (status === 'queued') return 'Queued';
  if (status === 'running') {
    if (detail && !/simulation\s+running|running/i.test(detail)) {
      return detail;
    }
    return `Running (${progress}%)`;
  }
  if (status === 'error') {
    if (detail && !/simulation\s+failed|error/i.test(detail)) {
      return `Failed: ${detail}`;
    }
    return 'Failed';
  }

  return detail || `${String(job.status || 'Unknown')}`;
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function formatTimestampTooltip(job) {
  const raw = job.startedAt || job.queuedAt || job.createdAt;
  if (!raw) {
    return 'Simulation start time unavailable';
  }
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) {
    return 'Simulation start time unavailable';
  }
  const formatted = new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'medium'
  }).format(parsed);
  return `Started: ${formatted}`;
}

function readOutputNameAndCounter() {
  const name = (document.getElementById('export-prefix')?.value || 'simulation').trim() || 'simulation';
  const counterEl = document.getElementById('export-counter');
  const counterRaw = Number(counterEl?.value);
  const counter = Number.isFinite(counterRaw) && counterRaw >= 1 ? Math.floor(counterRaw) : 1;
  return { name, counter };
}

function incrementOutputCounter() {
  const counterEl = document.getElementById('export-counter');
  if (!counterEl) return;
  const currentRaw = Number(counterEl.value);
  const current = Number.isFinite(currentRaw) && currentRaw >= 1 ? Math.floor(currentRaw) : 1;
  counterEl.value = String(current + 1);
}

function setSimulationInputsFromScript(script = {}) {
  const mappings = [
    ['freq-start', script.frequencyStart],
    ['freq-end', script.frequencyEnd],
    ['freq-steps', script.numFrequencies]
  ];

  for (const [id, value] of mappings) {
    if (value === undefined || value === null) continue;
    const el = document.getElementById(id);
    if (el) {
      el.value = String(value);
    }
  }

  if (script.outputName !== undefined) {
    const nameEl = document.getElementById('export-prefix');
    if (nameEl) {
      nameEl.value = String(script.outputName);
    }
  }
  if (script.counter !== undefined) {
    const counterEl = document.getElementById('export-counter');
    if (counterEl) {
      counterEl.value = String(script.counter);
    }
  }

  if (script.polarConfig) {
    const polar = script.polarConfig;
    const [angleStart, angleEnd, angleStep] = Array.isArray(polar.angle_range) ? polar.angle_range : [];
    const polarMap = [
      ['polar-angle-start', angleStart],
      ['polar-angle-end', angleEnd],
      ['polar-angle-step', angleStep],
      ['polar-norm-angle', polar.norm_angle],
      ['polar-distance', polar.distance],
      ['polar-inclination', polar.inclination]
    ];
    for (const [id, value] of polarMap) {
      if (value === undefined || value === null) continue;
      const el = document.getElementById(id);
      if (el) {
        el.value = String(value);
      }
    }
    const enabledAxes = new Set(Array.isArray(polar.enabled_axes) ? polar.enabled_axes : []);
    const axisMappings = [
      ['polar-axis-horizontal', 'horizontal'],
      ['polar-axis-vertical', 'vertical'],
      ['polar-axis-diagonal', 'diagonal']
    ];
    for (const [id, axis] of axisMappings) {
      const el = document.getElementById(id);
      if (el) {
        el.checked = enabledAxes.size === 0 ? true : enabledAxes.has(axis);
      }
    }
  }
}

async function ensureJobResults(panel, jobId, { display = true } = {}) {
  const job = panel.jobs?.get(jobId);
  if (!job) {
    showError('Simulation task not found.');
    return null;
  }

  panel.activeJobId = jobId;
  panel.currentJobId = jobId;

  if (panel.resultCache?.has(jobId)) {
    const cached = panel.resultCache.get(jobId);
    panel.lastResults = cached;
    if (display) {
      panel.displayResults(cached);
    }
    return cached;
  }

  if (job.status !== 'complete') {
    showError('Results are only available for completed simulations.');
    return null;
  }

  const results = await panel.solver.getResults(jobId);
  panel.resultCache.set(jobId, results);
  panel.lastResults = results;
  if (display) {
    panel.displayResults(results);
  }
  return results;
}

export function renderJobList(panel) {
  const list = document.getElementById('simulation-jobs-list');
  if (!list) return;

  const jobs = allJobs(panel);
  if (jobs.length === 0) {
    list.innerHTML = '<div class="simulation-job-meta">No jobs yet.</div>';
    return;
  }

  list.innerHTML = jobs.map((job) => `
    <div class="simulation-job-item ${panel.activeJobId === job.id ? 'is-active' : ''}" data-job-id="${job.id}">
      <div class="simulation-job-header">
        <div class="simulation-job-info">
          <div class="simulation-job-title" title="${escapeHtml(formatTimestampTooltip(job))}">${escapeHtml(job.label || job.id.slice(0, 8))}</div>
          <div class="simulation-job-meta">${escapeHtml(formatJobSummary(job))}</div>
        </div>
        <div class="simulation-job-actions">
          ${job.status === 'complete' ? `<button type="button" class="secondary button-compact" data-job-action="view" data-job-id="${job.id}" title="View results for this simulation">View</button>` : ''}
          ${job.status === 'complete' ? `<button type="button" class="secondary button-compact" data-job-action="export" data-job-id="${job.id}" title="Export simulation results to file">Export</button>` : ''}
          ${job.script ? `<button type="button" class="secondary button-compact" data-job-action="load-script" data-job-id="${job.id}" title="Restore geometry and solver parameters from this simulation">Script</button>` : ''}
          ${(job.status === 'error' || job.status === 'cancelled') && job.script ? `<button type="button" class="secondary button-compact" data-job-action="redo" data-job-id="${job.id}" title="Restore parameters and re-run this simulation">Redo</button>` : ''}
          ${job.status === 'queued' || job.status === 'running'
            ? `<button type="button" class="secondary button-compact" data-job-action="stop" data-job-id="${job.id}" title="Stop this running simulation">Stop</button>`
            : ''}
          <button type="button" class="secondary button-compact simulation-job-remove" data-job-action="remove" data-job-id="${job.id}" aria-label="Remove simulation from feed" title="Remove this simulation from the feed">&#x2715;</button>
        </div>
      </div>
    </div>
  `).join('');
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
  await panel.exportResults();
  panel.pollSimulationStatus();
}

export function loadJobScript(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job?.script) {
    showError('No saved parameters found for this simulation.');
    return;
  }

  const script = job.script;
  if (script.stateSnapshot && script.stateSnapshot.params) {
    GlobalState.loadState(script.stateSnapshot, 'simulation-job-load-script');
    syncPolarControlsFromBlocks(script.stateSnapshot.params._blocks);
  } else if (script.params) {
    GlobalState.update(script.params);
  }

  setSimulationInputsFromScript(script);
  showMessage(`Loaded parameters from ${job.label || jobId}.`, { type: 'info', duration: 2500 });
}

export async function redoJob(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job?.script) {
    showError('No saved parameters found for this simulation.');
    return;
  }
  loadJobScript(panel, jobId);

  // Remove the failed/cancelled job before re-running
  try { await panel.solver.deleteJob(jobId); } catch (_) { /* best-effort */ }
  removeJob(panel, jobId);
  persistPanelJobs(panel);
  renderJobList(panel);

  panel.runSimulation();
}

export async function removeJobFromFeed(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job) return;
  if (job.status === 'queued' || job.status === 'running') {
    showError('Stop the running simulation before removing it from the feed.');
    return;
  }
  if (!window.confirm(`Remove simulation "${job.label || jobId}" from the feed?`)) {
    return;
  }

  try {
    await panel.solver.deleteJob(jobId);
  } catch (error) {
    showError(`Failed to remove simulation from database: ${error.message}`);
    return;
  }

  if (!removeJob(panel, jobId)) {
    return;
  }
  persistPanelJobs(panel);
  renderJobList(panel);
}

export async function clearFailedSimulations(panel) {
  const localFailedIds = allJobs(panel)
    .filter((job) => job.status === 'error')
    .map((job) => job.id);

  if (localFailedIds.length === 0) {
    showMessage('No failed simulations to clear.', { type: 'info', duration: 2200 });
    return;
  }

  let deletedIds = [];
  try {
    const response = await panel.solver.clearFailedJobs();
    if (Array.isArray(response?.deleted_ids) && response.deleted_ids.length > 0) {
      deletedIds = response.deleted_ids;
    } else if (Number(response?.deleted_count) > 0) {
      deletedIds = [...localFailedIds];
    }
  } catch (error) {
    showError(`Failed to clear failed simulations from backend: ${error.message}`);
    return;
  }

  let removed = 0;
  for (const jobId of deletedIds) {
    if (removeJob(panel, jobId)) {
      removed += 1;
    }
  }
  persistPanelJobs(panel);
  renderJobList(panel);
  showMessage(
    removed > 0 ? `Deleted ${removed} failed simulation${removed === 1 ? '' : 's'} from database.` : 'No failed simulations found in database.',
    { type: 'info', duration: 2200 }
  );
}

export function validateSimulationConfig(config) {
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

export async function stopSimulation(panel) {
  const runBtn = document.getElementById('run-simulation-btn');
  const stopBtn = document.getElementById('stop-simulation-btn');
  const progressDiv = document.getElementById('simulation-progress');
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');

  const targetJobId = panel.activeJobId || panel.currentJobId;

  if (targetJobId) {
    try {
      await panel.solver.stopJob(targetJobId);
    } catch (error) {
      console.warn('Failed to call stop API:', error);
      // Continue with local cleanup even if API call fails
    }
  }

  if (targetJobId && panel.jobs.has(targetJobId)) {
    upsertJob(panel, {
      ...panel.jobs.get(targetJobId),
      id: targetJobId,
      status: 'cancelled',
      stage: 'cancelled',
      stageMessage: 'Simulation cancelled by user',
      errorMessage: 'Simulation cancelled by user',
      completedAt: new Date().toISOString()
    });
  }
  persistPanelJobs(panel);
  renderJobList(panel);

  // Update UI to show cancellation
  panel.completedStatusMessage = null;
  panel.simulationStartedAtMs = null;
  panel.lastSimulationDurationMs = null;
  updateStageUi(panel, progressFill, progressText, {
    progress: 0,
    stage: 'cancelled',
    message: 'Simulation cancelled by user'
  });
  showMessage('Simulation cancelled.', { type: 'info', duration: 2000 });
  if (runBtn) {
    runBtn.disabled = false;
  }
  if (stopBtn) {
    stopBtn.disabled = true;
  }

  // Hide progress bar after a short delay
  setTimeout(() => {
    setProgressVisible(progressDiv, false);
  }, 1000);

  if (!hasActiveJobs(panel)) {
    clearPollTimer(panel);
    setActiveJob(panel, null);
  }
  restoreConnectionStatus(panel);
}

export async function runSimulation(panel) {
  const runBtn = document.getElementById('run-simulation-btn');
  const progressDiv = document.getElementById('simulation-progress');
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');
  const resultsContainer = document.getElementById('results-container');
  panel.completedStatusMessage = null;

  // Get simulation settings
  const config = {
    frequencyStart: Number(document.getElementById('freq-start').value),
    frequencyEnd: Number(document.getElementById('freq-end').value),
    numFrequencies: Number(document.getElementById('freq-steps').value),
    frequencySpacing: 'log',
    deviceMode: 'auto'
  };

  const polarSettings = readPolarUiSettings();
  if (!polarSettings.ok) {
    showError(polarSettings.validationError);
    return;
  }
  config.polarConfig = {
    angle_range: polarSettings.angleRangeArray,
    norm_angle: polarSettings.normAngle,
    distance: polarSettings.distance,
    inclination: polarSettings.diagonalAngle,
    enabled_axes: polarSettings.enabledAxes
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
  runBtn.disabled = true;
  setProgressVisible(progressDiv, true);
  resultsContainer.classList.add('is-hidden');
  resultsContainer.style.display = 'none';
  updateStageUi(panel, progressFill, progressText, {
    progress: 0.05,
    stage: 'mesh_generation',
    message: 'Preparing simulation mesh'
  });

  try {
    // Get current mesh data
    const meshData = await panel.prepareMeshForSimulation();
    if (!meshData.surfaceTags || meshData.surfaceTags.length !== meshData.indices.length / 3) {
      throw new Error('Mesh payload is invalid: missing canonical surface tags.');
    }
    const state = GlobalState.get();
    const preparedParams = prepareGeometryParams(state.params, {
      type: state.type,
      forceFullQuadrants: true,
      applyVerticalOffset: true
    });
    const waveguidePayload = buildWaveguidePayload(preparedParams, '2.2');
    waveguidePayload.sim_type = 2;
    const submitOptions = {
      mesh: {
        strategy: 'occ_adaptive',
        waveguide_params: waveguidePayload
      }
    };

    updateStageUi(panel, progressFill, progressText, {
      progress: 0.2,
      stage: 'mesh_generation',
      message: 'Mesh ready, submitting to BEM solver'
    });

    // Disable stop button at start, enable it when simulation begins
    const stopBtn = document.getElementById('stop-simulation-btn');
    if (stopBtn) {
      stopBtn.disabled = true;
    }

    const health = await panel.solver.getHealthStatus();
    if (!health?.solverReady || !health?.occBuilderReady) {
      throw new Error('Backend solver and OCC mesher must be ready to run adaptive BEM simulation.');
    }

    const startedIso = new Date().toISOString();
    const { name: outputName, counter } = readOutputNameAndCounter();
    const jobId = await panel.solver.submitSimulation(config, meshData, submitOptions);
    setActiveJob(panel, jobId);
    upsertJob(panel, {
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
        stateSnapshot: JSON.parse(JSON.stringify(state))
      }
    });
    incrementOutputCounter();
    persistPanelJobs(panel);
    renderJobList(panel);

    updateStageUi(panel, progressFill, progressText, {
      progress: 0.3,
      stage: 'solver_setup',
      message: 'Job accepted by backend'
    });

    if (stopBtn) {
      stopBtn.disabled = false;
    }

    panel.pollSimulationStatus();

    // Non-blocking: download simulation mesh artifact if toggle is on
    const downloadMeshToggle = document.getElementById('download-sim-mesh');
    if (downloadMeshToggle && downloadMeshToggle.checked && panel.activeJobId) {
      downloadMeshArtifact(panel.activeJobId).catch(err => {
        console.warn('Mesh artifact download failed (non-blocking):', err.message);
      });
    }
  } catch (error) {
    console.error('Simulation error:', error);
    panel.completedStatusMessage = null;
    panel.simulationStartedAtMs = null;
    panel.lastSimulationDurationMs = null;
    updateStageUi(panel, progressFill, progressText, {
      progress: 1,
      stage: 'error',
      message: error.message
    });
    showError(`Simulation failed: ${error.message}`);
    runBtn.disabled = false;

    setTimeout(() => {
      setProgressVisible(progressDiv, false);
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

export function pollSimulationStatus(panel) {
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');
  const runBtn = document.getElementById('run-simulation-btn');
  const progressDiv = document.getElementById('simulation-progress');

  if (panel.isPolling) {
    return;
  }
  panel.isPolling = true;

  const pollOnce = async () => {
    try {
      const payload = await panel.solver.listJobs({ limit: 200, offset: 0 });
      const remoteItems = Array.isArray(payload?.items) ? payload.items.map((item) => toUiJob(item)) : [];
      const knownIds = new Set();
      for (const item of remoteItems) {
        upsertJob(panel, item);
        knownIds.add(item.id);
      }

      for (const local of allJobs(panel)) {
        if (knownIds.has(local.id)) {
          continue;
        }
        if (local.status === 'queued' || local.status === 'running') {
          upsertJob(panel, {
            ...local,
            status: 'error',
            stage: 'error',
            stageMessage: 'Job missing from backend after reconnect',
            errorMessage: 'Job state was lost after backend restart or reset.',
            completedAt: local.completedAt || new Date().toISOString()
          });
        }
      }

      if (!panel.activeJobId || !panel.jobs.has(panel.activeJobId)) {
        const active = allJobs(panel).find((job) => job.status === 'queued' || job.status === 'running');
        setActiveJob(panel, active ? active.id : null);
      }

      if (panel.activeJobId && panel.jobs.has(panel.activeJobId)) {
        const active = panel.jobs.get(panel.activeJobId);
        updateStageUi(panel, progressFill, progressText, {
          progress: active.progress ?? 0,
          stage: active.stage || active.status || 'queued',
          message: active.stageMessage || active.errorMessage || ''
        });

        if (active.status === 'complete' && !panel.resultCache.has(active.id)) {
          updateStageUi(panel, progressFill, progressText, {
            progress: 1,
            stage: 'finalizing',
            message: 'Fetching and rendering results'
          });
          const results = await panel.solver.getResults(active.id);
          panel.resultCache.set(active.id, results);
          panel.lastResults = results;
          panel.displayResults(results);
          updateStageUi(panel, progressFill, progressText, {
            progress: 1,
            stage: 'complete',
            message: 'Results ready'
          });
        } else if (active.status === 'complete' && panel.resultCache.has(active.id)) {
          panel.lastResults = panel.resultCache.get(active.id);
        } else if (active.status === 'error' || active.status === 'cancelled') {
          panel.completedStatusMessage = null;
          panel.simulationStartedAtMs = null;
          panel.lastSimulationDurationMs = null;
          updateStageUi(panel, progressFill, progressText, {
            progress: active.status === 'cancelled' ? 0 : 1,
            stage: active.status,
            message: active.errorMessage || active.stageMessage || `Simulation ${active.status}`
          });
        }
      }

      const anyActive = hasActiveJobs(panel);
      panel.pollBackoffMs = 1000;
      panel.pollDelayMs = anyActive ? 1000 : 10000;

      if (!anyActive) {
        runBtn.disabled = false;
        setTimeout(() => {
          setProgressVisible(progressDiv, false);
          restoreConnectionStatus(panel);
        }, 1000);
      }

      persistPanelJobs(panel);
      renderJobList(panel);
    } catch (error) {
      panel.pollBackoffMs = Math.min(10000, panel.pollBackoffMs === 1000 ? 2000 : panel.pollBackoffMs === 2000 ? 5000 : 10000);
      panel.pollDelayMs = panel.pollBackoffMs;
      console.error('Status polling error:', error);
      updateStageUi(panel, progressFill, progressText, {
        progress: 1,
        stage: 'error',
        message: 'Error checking status'
      });
      showError('Error checking simulation status.');
      runBtn.disabled = false;
      restoreConnectionStatus(panel);
    } finally {
      clearPollTimer(panel);
      setPollTimer(
        panel,
        setTimeout(() => {
          pollOnce().catch(() => {});
        }, panel.pollDelayMs)
      );
    }
  };

  pollOnce().catch(() => {});
}

/**
 * Download the simulation mesh artifact (.msh) for a job.
 * Fire-and-forget — does not block simulation progress.
 */
async function downloadMeshArtifact(jobId) {
  // Brief delay to allow backend to finish mesh generation and store artifact
  await new Promise(r => setTimeout(r, 2000));

  const resp = await fetch(`http://localhost:8000/api/mesh-artifact/${jobId}`);
  if (!resp.ok) {
    throw new Error(`Mesh artifact not available (${resp.status})`);
  }
  const mshText = await resp.text();
  const blob = new Blob([mshText], { type: 'text/plain' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `simulation_mesh_${jobId}.msh`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export { downloadMeshArtifact };
