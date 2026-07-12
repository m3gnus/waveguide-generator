import { showError, showMessage } from '../feedback.js';
import { persistCurrentExportFields } from '../fileOps.js';
import {
  getFrequencySpacing,
  getMeshValidationMode,
  getVerbose,
} from '../settings/simBasicSettings.js';
import { getSolverBackend } from '../settings/simAdvancedSettings.js';
import {
  getCurrentSimulationManagementSettings,
  getTaskListMinRatingFilter,
  getTaskListSortPreference,
} from '../settings/simulationManagementSettings.js';
import { buildPolarStatePatchFromConfig, readPolarStateSettings } from './polarSettings.js';
import { allJobs, formatJobListLabel, hasActiveJobs } from './jobTracker.js';
import {
  updateStageUi,
  setProgressVisible,
  restoreConnectionStatus,
  formatElapsedDuration,
  resolveJobDurationMs,
  resetProgressAnnouncement,
} from './progressUi.js';
import {
  clearPollTimer,
  clearProgressHideTimer,
  scheduleProgressHide,
  setActiveJob,
} from './jobOrchestration.js';
import {
  summarizePersistedSimulationMeshStats,
  validateSimulationConfig,
} from '../../modules/simulation/domain.js';
import {
  applySimulationJobScriptState,
  readSimulationState,
  updateSimulationStateParams,
} from '../../modules/simulation/state.js';
import { resolveAvailableSolveCounter } from '../../modules/simulation/naming.js';
import { resolveClearedFailedJobIds } from '../../modules/simulation/jobs.js';
import { deleteTaskWorkspaceDirectory } from './workspaceTasks.js';
import {
  clearSimulationControllerJobs,
  ensureSimulationControllerJobResults,
  submitSimulationControllerJob,
  recordSimulationControllerExport,
  recordSimulationControllerRating,
  removeSimulationControllerJob,
  stopSimulationControllerJob,
} from './controller.js';

export { validateSimulationConfig };

const GEOMETRY_DIAGNOSTIC_ROWS = Object.freeze([
  ['throat_disc', 'Throat Disc'],
  ['horn_wall', 'Horn Wall'],
  ['inner_wall', 'Inner Wall'],
  ['outer_wall', 'Outer Wall'],
  ['mouth_rim', 'Mouth Rim'],
  ['throat_return', 'Throat Return'],
  ['rear_cap', 'Rear Cap'],
  ['enc_front', 'Enclosure Front'],
  ['enc_side', 'Enclosure Side'],
  ['enc_rear', 'Enclosure Rear'],
  ['enc_edge', 'Enclosure Edge'],
]);
const JOB_EXPORT_MENU_ITEMS = Object.freeze([
  ['selected', 'Selected Formats', 'Export formats enabled in Export Settings'],
  ['mwg_config', 'Config (.txt)', 'Export task parameter config'],
  ['step', 'STEP (.step)', 'Export waveguide STEP surface'],
  ['stl', 'STL (.stl)', 'Export viewport STL mesh'],
  ['fusion_csv', 'Fusion CSV', 'Export profiles and slices CSV'],
  ['png', 'Charts (PNG)', 'Export result charts'],
  ['csv', 'Results CSV', 'Export frequency result data'],
  ['json', 'Results JSON', 'Export full result payload'],
  ['txt', 'Report (.txt)', 'Export summary report'],
  ['polar_csv', 'Polar CSV', 'Export polar directivity data'],
  ['impedance_csv', 'Impedance CSV', 'Export impedance result data'],
  ['vacs', 'ABEC VACS', 'Export ABEC spectrum data'],
]);

