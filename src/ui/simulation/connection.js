import {
  cacheRuntimeHealth,
  describeSelectedDevice,
  summarizeRuntimeCapabilities,
} from '../runtimeCapabilities.js';

export async function checkSolverConnection(panel) {
  const statusDot = document.getElementById('solver-status');
  const statusText = document.getElementById('solver-status-text');
  const statusHelp = document.getElementById('solver-status-help');
  const runButton = document.getElementById('run-simulation-btn');
  const defaultHelpText = 'Requires the Python backend running on localhost:8000';

  const scheduleNextCheck = () => {
    if (panel.connectionPollTimer) {
      clearTimeout(panel.connectionPollTimer);
    }
    panel.connectionPollTimer = setTimeout(() => checkSolverConnection(panel), 10000);
  };

  if (!statusDot || !statusText || !runButton) {
    scheduleNextCheck();
    return;
  }

  try {
    const health = await panel.solver.getHealthStatus();
    cacheRuntimeHealth(health);
    const runtime = summarizeRuntimeCapabilities(health);
    const isConnected = runtime.fullyReady;

    statusDot.className = isConnected ? 'status-dot connected' : 'status-dot disconnected';

    // Preserve live stage text while simulation is running.
    if (!panel.stageStatusActive) {
      if (isConnected) {
        statusText.textContent = panel.completedStatusMessage || 'Solver ready';
        runButton.disabled = false;
        const deviceText = describeSelectedDevice(health);
        if (statusHelp) {
          if (deviceText) {
            statusHelp.textContent = deviceText;
            statusHelp.classList.remove('is-hidden');
          } else {
            statusHelp.classList.add('is-hidden');
          }
        }
      } else {
        panel.completedStatusMessage = null;
        statusText.textContent = 'Backend connected — solver not available';
        runButton.disabled = true;
        if (statusHelp) {
          statusHelp.textContent = defaultHelpText;
          statusHelp.classList.remove('is-hidden');
        }
      }
    }
  } catch (error) {
    statusDot.className = 'status-dot disconnected';
    if (!panel.stageStatusActive) {
      panel.completedStatusMessage = null;
      statusText.textContent = 'Solver offline';
      runButton.disabled = true;
      if (statusHelp) {
        statusHelp.textContent = defaultHelpText;
        statusHelp.classList.remove('is-hidden');
      }
    }
  }

  scheduleNextCheck();
}
