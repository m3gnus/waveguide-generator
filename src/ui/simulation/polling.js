import { updateStageUi, setProgressVisible, restoreConnectionStatus, getSimulationDom } from './progressUi.js';
import { showError } from '../feedback.js';
import {
  allJobs,
  hasActiveJobs,
  persistPanelJobs,
  upsertJob,
  toUiJob
} from './jobTracker.js';
// renderJobList is imported from jobActions.js; this creates a circular reference between
// polling.js and jobActions.js. Both modules only use each other's exports inside function
// bodies (never at module init time), so ESM live bindings resolve correctly at runtime.
import { renderJobList } from './jobActions.js';

export function setPollTimer(panel, timer) {
  panel.pollTimer = timer;
  panel.pollInterval = timer;
}

export function clearPollTimer(panel) {
  if (panel.pollTimer) {
    clearTimeout(panel.pollTimer);
  }
  panel.pollTimer = null;
  panel.pollInterval = null;
  // Reset isPolling so pollSimulationStatus() can restart the loop if needed.
  panel.isPolling = false;
}

export function setActiveJob(panel, jobId) {
  panel.activeJobId = jobId || null;
  panel.currentJobId = panel.activeJobId;
}

export function pollSimulationStatus(panel) {
  // Guard must run before any DOM access to support test environments.
  if (panel.isPolling) {
    return;
  }
  panel.isPolling = true;

  const pollOnce = async () => {
    try {
      const { runBtn } = getSimulationDom();
      const payload = await panel.solver.listJobs({ limit: 200, offset: 0 });
      const remoteItems = Array.isArray(payload?.items) ? payload.items.map((item) => toUiJob(item)) : [];
      const knownIds = new Set();
      for (const item of remoteItems) {
        upsertJob(panel, item);
        knownIds.add(item.id);
      }

      for (const local of allJobs(panel)) {
        if (knownIds.has(local.id)) {
          continue;
        }
        if (local.status === 'queued' || local.status === 'running') {
          upsertJob(panel, {
            ...local,
            status: 'error',
            stage: 'error',
            stageMessage: 'Job missing from backend after reconnect',
            errorMessage: 'Job state was lost after backend restart or reset.',
            completedAt: local.completedAt || new Date().toISOString()
          });
        }
      }

      if (!panel.activeJobId || !panel.jobs.has(panel.activeJobId)) {
        const active = allJobs(panel).find((job) => job.status === 'queued' || job.status === 'running');
        setActiveJob(panel, active ? active.id : null);
      }

      if (panel.activeJobId && panel.jobs.has(panel.activeJobId)) {
        const active = panel.jobs.get(panel.activeJobId);
        updateStageUi(panel, {
          progress: active.progress ?? 0,
          stage: active.stage || active.status || 'queued',
          message: active.stageMessage || active.errorMessage || ''
        });

        if (active.status === 'complete' && !panel.resultCache.has(active.id)) {
          updateStageUi(panel, {
            progress: 1,
            stage: 'finalizing',
            message: 'Fetching and rendering results'
          });
          const results = await panel.solver.getResults(active.id);
          panel.resultCache.set(active.id, results);
          panel.lastResults = results;
          panel.displayResults(results);
          updateStageUi(panel, {
            progress: 1,
            stage: 'complete',
            message: 'Results ready'
          });
        } else if (active.status === 'complete' && panel.resultCache.has(active.id)) {
          panel.lastResults = panel.resultCache.get(active.id);
        } else if (active.status === 'error' || active.status === 'cancelled') {
          panel.completedStatusMessage = null;
          panel.simulationStartedAtMs = null;
          panel.lastSimulationDurationMs = null;
          updateStageUi(panel, {
            progress: active.status === 'cancelled' ? 0 : 1,
            stage: active.status,
            message: active.errorMessage || active.stageMessage || `Simulation ${active.status}`
          });
        }
      }

      const anyActive = hasActiveJobs(panel);
      panel.pollBackoffMs = 1000;
      panel.pollDelayMs = anyActive ? 1000 : 10000;

      if (!anyActive) {
        if (runBtn) runBtn.disabled = false;
        setTimeout(() => {
          setProgressVisible(false);
          restoreConnectionStatus(panel);
        }, 1000);
      }

      persistPanelJobs(panel);
      renderJobList(panel);
    } catch (error) {
      panel.pollBackoffMs = Math.min(10000, panel.pollBackoffMs === 1000 ? 2000 : panel.pollBackoffMs === 2000 ? 5000 : 10000);
      panel.pollDelayMs = panel.pollBackoffMs;
      console.error('Status polling error:', error);
      updateStageUi(panel, {
        progress: 1,
        stage: 'error',
        message: 'Error checking status'
      });
      showError('Error checking simulation status.');
      const { runBtn } = getSimulationDom();
      if (runBtn) runBtn.disabled = false;
      restoreConnectionStatus(panel);
    } finally {
      // Internal reschedule: clear previous timer ref and set new one without
      // resetting isPolling (clearPollTimer resets isPolling; use inline here
      // so the loop guard remains active while the next tick is pending).
      if (panel.pollTimer) clearTimeout(panel.pollTimer);
      const nextTimer = setTimeout(() => { pollOnce().catch(() => {}); }, panel.pollDelayMs);
      panel.pollTimer = nextTimer;
      panel.pollInterval = nextTimer;
    }
  };

  pollOnce().catch(() => {});
}
