import { showError, showMessage, showSuccess } from '../feedback.js';

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
  const detail = resolveStageDetail(key, message, pct);

  if (progressFill) {
    progressFill.style.width = `${pct}%`;
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

  // Call backend API to stop the job if we have a job ID
  if (panel.currentJobId) {
    try {
      await fetch(`http://localhost:8000/api/stop/${panel.currentJobId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
      });
    } catch (error) {
      console.warn('Failed to call stop API:', error);
      // Continue with local cleanup even if API call fails
    }
  }

  // Clear the polling interval
  if (panel.pollInterval) {
    clearInterval(panel.pollInterval);
    panel.pollInterval = null;
  }

  // Update UI to show cancellation
  updateStageUi(panel, progressFill, progressText, {
    progress: 0,
    stage: 'cancelled',
    message: 'Simulation cancelled by user'
  });
  showMessage('Simulation cancelled.', { type: 'info', duration: 2000 });
  runBtn.disabled = false;
  stopBtn.disabled = true;

  // Hide progress bar after a short delay
  setTimeout(() => {
    setProgressVisible(progressDiv, false);
  }, 1000);

  // Reset job ID
  panel.currentJobId = null;
  restoreConnectionStatus(panel);
}

export async function runSimulation(panel) {
  const runBtn = document.getElementById('run-simulation-btn');
  const progressDiv = document.getElementById('simulation-progress');
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');
  const resultsContainer = document.getElementById('results-container');

  // Get simulation settings
  const config = {
    frequencyStart: Number(document.getElementById('freq-start').value),
    frequencyEnd: Number(document.getElementById('freq-end').value),
    numFrequencies: Number(document.getElementById('freq-steps').value),
    simulationType: document.getElementById('sim-type').value,
    circSymProfile: parseInt(document.getElementById('circsym-profile')?.value ?? '-1', 10)
  };

  // Get polar directivity configuration
  const angleStart = parseFloat(document.getElementById('polar-angle-start').value) || 0;
  const angleEnd = parseFloat(document.getElementById('polar-angle-end').value) || 180;
  const angleStep = parseFloat(document.getElementById('polar-angle-step').value) || 5;
  const angleCount = Math.floor((angleEnd - angleStart) / angleStep) + 1;

  config.polarConfig = {
    angle_range: [angleStart, angleEnd, angleCount],
    norm_angle: parseFloat(document.getElementById('polar-norm-angle').value),
    distance: parseFloat(document.getElementById('polar-distance').value),
    inclination: parseFloat(document.getElementById('polar-inclination').value)
  };

  // Validate settings
  const validationError = validateSimulationConfig(config);
  if (validationError) {
    showError(validationError);
    return;
  }

  // Show progress
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

    // Check if real solver is available
    const isConnected = await panel.solver.checkConnection();

    if (isConnected) {
      // Submit to real solver
      panel.currentJobId = await panel.solver.submitSimulation(config, meshData);

      updateStageUi(panel, progressFill, progressText, {
        progress: 0.3,
        stage: 'solver_setup',
        message: 'Job accepted by backend'
      });

      // Enable stop button when simulation starts
      if (stopBtn) {
        stopBtn.disabled = false;
      }

      // Poll for results
      panel.pollSimulationStatus();
    } else {
      // Use mock solver for demonstration
      updateStageUi(panel, progressFill, progressText, {
        progress: 0.5,
        stage: 'bem_solve',
        message: 'Running mock simulation'
      });

      await panel.runMockSimulation(config);

      updateStageUi(panel, progressFill, progressText, {
        progress: 1,
        stage: 'complete',
        message: 'Mock simulation complete'
      });
      showSuccess('Mock simulation complete.');

      setTimeout(() => {
        setProgressVisible(progressDiv, false);
        panel.displayResults();
        runBtn.disabled = false;
        restoreConnectionStatus(panel);
      }, 1000);
    }
  } catch (error) {
    console.error('Simulation error:', error);
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

  panel.pollInterval = setInterval(async () => {
    try {
      const status = await panel.solver.getJobStatus(panel.currentJobId);

      // Check if simulation was cancelled
      if (status.status === 'cancelled' || status.status === 'error') {
        clearInterval(panel.pollInterval);
        panel.pollInterval = null;
        updateStageUi(panel, progressFill, progressText, {
          progress: status.status === 'cancelled' ? 0 : 1,
          stage: status.status,
          message: status.message || `Simulation ${status.status}`
        });
        runBtn.disabled = false;
        restoreConnectionStatus(panel);
        return;
      }

      if (status.status === 'running') {
        updateStageUi(panel, progressFill, progressText, {
          progress: status.progress,
          stage: status.stage,
          message: status.stage_message
        });
      } else if (status.status === 'queued') {
        updateStageUi(panel, progressFill, progressText, {
          progress: status.progress,
          stage: status.stage || 'queued',
          message: status.stage_message || 'Waiting for solver worker'
        });
      } else if (status.status === 'complete') {
        clearInterval(panel.pollInterval);
        panel.pollInterval = null;
        updateStageUi(panel, progressFill, progressText, {
          progress: 1,
          stage: 'finalizing',
          message: 'Fetching and rendering results'
        });

        // Fetch and display results
        const results = await panel.solver.getResults(panel.currentJobId);
        panel.lastResults = results;
        panel.displayResults(results);

        updateStageUi(panel, progressFill, progressText, {
          progress: 1,
          stage: 'complete',
          message: 'Results ready'
        });

        setTimeout(() => {
          setProgressVisible(progressDiv, false);
          runBtn.disabled = false;
          restoreConnectionStatus(panel);
        }, 1000);
      } else if (status.status === 'error') {
        clearInterval(panel.pollInterval);
        panel.pollInterval = null;
        updateStageUi(panel, progressFill, progressText, {
          progress: 1,
          stage: 'error',
          message: status.message || 'Simulation failed'
        });
        runBtn.disabled = false;
        restoreConnectionStatus(panel);
      }
    } catch (error) {
      clearInterval(panel.pollInterval);
      panel.pollInterval = null;
      console.error('Status polling error:', error);
      updateStageUi(panel, progressFill, progressText, {
        progress: 1,
        stage: 'error',
        message: 'Error checking status'
      });
      showError('Error checking simulation status.');
      runBtn.disabled = false;
      restoreConnectionStatus(panel);
    }
  }, 1000);
}
