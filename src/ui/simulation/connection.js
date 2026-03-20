import {
  cacheRuntimeHealth,
  describeSelectedDevice,
  summarizeRuntimeCapabilities,
} from "../runtimeCapabilities.js";
import { showAlertDialog } from "../feedback.js";
import {
  formatDependencyBlockMessage,
  summarizeRuntimeDoctor,
} from "../../modules/runtime/health.js";

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
    .join("|");

  return {
    signature,
    title: "Backend Dependencies Missing",
    message: formatDependencyBlockMessage(health, {
      includeOptional: false,
      fallback:
        "Required backend dependencies are missing. Simulation and OCC meshing stay blocked until these are installed.",
    }),
  };
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
    const dependencyWarning = buildRequiredDependencyWarning(health);

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
            ? "Required backend dependencies are missing. See install guidance."
            : defaultHelpText;
          statusHelp.classList.remove("is-hidden");
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
        tone: "warning",
        closeLabel: "Dismiss",
      }).finally(() => {
        activeDependencyWarning = null;
      });
    } else if (!dependencyWarning) {
      lastDependencyWarningSignature = null;
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
    lastDependencyWarningSignature = null;
  }

  scheduleNextCheck();
}
