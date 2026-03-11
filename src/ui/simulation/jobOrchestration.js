// @ts-check

/**
 * Shared simulation-job orchestration state helpers.
 * Keeps polling and job action modules coordinated through one boundary.
 */

/**
 * @param {object} panel
 * @param {ReturnType<typeof setTimeout>} timer
 */
export function setPollTimer(panel, timer) {
  panel.pollTimer = timer;
  panel.pollInterval = timer;
}

/**
 * @param {object} panel
 */
export function clearPollTimer(panel) {
  if (panel.pollTimer) {
    clearTimeout(panel.pollTimer);
  }
  panel.pollTimer = null;
  panel.pollInterval = null;
  // Reset isPolling so pollSimulationStatus() can restart the loop if needed.
  panel.isPolling = false;
  panel.consecutivePollFailures = 0;
}

/**
 * @param {object} panel
 * @param {string|null|undefined} jobId
 */
export function setActiveJob(panel, jobId) {
  panel.activeJobId = jobId || null;
  panel.currentJobId = panel.activeJobId;
}
