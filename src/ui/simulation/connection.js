export async function checkSolverConnection(panel) {
  const statusDot = document.getElementById('solver-status');
  const statusText = document.getElementById('solver-status-text');

  try {
    const isConnected = await panel.solver.checkConnection();

    if (isConnected) {
      statusDot.className = 'status-dot connected';
      statusText.textContent = 'Connected to BEM solver';
      document.getElementById('run-simulation-btn').disabled = false;
    } else {
      statusDot.className = 'status-dot disconnected';
      statusText.textContent = 'BEM solver not available';
      document.getElementById('run-simulation-btn').disabled = true;
    }
  } catch (error) {
    statusDot.className = 'status-dot disconnected';
    statusText.textContent = 'BEM solver not available (using mock data)';
    // Don't disable button - allow mock simulation
    document.getElementById('run-simulation-btn').disabled = false;
  }

  // Check again in 10 seconds
  setTimeout(() => checkSolverConnection(panel), 10000);
}