export function renderSimulationMeshDiagnostics(summary = null) {
  const container = document.getElementById('simulation-mesh-diagnostics');
  if (!container) {
    return;
  }

  if (!summary) {
    container.innerHTML =
      '<div class="simulation-mesh-diagnostics-placeholder">Mesh stats appear here before you submit. Updated with solver data once the job starts.</div>';
    return;
  }

  const provenance = summary.provenance === 'backend' ? 'backend' : 'preview';
  const sourceLabel = provenance === 'backend' ? 'Solver Geometry' : 'Simulation Geometry';
  const activeGeometryRows = GEOMETRY_DIAGNOSTIC_ROWS.filter(
    ([identity]) => Number(summary.identityTriangleCounts?.[identity] ?? 0) > 0
  );

  const identityRows = activeGeometryRows
    .map(
      ([identity, label]) => `
    <div class="simulation-mesh-diagnostics-region">
      <span class="simulation-mesh-diagnostics-tag-label">${label}</span>
      <span class="simulation-mesh-diagnostics-tag-count">${summary.identityTriangleCounts?.[identity] ?? 0} tris</span>
    </div>
  `
    )
    .join('');
  const emptyStateMarkup =
    activeGeometryRows.length === 0
      ? '<div class="simulation-mesh-diagnostics-empty">No geometry regions were classified for this mesh.</div>'
      : '';
  const warnings = formatGeometryDiagnosticWarnings(summary);

  const warningMarkup =
    warnings.length > 0
      ? `<div class="simulation-mesh-diagnostics-warning">${warnings.map((warning) => escapeHtml(warning)).join('<br>')}</div>`
      : '';

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
  const status = String(job.status || '').toLowerCase();
  const progress = Math.round((Number(job.progress) || 0) * 100);
  const detail = String(job.stageMessage || job.errorMessage || '').trim();
  const stage = String(job.stage || '').toLowerCase();

  if (status === 'complete') {
    const duration = formatElapsedDuration(resolveJobDurationMs(job));
    return duration ? `Complete (${duration})` : 'Complete';
  }
  if (status === 'cancelled') return 'Cancelled';
  if (stage === 'cancelling' || job.cancellationRequested) {
    return detail || 'Stopping...';
  }
  if (status === 'queued') return 'Queued';
  if (status === 'running') {
    if (detail && !/simulation\s+running|running/i.test(detail)) {
      return detail;
    }
    return `Running (${progress}%)`;
  }
  if (status === 'error') {
    // Prefer the specific backend error over the generic "Simulation failed" stage
    // message, and only suppress the exact generic placeholder — the previous regex
    // also matched any real error containing the word "error" and hid it.
    const errorDetail = String(job.errorMessage || job.stageMessage || '').trim();
    if (errorDetail && !/^simulation failed\.?$/i.test(errorDetail)) {
      return `Failed: ${errorDetail}`;
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

function readJobId(job) {
  return String(job?.id ?? '');
}

function formatGeometryDiagnosticWarnings(summary = null) {
  const formatted = [];
  const warnings = Array.isArray(summary?.warnings) ? summary.warnings : [];
  const throatDiscCount = Number(summary?.identityTriangleCounts?.throat_disc ?? 0);

  for (const rawWarning of warnings) {
    const warning = String(rawWarning ?? '').trim();
    if (!warning) {
      continue;
    }
    if (/source surface tag/i.test(warning) || /no source surface tag/i.test(warning)) {
      formatted.push(
        throatDiscCount > 0
          ? 'Throat Disc is present, but it is not classified as the source region.'
          : 'Throat Disc is missing from the mesh.'
      );
      continue;
    }
    if (/face-identity diagnostics are unavailable/i.test(warning)) {
      formatted.push('Geometry region breakdown is unavailable for this job.');
      continue;
    }
    if (/unsupported surface tags/i.test(warning)) {
      formatted.push('Mesh contains unsupported surface classifications.');
      continue;
    }
    formatted.push(warning);
  }

  return Array.from(new Set(formatted));
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
    timeStyle: 'medium',
  }).format(parsed);
  return `Started: ${formatted}`;
}

function describeJobFeedSource(panel) {
  const mode = panel?.jobSourceMode === 'folder' ? 'folder' : 'backend';
  return {
    mode,
    label: mode === 'folder' ? 'Folder Tasks' : 'Backend Jobs',
  };
}

function renderRatingStars(job) {
  const jobId = readJobId(job);
  const jobIdAttr = escapeHtml(jobId);
  const label = job.label || jobId;
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
            class="simulation-job-rating-star${isActive ? ' is-active' : ''}"
            data-job-rating="${ratingValue}"
            data-job-id="${jobIdAttr}"
            aria-label="Rate ${escapeHtml(label)} ${ratingValue} out of 5"
            aria-pressed="${isActive ? 'true' : 'false'}"
            title="Rate ${ratingValue} out of 5"
          >${isActive ? '&#9733;' : '&#9734;'}</button>
        `;
      }).join('')}
    </div>
  `;
}

function renderJobActionButton({
  action,
  jobIdAttr,
  label,
  title,
  className = 'btn-secondary button-compact',
}) {
  return `<button type="button" class="${className}" data-job-action="${action}" data-job-id="${jobIdAttr}" title="${title}">${label}</button>`;
}

function renderJobExportMenu(jobIdAttr) {
  const items = JOB_EXPORT_MENU_ITEMS.map(
    ([formatId, label, title]) => `
      <button
        type="button"
        role="menuitem"
        data-job-action="export-format"
        data-job-id="${jobIdAttr}"
        data-export-format="${escapeHtml(formatId)}"
        title="${escapeHtml(title)}"
      >${escapeHtml(label)}</button>
    `
  ).join('');

  return `
    <div class="export-menu export-menu--job">
      <button
        type="button"
        class="btn-secondary button-compact export-menu-trigger"
        aria-haspopup="menu"
        aria-expanded="false"
        title="Export task"
      >Export</button>
      <div class="export-menu-list" role="menu" aria-label="Export task">
        ${items}
      </div>
    </div>
  `;
}

function syncJobListPreferenceControls() {
  const settings = getCurrentSimulationManagementSettings();
  const sortEl = document.getElementById('simulation-jobs-sort');
  if (sortEl && sortEl.value !== settings.defaultSort) {
    sortEl.value = settings.defaultSort;
  }

  const ratingEl = document.getElementById('simulation-jobs-min-rating');
  const ratingValue = String(settings.minRatingFilter);
  if (ratingEl && ratingEl.value !== ratingValue) {
    ratingEl.value = ratingValue;
  }
}

function buildJobListSignature(panel, source, jobs, sortBy, minRating) {
  return JSON.stringify({
    source: source.mode,
    activeJobId: String(panel.activeJobId ?? ''),
    sortBy,
    minRating,
    jobs: jobs.map((job) => ({
      id: readJobId(job),
      status: job.status,
      progress: job.progress,
      stage: job.stage,
      stageMessage: job.stageMessage,
      errorMessage: job.errorMessage,
      cancellationRequested: Boolean(job.cancellationRequested),
      rating: job.rating,
      label: job.label,
      createdAt: job.createdAt,
      queuedAt: job.queuedAt,
      startedAt: job.startedAt,
      completedAt: job.completedAt,
      hasScript: Boolean(job.script),
    })),
  });
}

function findJobRow(list, jobId) {
  if (!list?.querySelectorAll || !jobId) return null;
  return (
    Array.from(list.querySelectorAll('[data-job-id]')).find(
      (row) => row.getAttribute?.('data-job-id') === jobId
    ) || null
  );
}

function captureJobListInteractionState(list) {
  if (!list?.querySelector) return null;

  const openMenu = list.querySelector('.export-menu.is-open');
  const openMenuJobId = openMenu?.closest?.('[data-job-id]')?.getAttribute?.('data-job-id') || null;
  const focused = typeof document !== 'undefined' ? document.activeElement : null;
  if (!focused || typeof list.contains !== 'function' || !list.contains(focused)) {
    return { openMenuJobId, focusedControl: null };
  }

  const jobId = focused.closest?.('[data-job-id]')?.getAttribute?.('data-job-id') || null;
  if (!jobId) {
    return { openMenuJobId, focusedControl: null };
  }

  return {
    openMenuJobId,
    focusedControl: {
      jobId,
      action: focused.getAttribute?.('data-job-action') || null,
      exportFormat: focused.getAttribute?.('data-export-format') || null,
      rating: focused.getAttribute?.('data-job-rating') || null,
      isExportTrigger: Boolean(focused.classList?.contains('export-menu-trigger')),
    },
  };
}

function restoreJobListInteractionState(list, state) {
  if (!state) return;

  if (state.openMenuJobId) {
    const row = findJobRow(list, state.openMenuJobId);
    const menu = row?.querySelector?.('.export-menu');
    if (menu) {
      menu.classList.add('is-open');
      menu.querySelector?.('.export-menu-trigger')?.setAttribute('aria-expanded', 'true');
    }
  }

  const focus = state.focusedControl;
  if (!focus) return;
  const row = findJobRow(list, focus.jobId);
  if (!row?.querySelectorAll) return;
  const control = Array.from(
    row.querySelectorAll('button, [data-job-action], [data-job-rating]')
  ).find((candidate) => {
    if (focus.isExportTrigger && candidate.classList?.contains('export-menu-trigger')) return true;
    return (
      candidate.getAttribute?.('data-job-action') === focus.action &&
      candidate.getAttribute?.('data-export-format') === focus.exportFormat &&
      candidate.getAttribute?.('data-job-rating') === focus.rating
    );
  });
  control?.focus?.();
}

function readOutputNameAndCounter() {
  const name =
    (document.getElementById('export-prefix')?.value || 'simulation').trim() || 'simulation';
  const counterEl = document.getElementById('export-counter');
  const counterRaw = Number(counterEl?.value);
  const counter = Number.isFinite(counterRaw) && counterRaw >= 1 ? Math.floor(counterRaw) : 1;
  return { name, counter };
}

function setOutputCounter(counter) {
  const counterEl = document.getElementById('export-counter');
  if (!counterEl) return;
  const next = Number(counter);
  counterEl.value = String(Number.isFinite(next) && next >= 1 ? Math.floor(next) : 1);
  persistCurrentExportFields();
}

function setSimulationInputsFromScript(script = {}) {
  const mappings = [
    ['freq-start', script.frequencyStart],
    ['freq-end', script.frequencyEnd],
    ['freq-steps', script.numFrequencies],
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
  persistCurrentExportFields();

  if (script.polarConfig) {
    const currentState = readSimulationState();
    updateSimulationStateParams(
      buildPolarStatePatchFromConfig(currentState?.params, script.polarConfig)
    );
  }
}

async function ensureJobResults(panel, jobId, { display = true } = {}) {
  const job = panel.jobs?.get(jobId) || null;
  const result = await ensureSimulationControllerJobResults(panel, jobId, {
    display,
    displayResults: (results) => {
      panel.displayResults(results, job);
    },
  });

  if (result.reason === 'missing_job') {
    showError('Simulation task not found.');
    return null;
  }
  if (result.reason === 'not_complete') {
    showError('Results are only available for completed simulations.');
    return null;
  }
  return result.results;
}

export function renderJobList(panel) {
  if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
    return;
  }

  const list = document.getElementById('simulation-jobs-list');
  if (!list) return;
  panel.app?.resultsDock?.onJobsUpdated();

  const sourceEl = document.getElementById('simulation-jobs-source-label');
  const source = describeJobFeedSource(panel);
  if (sourceEl) {
    sourceEl.textContent = source.label;
  }

  syncJobListPreferenceControls();
  const sortBy = getTaskListSortPreference();
  const minRating = getTaskListMinRatingFilter();
  const jobs = allJobs(panel, {
    sortBy,
    minRating,
  });
  const signature = buildJobListSignature(panel, source, jobs, sortBy, minRating);
  // A skeleton write (restoreJobs/refresh) replaces the list content without
  // touching the memo, so the memo may only skip when no skeleton is showing.
  const listShowsSkeleton =
    typeof list.querySelector === 'function' && Boolean(list.querySelector('.skeleton-job-item'));
  if (
    panel._jobListElement === list &&
    panel._jobListSignature === signature &&
    !listShowsSkeleton
  ) {
    return;
  }
  const interactionState = captureJobListInteractionState(list);

  if (jobs.length === 0) {
    list.innerHTML = `<div class="simulation-job-meta">No ${source.mode === 'folder' ? 'folder tasks' : 'backend jobs'} yet.</div>`;
    panel._jobListElement = list;
    panel._jobListSignature = signature;
    return;
  }

  list.innerHTML = jobs
    .map((job) => {
      const jobId = readJobId(job);
      const jobIdAttr = escapeHtml(jobId);
      const isActive = String(panel.activeJobId ?? '') === jobId;
      const statusClass =
        job.status === 'running'
          ? 'is-running'
          : job.status === 'complete'
            ? 'is-completed'
            : job.status === 'error'
              ? 'is-failed'
              : '';
      const canRerun = (job.status === 'error' || job.status === 'cancelled') && job.script;
      const canStop =
        (job.status === 'queued' || job.status === 'running') && job.stage !== 'cancelling';
      const jobSummary = formatJobSummary(job);
      return `
    <div class="simulation-job-item ${isActive ? 'is-active' : ''} ${statusClass}" data-job-id="${jobIdAttr}">
      <div class="simulation-job-header">
        <div class="simulation-job-title" title="${escapeHtml(formatTimestampTooltip(job))}">
          <span>${escapeHtml(formatJobListLabel(job))}</span>
        </div>
        <button type="button" class="simulation-job-remove" data-job-action="remove" data-job-id="${jobIdAttr}" aria-label="Remove" title="Remove">&#x2715;</button>
      </div>
      <div class="simulation-job-status-row">
        <div class="simulation-job-meta" title="${escapeHtml(jobSummary)}">${escapeHtml(jobSummary)}</div>
        ${job.status === 'complete' ? renderRatingStars(job) : ''}
      </div>
      <div class="simulation-job-actions">
        ${job.status === 'complete' ? renderJobActionButton({ action: 'view', jobIdAttr, label: 'Results', title: 'View results' }) : ''}
        ${job.status === 'complete' ? renderJobExportMenu(jobIdAttr) : ''}
        ${job.status === 'complete' && source.mode === 'folder' ? renderJobActionButton({ action: 'open-folder', jobIdAttr, label: 'View Output', title: 'Open output folder' }) : ''}
        ${job.script ? renderJobActionButton({ action: 'load-script', jobIdAttr, label: 'Load', title: 'Load parameters' }) : ''}
        ${canRerun ? renderJobActionButton({ action: 'redo', jobIdAttr, label: 'Rerun', title: 'Rerun' }) : ''}
        ${canStop ? renderJobActionButton({ action: 'stop', jobIdAttr, label: 'Stop', title: 'Stop', className: 'btn-tertiary button-compact' }) : ''}
      </div>
    </div>
  `;
    })
    .join('');
  panel._jobListElement = list;
  panel._jobListSignature = signature;
  restoreJobListInteractionState(list, interactionState);
}

export async function viewJobResults(panel, jobId) {
  const results = await ensureJobResults(panel, jobId, { display: true });
  if (!results) return;
  panel.openViewResults();
  panel.pollSimulationStatus();
}

export async function exportJobResults(panel, jobId, selectedFormats = null) {
  const results = await ensureJobResults(panel, jobId, { display: true });
  if (!results) return;
  const job = panel.jobs?.get(jobId) || null;
  const bundle = await panel.exportResults({ job, selectedFormats });
  if (bundle && (bundle.exportedFiles.length > 0 || bundle.failures.length > 0)) {
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
    showError('No saved parameters found for this simulation.');
    return;
  }

  const script = job.script;
  applySimulationJobScriptState(script, {
    source: 'simulation-job-load-script',
  });

  setSimulationInputsFromScript(script);
  // A loaded config can have a completely different scale — reframe once so
  // the user is not left staring at a zoomed-in fragment of the new geometry.
  panel.app?.focusOnModel?.();
  showMessage(`Loaded parameters from ${job.label || jobId}.`, {
    type: 'info',
    duration: 2500,
  });
}

export async function rateJob(panel, jobId, rating) {
  const next = await recordSimulationControllerRating(panel, jobId, rating);
  if (!next) {
    showError('Simulation task not found.');
    return;
  }
  renderJobList(panel);
}

export async function redoJob(panel, jobId) {
  const job = panel.jobs?.get(jobId);
  if (!job?.script) {
    showError('No saved parameters found for this simulation.');
    return;
  }
  loadJobScript(panel, jobId);

  // Remove the failed/cancelled job before re-running
  try {
    await panel.solver.deleteJob(jobId);
  } catch {
    /* best-effort */
  }
  removeSimulationControllerJob(panel, jobId);
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

  // Delete the task folder from the output directory
  await deleteTaskWorkspaceDirectory(job);

  if (!removeSimulationControllerJob(panel, jobId)) {
    return;
  }
  renderJobList(panel);
}

export async function clearFailedSimulations(panel) {
  const localFailedIds = allJobs(panel)
    .filter((job) => job.status === 'error')
    .map((job) => job.id);

  if (localFailedIds.length === 0) {
    showMessage('No failed simulations to clear.', {
      type: 'info',
      duration: 2200,
    });
    return;
  }

  let deletedIds;
  try {
    const response = await panel.solver.clearFailedJobs();
    deletedIds = resolveClearedFailedJobIds(localFailedIds, response);
  } catch (error) {
    showError(`Failed to clear failed simulations from backend: ${error.message}`);
    return;
  }

  const removed = clearSimulationControllerJobs(panel, deletedIds);
  renderJobList(panel);
  showMessage(
    removed > 0
      ? `Deleted ${removed} failed simulation${removed === 1 ? '' : 's'} from database.`
      : 'No failed simulations found in database.',
    { type: 'info', duration: 2200 }
  );
}

export async function stopSimulation(panel) {
  clearProgressHideTimer(panel);
  const targetJobId = panel.activeJobId || panel.currentJobId;
  const { stopError, cancelledJob } = await stopSimulationControllerJob(panel, targetJobId);
  if (stopError) {
    console.warn('Failed to call stop API:', stopError);
    showError(`Failed to stop simulation: ${stopError.message}`);
    return;
  }
  renderJobList(panel);

  if (cancelledJob?.status === 'cancelled') {
    panel.completedStatusMessage = null;
    panel.simulationStartedAtMs = null;
    panel.lastSimulationDurationMs = null;
    updateStageUi(panel, {
      progress: 0,
      stage: 'cancelled',
      message: cancelledJob.stageMessage || 'Simulation cancelled by user',
    });
    showMessage('Simulation cancelled.', { type: 'info', duration: 2000 });
  } else if (cancelledJob) {
    setProgressVisible(true);
    updateStageUi(panel, {
      progress: Number(cancelledJob.progress) || 0,
      stage: cancelledJob.stage || 'cancelling',
      message:
        cancelledJob.stageMessage || 'Cancellation requested. Waiting for backend worker to stop.',
    });
    showMessage('Cancellation requested. Waiting for backend worker to stop.', {
      type: 'info',
      duration: 2400,
    });
  }
  if (!hasActiveJobs(panel)) {
    scheduleProgressHide(
      panel,
      () => {
        setProgressVisible(false);
      },
      1000
    );
    clearPollTimer(panel);
    setActiveJob(panel, null);
    restoreConnectionStatus(panel);
  }
}

export async function runSimulation(panel) {
  clearProgressHideTimer(panel);
  panel.completedStatusMessage = null;

  // Get simulation settings
  const config = {
    frequencyStart: Number(document.getElementById('freq-start').value),
    frequencyEnd: Number(document.getElementById('freq-end').value),
    numFrequencies: Number(document.getElementById('freq-steps').value),
    meshValidationMode: getMeshValidationMode(),
    frequencySpacing: getFrequencySpacing(),
    verbose: getVerbose(),
    solverBackend: getSolverBackend(),
    solverMode: document.getElementById('solver-mode')?.value || 'auto',
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
    spherical_sampling: polarSettings.sphericalSampling === true,
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
    stage: 'mesh_generation',
    message: 'Preparing simulation mesh',
  });

  try {
    // The mesh payload is a contract placeholder. HornLab mesher builds the
    // actual solve mesh on the backend from waveguide_params.
    const meshData = await panel.prepareMeshForSimulation();

    updateStageUi(panel, {
      progress: 0.2,
      stage: 'mesh_generation',
      message: 'Mesh ready, submitting to BEM solver',
    });

    const { name: outputName, counter: requestedCounter } = readOutputNameAndCounter();
    const counter = resolveAvailableSolveCounter({
      outputName,
      counter: requestedCounter,
      existingJobs: allJobs(panel),
    });
    await submitSimulationControllerJob(panel, {
      config,
      meshData,
      outputName,
      counter,
    });
    setOutputCounter(counter + 1);
    renderJobList(panel);

    updateStageUi(panel, {
      progress: 0.3,
      stage: 'initializing',
      message: 'Job accepted by backend',
    });

    panel.pollSimulationStatus();
  } catch (error) {
    console.error('Simulation error:', error);
    panel.completedStatusMessage = null;
    panel.simulationStartedAtMs = null;
    panel.lastSimulationDurationMs = null;
    updateStageUi(panel, {
      progress: 1,
      stage: 'error',
      message: error.message,
    });
    showError(`Simulation failed: ${error.message}`);

    scheduleProgressHide(
      panel,
      () => {
        setProgressVisible(false);
        restoreConnectionStatus(panel);
      },
      3000
    );
  }
}

export function renderBackendSimulationMeshDiagnostics(meshStats = null) {
  if (!meshStats) {
    return;
  }
  renderSimulationMeshDiagnostics(summarizePersistedSimulationMeshStats(meshStats));
}
