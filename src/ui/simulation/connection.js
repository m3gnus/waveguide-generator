export async function checkSolverConnection(panel) {
  const statusDot = document.getElementById('solver-status');
  const statusText = document.getElementById('solver-status-text');
  const statusHelp = document.getElementById('solver-status-help');
  const runButton = document.getElementById('run-simulation-btn');

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
    const isConnected = Boolean(health?.solverReady);

    statusDot.className = isConnected ? 'status-dot connected' : 'status-dot disconnected';

    // Preserve live stage text while simulation is running.
    if (!panel.stageStatusActive) {
      if (isConnected) {
        statusText.textContent = 'Connected to BEM solver';
        runButton.disabled = false;
        if (statusHelp) statusHelp.classList.add('is-hidden');
      } else {
        statusText.textContent = 'Backend online, solver runtime unavailable';
        runButton.disabled = true;
        if (statusHelp) statusHelp.classList.remove('is-hidden');
      }
    }
  } catch (error) {
    statusDot.className = 'status-dot disconnected';
    if (!panel.stageStatusActive) {
      statusText.textContent = 'BEM solver not available (using mock data)';
      // Don't disable button - allow mock simulation
      runButton.disabled = false;
      if (statusHelp) statusHelp.classList.remove('is-hidden');
    }
  }

  scheduleNextCheck();
}
