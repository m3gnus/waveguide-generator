import {
  cacheRuntimeHealth,
  describeSelectedDevice,
  summarizeRuntimeCapabilities,
} from "../runtimeCapabilities.js";
import {
  createDependencyStatusPanel,
  updateDependencyStatusPanel,
} from "../dependencyStatus.js";
import { summarizeRuntimeDoctor } from "../../modules/runtime/health.js";

let dependencyPanel = null;

function ensureDependencyPanel(statusHelp) {
  if (!dependencyPanel && statusHelp?.parentElement) {
    const actionsSection = statusHelp.closest(".actions-section");
    if (actionsSection) {
      dependencyPanel = createDependencyStatusPanel(null);
      dependencyPanel.id = "dependency-status-panel";
      actionsSection.insertBefore(dependencyPanel, statusHelp.nextSibling);
    }
  }
  return dependencyPanel;
}

export async function checkSolverConnection(panel) {
  const statusDot = document.getElementById("solver-status");
  const statusText = document.getElementById("solver-status-text");
  const statusHelp = document.getElementById("solver-status-help");
  const runButton = document.getElementById("run-simulation-btn");
  const defaultHelpText =
    "Requires the Python backend running on localhost:8000";

  const scheduleNextCheck = () => {
    if (panel.connectionPollTimer) {
      clearTimeout(panel.connectionPollTimer);
    }
    panel.connectionPollTimer = setTimeout(
      () => checkSolverConnection(panel),
      10000,
    );
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

    statusDot.className = isConnected
      ? "status-dot connected"
      : "status-dot disconnected";

    if (!panel.stageStatusActive) {
      if (isConnected) {
        statusText.textContent = panel.completedStatusMessage || "Solver ready";
        runButton.disabled = false;
        const deviceText = describeSelectedDevice(health);
        if (statusHelp) {
          if (deviceText) {
            statusHelp.textContent = deviceText;
            statusHelp.classList.remove("is-hidden");
          } else {
            statusHelp.classList.add("is-hidden");
          }
        }
      } else {
        panel.completedStatusMessage = null;
        statusText.textContent = "Backend connected — dependency issues detected";
        runButton.disabled = true;
        if (statusHelp) {
          statusHelp.textContent = doctor.requiredIssues.length > 0
            ? "Required backend dependencies are missing. See details below."
            : defaultHelpText;
          statusHelp.classList.remove("is-hidden");
        }
      }
    }

    const depPanel = ensureDependencyPanel(statusHelp);
    if (depPanel) {
      updateDependencyStatusPanel(depPanel, health);
    }
  } catch (error) {
    statusDot.className = "status-dot disconnected";
    if (!panel.stageStatusActive) {
      panel.completedStatusMessage = null;
      statusText.textContent = "Solver offline";
      runButton.disabled = true;
      if (statusHelp) {
        statusHelp.textContent = defaultHelpText;
        statusHelp.classList.remove("is-hidden");
      }
    }
    const depPanel = ensureDependencyPanel(statusHelp);
    if (depPanel) {
      updateDependencyStatusPanel(depPanel, null);
    }
  }

  scheduleNextCheck();
}
