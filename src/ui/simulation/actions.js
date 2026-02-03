export async function runSimulation(panel) {
  const runBtn = document.getElementById('run-simulation-btn');
  const progressDiv = document.getElementById('simulation-progress');
  const progressFill = document.getElementById('progress-fill');
  const progressText = document.getElementById('progress-text');
  const resultsContainer = document.getElementById('results-container');

  // Get simulation settings
  const config = {
    frequencyStart: parseInt(document.getElementById('freq-start').value),
    frequencyEnd: parseInt(document.getElementById('freq-end').value),
    numFrequencies: parseInt(document.getElementById('freq-steps').value),
    simulationType: document.getElementById('sim-type').value
  };

  // Get polar directivity configuration
  const angleRangeStr = document.getElementById('polar-angle-range').value;
  const angleRangeParts = angleRangeStr.split(',').map((s) => parseFloat(s.trim()));

  config.polarConfig = {
    angle_range: angleRangeParts.length === 3 ? angleRangeParts : [0, 180, 37],
    norm_angle: parseFloat(document.getElementById('polar-norm-angle').value),
    distance: parseFloat(document.getElementById('polar-distance').value),
    inclination: parseFloat(document.getElementById('polar-inclination').value)
  };

  // Validate settings
  if (config.frequencyStart >= config.frequencyEnd) {
    alert('Start frequency must be less than end frequency');
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

    progressFill.style.width = '20%';
    progressText.textContent = 'Submitting to BEM solver...';

    // Check if real solver is available
    const isConnected = await panel.solver.checkConnection();

    if (isConnected) {
      // Submit to real solver
      panel.currentJobId = await panel.solver.submitSimulation(config, meshData);

      progressFill.style.width = '30%';
      progressText.textContent = 'Simulation running...';

      // Poll for results
      panel.pollSimulationStatus();
    } else {
      // Use mock solver for demonstration
      progressFill.style.width = '50%';
      progressText.textContent = 'Running mock simulation...';

      await panel.runMockSimulation(config);

      progressFill.style.width = '100%';
      progressText.textContent = 'Complete!';

      setTimeout(() => {
        progressDiv.style.display = 'none';
        panel.displayResults();
        runBtn.disabled = false;
      }, 1000);
    }
  } catch (error) {
    console.error('Simulation error:', error);
    progressText.textContent = `Error: ${error.message}`;
    runBtn.disabled = false;

    setTimeout(() => {
      progressDiv.style.display = 'none';
    }, 3000);
  }
}

export async function runMockSimulation() {
  // Simulate processing time
  return new Promise((resolve) => {
    setTimeout(resolve, 2000);
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
      runBtn.disabled = false;
    }
  }, 1000);
}
