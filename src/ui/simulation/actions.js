import { showError, showMessage, showSuccess } from '../feedback.js';

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
  progressText.textContent = 'Simulation cancelled';
  showMessage('Simulation cancelled.', { type: 'info', duration: 2000 });
  runBtn.disabled = false;
  stopBtn.disabled = true;

  // Hide progress bar after a short delay
  setTimeout(() => {
    if (progressDiv.style.display !== 'none') {
      progressDiv.style.display = 'none';
    }
  }, 1000);

  // Reset job ID
  panel.currentJobId = null;
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
  progressDiv.style.display = 'block';
  resultsContainer.style.display = 'none';
  progressFill.style.width = '0%';
  progressText.textContent = 'Preparing mesh...';

  try {
    // Get current mesh data
    const meshData = await panel.prepareMeshForSimulation();
    if (!meshData.surfaceTags || meshData.surfaceTags.length !== meshData.indices.length / 3) {
      throw new Error('Mesh payload is invalid: missing canonical surface tags.');
    }

    progressFill.style.width = '20%';
    progressText.textContent = 'Submitting to BEM solver...';

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

      progressFill.style.width = '30%';
      progressText.textContent = 'Simulation running...';

      // Enable stop button when simulation starts
      if (stopBtn) {
        stopBtn.disabled = false;
      }

      // Poll for results
      panel.pollSimulationStatus();
    } else {
      // Use mock solver for demonstration
      progressFill.style.width = '50%';
      progressText.textContent = 'Running mock simulation...';

      await panel.runMockSimulation(config);

      progressFill.style.width = '100%';
      progressText.textContent = 'Complete!';
      showSuccess('Mock simulation complete.');

      setTimeout(() => {
        progressDiv.style.display = 'none';
        panel.displayResults();
        runBtn.disabled = false;
      }, 1000);
    }
  } catch (error) {
    console.error('Simulation error:', error);
    progressText.textContent = `Error: ${error.message}`;
    showError(`Simulation failed: ${error.message}`);
    runBtn.disabled = false;

    setTimeout(() => {
      progressDiv.style.display = 'none';
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
        progressFill.style.width = `${Math.min(100, progressFill.offsetWidth)}%`;
        progressText.textContent = `Simulation ${status.status}`;
        runBtn.disabled = false;
        return;
      }

      if (status.status === 'running') {
        const progress = Math.min(95, 30 + (status.progress * 65));
        progressFill.style.width = `${progress}%`;
        progressText.textContent = `Simulating... ${Math.round(status.progress * 100)}%`;
      } else if (status.status === 'complete') {
        clearInterval(panel.pollInterval);
        progressFill.style.width = '100%';
        progressText.textContent = 'Complete!';

        // Fetch and display results
        const results = await panel.solver.getResults(panel.currentJobId);
        panel.lastResults = results;
        panel.displayResults(results);

        setTimeout(() => {
          progressDiv.style.display = 'none';
          runBtn.disabled = false;
        }, 1000);
      } else if (status.status === 'error') {
        clearInterval(panel.pollInterval);
        progressText.textContent = `Error: ${status.message || 'Simulation failed'}`;
        runBtn.disabled = false;
      }
    } catch (error) {
      clearInterval(panel.pollInterval);
      console.error('Status polling error:', error);
      progressText.textContent = 'Error checking status';
      showError('Error checking simulation status.');
      runBtn.disabled = false;
    }
  }, 1000);
}
