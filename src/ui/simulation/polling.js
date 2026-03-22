// @ts-check

import { updateStageUi, setProgressVisible, restoreConnectionStatus } from './progressUi.js';
import { showError } from '../feedback.js';
import { getAutoExportOnComplete } from '../settings/simulationManagementSettings.js';
import { persistSimulationGenerationArtifacts } from './workspaceTasks.js';
import {
  hasActiveJobs
} from './jobTracker.js';
import {
  renderBackendSimulationMeshDiagnostics,
  renderJobList
} from './jobActions.js';
import { setPollTimer } from './jobOrchestration.js';
import {
  ensureSimulationControllerJobResults,
  recordSimulationControllerExport,
  reconcileSimulationControllerRemoteJobs
} from './controller.js';
export { setPollTimer, clearPollTimer, setActiveJob } from './jobOrchestration.js';

const ACTIVE_POLL_MS = 1000;
const IDLE_POLL_MS = 15000;
const MAX_POLL_BACKOFF_MS = 30000;

/**
 * @typedef {Object} SolverPollingApi
 * @property {(query?: {limit?: number, offset?: number}) => Promise<{items?: unknown[]}>} listJobs
 * @property {(jobId: string) => Promise<unknown>} getResults
 * @property {(jobId: string) => Promise<string>} [getMeshArtifact]
 */

/**
 * @typedef {Object} PollingPanel
 * @property {ReturnType<typeof setTimeout>|null} pollTimer
 * @property {ReturnType<typeof setTimeout>|null} pollInterval
 * @property {boolean} isPolling
 * @property {number} consecutivePollFailures
 * @property {number} pollBackoffMs
 * @property {number} pollDelayMs
 * @property {string|null} activeJobId
 * @property {string|null} currentJobId
 * @property {Map<string, any>} jobs
 * @property {Map<string, any>} resultCache
 * @property {unknown} lastResults
 * @property {SolverPollingApi} solver
 * @property {(results: unknown) => void} displayResults
 * @property {string|null} completedStatusMessage
 * @property {number|null} simulationStartedAtMs
 * @property {number|null} lastSimulationDurationMs
 */

/**
 * @param {number} currentBackoffMs
 * @returns {number}
 */
function nextBackoffMs(currentBackoffMs) {
  const current = Number(currentBackoffMs);
  if (!Number.isFinite(current) || current < ACTIVE_POLL_MS) {
    return ACTIVE_POLL_MS * 2;
  }
  return Math.min(MAX_POLL_BACKOFF_MS, current * 2);
}

/**
 * @param {PollingPanel} panel
 */
export function pollSimulationStatus(panel) {
  // Guard must run before any DOM access to support test environments.
  if (panel.isPolling) {
    return;
  }
  panel.isPolling = true;

  const pollOnce = async () => {
    try {
      const { activeJob, anyActive } = await reconcileSimulationControllerRemoteJobs(panel, {
        onManifestSyncError: (error) => {
          console.warn('Task manifest sync failed during polling:', error);
        }
      });

      if (activeJob) {
        if (activeJob.meshStats && typeof panel.app?.setSimulationMeshStats === 'function') {
          panel.app.setSimulationMeshStats(activeJob.meshStats);
          renderBackendSimulationMeshDiagnostics(activeJob.meshStats);
        }
        updateStageUi(panel, {
          progress: activeJob.progress ?? 0,
          stage: activeJob.stage || activeJob.status || 'queued',
          message: activeJob.stageMessage || activeJob.errorMessage || ''
        });

        if (activeJob.status === 'complete') {
          if (!panel.resultCache.has(activeJob.id)) {
            updateStageUi(panel, {
              progress: 1,
              stage: 'finalizing',
              message: 'Fetching and rendering results'
            });
          }
          const result = await ensureSimulationControllerJobResults(panel, activeJob.id, {
            display: true,
            displayResults: (results) => {
              panel.displayResults(results);
            }
          });
          if (result.ok) {
            updateStageUi(panel, {
              progress: 1,
              stage: 'complete',
              message: 'Results ready'
            });

            if (activeJob.justCompleted) {
              let meshArtifactText = null;
              if (activeJob.hasMeshArtifact && typeof panel.solver?.getMeshArtifact === 'function') {
                try {
                  meshArtifactText = await panel.solver.getMeshArtifact(activeJob.id);
                } catch (error) {
                  console.warn('Mesh artifact fetch failed during completion persistence:', error);
                }
              }

              const persistedArtifacts = await persistSimulationGenerationArtifacts(activeJob, {
                results: result.results,
                meshArtifactText
              });
              if (persistedArtifacts.warnings.length > 0) {
                console.warn('Generation artifact persistence warnings:', persistedArtifacts.warnings);
              }

              const exportPatch = {
                rawResultsFile: persistedArtifacts.rawResultsFile,
                meshArtifactFile: persistedArtifacts.meshArtifactFile,
                justCompleted: false
              };

              if (getAutoExportOnComplete()) {
                const bundle = await panel.exportResults({
                  job: activeJob,
                  auto: true
                });
                exportPatch.exportedFiles = bundle?.exportedFiles ?? [];
                exportPatch.autoExportCompletedAt = activeJob.completedAt ?? new Date().toISOString();
              }

              await recordSimulationControllerExport(panel, activeJob.id, exportPatch);
            }
          }
        } else if (activeJob.status === 'error' || activeJob.status === 'cancelled') {
          panel.completedStatusMessage = null;
          panel.simulationStartedAtMs = null;
          panel.lastSimulationDurationMs = null;
          updateStageUi(panel, {
            progress: activeJob.status === 'cancelled' ? 0 : 1,
            stage: activeJob.status,
            message: activeJob.errorMessage || activeJob.stageMessage || `Simulation ${activeJob.status}`
          });
        }
      }

      panel.pollBackoffMs = ACTIVE_POLL_MS;
      panel.consecutivePollFailures = 0;
      panel.pollDelayMs = anyActive ? ACTIVE_POLL_MS : IDLE_POLL_MS;

      if (!anyActive) {
        setTimeout(() => {
          setProgressVisible(false);
          restoreConnectionStatus(panel);
        }, 1000);
      }

      renderJobList(panel);
    } catch (error) {
      panel.consecutivePollFailures = (Number(panel.consecutivePollFailures) || 0) + 1;
      panel.pollBackoffMs = nextBackoffMs(panel.pollBackoffMs);
      panel.pollDelayMs = hasActiveJobs(panel)
        ? panel.pollBackoffMs
        : Math.max(IDLE_POLL_MS, panel.pollBackoffMs);
      console.error('Status polling error:', error);
      updateStageUi(panel, {
        progress: 1,
        stage: 'error',
        message: 'Error checking status'
      });
      showError('Error checking simulation status.');
      restoreConnectionStatus(panel);
    } finally {
      // Internal reschedule: clear previous timer ref and set new one without
      // resetting isPolling (clearPollTimer resets isPolling; use inline here
      // so the loop guard remains active while the next tick is pending).
      if (panel.pollTimer) clearTimeout(panel.pollTimer);
      const nextTimer = setTimeout(() => { pollOnce().catch(() => {}); }, panel.pollDelayMs);
      setPollTimer(panel, nextTimer);
    }
  };

  pollOnce().catch(() => {});
}
