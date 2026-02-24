// Stage labels and progress UI helpers.
// DOM refs are lazily cached on first use to reduce repeated getElementById calls in hot paths.

export const STAGE_LABELS = {
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

export function formatElapsedDuration(durationMs) {
  if (durationMs === null || durationMs === undefined || durationMs === '') {
    return null;
  }
  const numericDuration = Number(durationMs);
  if (!Number.isFinite(numericDuration) || numericDuration < 0) {
    return null;
  }

  const roundedSeconds = Math.round(numericDuration / 1000);
  const hours = Math.floor(roundedSeconds / 3600);
  const minutes = Math.floor((roundedSeconds % 3600) / 60);
  const seconds = roundedSeconds % 60;

  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}`;
  }
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function parseTimestampMs(raw) {
  if (!raw) return null;
  const parsed = Date.parse(raw);
  if (!Number.isFinite(parsed)) return null;
  return parsed;
}

export function resolveJobDurationMs(job) {
  const startedAtMs = parseTimestampMs(job.startedAt || job.queuedAt || job.createdAt);
  const completedAtMs = parseTimestampMs(job.completedAt);
  if (startedAtMs === null || completedAtMs === null) {
    return null;
  }
  const durationMs = completedAtMs - startedAtMs;
  if (!Number.isFinite(durationMs) || durationMs < 0) {
    return null;
  }
  return durationMs;
}

export function resolveStageDetail(stage, message, pct) {
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

// DOM cache — lazily populated on first use to reduce repeated getElementById calls in hot paths.
let _dom = null;

/**
 * Returns a cached reference to simulation progress DOM elements.
 * Caches on first call; all consumers share the same refs for the page lifetime.
 */
export function getSimulationDom() {
  if (!_dom) {
    _dom = {
      progressFill: document.getElementById('progress-fill'),
      progressText: document.getElementById('progress-text'),
      progressBar: document.getElementById('simulation-progressbar'),
      progressDiv: document.getElementById('simulation-progress'),
      runBtn: document.getElementById('run-simulation-btn'),
      stopBtn: document.getElementById('stop-simulation-btn'),
      statusDot: document.getElementById('solver-status'),
      statusText: document.getElementById('solver-status-text'),
      statusHelp: document.getElementById('solver-status-help'),
    };
  }
  return _dom;
}

export function setProgressVisible(visible) {
  const { progressDiv } = getSimulationDom();
  if (!progressDiv) return;
  if (visible) {
    progressDiv.classList.remove('is-hidden');
    progressDiv.style.display = 'block';
  } else {
    progressDiv.classList.add('is-hidden');
    progressDiv.style.display = 'none';
  }
}

export function updateProgressUi({
  progress = 0,
  stage = 'bem_solve',
  message = ''
} = {}) {
  const { progressFill, progressText, progressBar } = getSimulationDom();
  const clamped = Math.max(0, Math.min(1, Number(progress) || 0));
  const pct = Math.round(clamped * 100);
  const key = normalizeStage(stage);
  const label = STAGE_LABELS[key] || key;
  const detail = resolveStageDetail(key, message, pct);

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

export function updateConnectionStageUi(panel, {
  progress = 0,
  stage = 'bem_solve',
  message = ''
} = {}) {
  const { statusDot, statusText: statusTextEl, statusHelp } = getSimulationDom();
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
  if (statusTextEl) {
    statusTextEl.textContent = `Stage ${step}/4: ${label} (${pct}%)`;
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

export function updateStageUi(panel, payload) {
  updateProgressUi(payload);
  updateConnectionStageUi(panel, payload);
}

export function restoreConnectionStatus(panel) {
  if (!panel) return;
  panel.stageStatusActive = false;
  panel.checkSolverConnection();
}
