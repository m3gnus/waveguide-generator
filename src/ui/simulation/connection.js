export async function checkSolverConnection(panel) {
  const statusDot = document.getElementById('solver-status');
  const statusText = document.getElementById('solver-status-text');
  const statusHelp = document.getElementById('solver-status-help');

  try {
    const isConnected = await panel.solver.checkConnection();

    if (isConnected) {
      statusDot.className = 'status-dot connected';
      statusText.textContent = 'Connected to BEM solver';
      document.getElementById('run-simulation-btn').disabled = false;
      if (statusHelp) statusHelp.classList.add('is-hidden');
    } else {
      statusDot.className = 'status-dot disconnected';
      statusText.textContent = 'BEM solver not available';
      document.getElementById('run-simulation-btn').disabled = true;
      if (statusHelp) statusHelp.classList.remove('is-hidden');
    }
  } catch (error) {
    statusDot.className = 'status-dot disconnected';
    statusText.textContent = 'BEM solver not available (using mock data)';
    // Don't disable button - allow mock simulation
    document.getElementById('run-simulation-btn').disabled = false;
    if (statusHelp) statusHelp.classList.remove('is-hidden');
  }

  // Check again in 10 seconds
  setTimeout(() => checkSolverConnection(panel), 10000);
}
