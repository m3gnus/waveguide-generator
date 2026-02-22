function _modeLabel(mode) {
  switch (String(mode || '').trim().toLowerCase()) {
    case 'opencl_gpu':
      return 'OpenCL GPU';
    case 'opencl_cpu':
      return 'OpenCL CPU';
    case 'numba':
      return 'Numba CPU';
    case 'auto':
      return 'Auto';
    default:
      return String(mode || 'Unknown').trim();
  }
}

function _selectedDeviceText(health) {
  const deviceInfo = health?.deviceInterface;
  if (!deviceInfo || typeof deviceInfo !== 'object') {
    return '';
  }

  const selectedMode = _modeLabel(deviceInfo.selected_mode || deviceInfo.requested_mode || 'auto');
  const deviceName = String(deviceInfo.device_name || '').trim();
  const hasDeviceName = deviceName && deviceName.toLowerCase() !== 'none';
  const modeAlreadyMentionsCpu = selectedMode.toLowerCase().includes('cpu');
  const deviceNameIsCpuLabel = /^cpu$/i.test(deviceName);
  const shouldShowDeviceSuffix = hasDeviceName && !(modeAlreadyMentionsCpu && deviceNameIsCpuLabel);
  return shouldShowDeviceSuffix
    ? `Selected solver backend: ${selectedMode} (${deviceName})`
    : `Selected solver backend: ${selectedMode}`;
}

export async function checkSolverConnection(panel) {
  const statusDot = document.getElementById('solver-status');
  const statusText = document.getElementById('solver-status-text');
  const statusHelp = document.getElementById('solver-status-help');
  const runButton = document.getElementById('run-simulation-btn');
  const defaultHelpText = 'BEM solver requires Python backend running on localhost:8000';

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
    const solverReady = Boolean(health?.solverReady);
    const occReady = Boolean(health?.occBuilderReady);
    const isConnected = solverReady && occReady;

    statusDot.className = isConnected ? 'status-dot connected' : 'status-dot disconnected';

    // Preserve live stage text while simulation is running.
    if (!panel.stageStatusActive) {
      if (isConnected) {
        statusText.textContent = panel.completedStatusMessage || 'Connected to adaptive BEM solver';
        runButton.disabled = false;
        const deviceText = _selectedDeviceText(health);
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
        statusText.textContent = 'Backend online, adaptive solver runtime unavailable';
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
      statusText.textContent = 'BEM solver backend unavailable';
      runButton.disabled = true;
      if (statusHelp) {
        statusHelp.textContent = defaultHelpText;
        statusHelp.classList.remove('is-hidden');
      }
    }
  }

  scheduleNextCheck();
}
