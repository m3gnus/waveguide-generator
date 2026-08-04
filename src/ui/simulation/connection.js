import {
  cacheRuntimeHealth,
  describeSelectedDevice,
  summarizeRuntimeCapabilities,
} from '../runtimeCapabilities.js';
import { showAlertDialog } from '../feedback.js';
import {
  formatDependencyBlockMessage,
  summarizeRuntimeDoctor,
} from '../../modules/runtime/health.js';
import {
  describeBackendStartupStatus,
  fetchBackendStartupStatus,
} from '../../modules/runtime/backendStartup.js';

let lastDependencyWarningSignature = null;
let activeDependencyWarning = null;

export function buildRequiredDependencyWarning(health) {
  const doctor = summarizeRuntimeDoctor(health);
  if (doctor.requiredIssues.length === 0) {
    return null;
  }

  const signature = doctor.requiredIssues
    .map((component) => `${component.id}:${component.status}`)
    .sort()
    .join('|');

  return {
    signature,
    title: 'Backend Dependencies Missing',
    message: formatDependencyBlockMessage(health, {
      includeOptional: false,
      fallback:
        'Required backend dependencies are missing. Simulation and backend meshing stay blocked until these are installed.',
    }),
  };
}

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
    const doctor = summarizeRuntimeDoctor(health);
    const isConnected = runtime.fullyReady;
    const dependencyWarning = buildRequiredDependencyWarning(health);

    statusDot.className = isConnected ? 'status-dot connected' : 'status-dot disconnected';

    if (!panel.stageStatusActive) {
      if (isConnected) {
        const baseMsg = panel.completedStatusMessage || 'Solver ready';
        const deviceText = describeSelectedDevice(health);
        statusText.textContent = deviceText ? `${baseMsg} · ${deviceText}` : baseMsg;
        runButton.disabled = false;
        if (statusHelp) statusHelp.classList.add('is-hidden');
      } else {
        panel.completedStatusMessage = null;
        statusText.textContent = 'Backend connected — dependency issues detected';
        runButton.disabled = true;
        if (statusHelp) {
          statusHelp.textContent =
            doctor.requiredIssues.length > 0
              ? 'Required backend dependencies are missing. See install guidance.'
              : defaultHelpText;
          statusHelp.classList.remove('is-hidden');
        }
      }
    }

    if (
      dependencyWarning &&
      dependencyWarning.signature !== lastDependencyWarningSignature &&
      !activeDependencyWarning
    ) {
      lastDependencyWarningSignature = dependencyWarning.signature;
      activeDependencyWarning = showAlertDialog({
        title: dependencyWarning.title,
        message: dependencyWarning.message,
        tone: 'warning',
        closeLabel: 'Dismiss',
      }).finally(() => {
        activeDependencyWarning = null;
      });
    } else if (!dependencyWarning) {
      lastDependencyWarningSignature = null;
    }
  } catch {
    let startupStatus = null;
    try {
      startupStatus = await fetchBackendStartupStatus();
    } catch {
      // The frontend may be hosted separately from the WG launcher. Fall back
      // to the ordinary offline message when no launcher diagnostic exists.
    }
    const offline = describeBackendStartupStatus(startupStatus);
    statusDot.className = 'status-dot disconnected';
    if (!panel.stageStatusActive) {
      panel.completedStatusMessage = null;
      statusText.textContent = offline.label;
      runButton.disabled = true;
      if (statusHelp) {
        statusHelp.textContent = offline.help || defaultHelpText;
        statusHelp.classList.remove('is-hidden');
      }
    }
    lastDependencyWarningSignature = null;
  }

  scheduleNextCheck();
}
