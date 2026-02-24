import { AppEvents } from '../../events.js';
import {
  clearFailedSimulations,
  exportJobResults,
  loadJobScript,
  redoJob,
  removeJobFromFeed,
  stopSimulation,
  viewJobResults
} from './actions.js';

export function setupEventListeners(panel) {
  // Stop simulation button
  const stopBtn = document.getElementById('stop-simulation-btn');
  if (stopBtn) {
    stopBtn.addEventListener('click', () => stopSimulation(panel));
  }

  // Tab switching
  const tabButtons = Array.from(document.querySelectorAll('.tab-btn'));
  tabButtons.forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const tab = e.currentTarget.dataset.tab;
      switchTab(tab);
    });
    btn.addEventListener('keydown', (event) => {
      handleTabKeydown(event, tabButtons);
    });
  });

  // Run simulation button
  const runBtn = document.getElementById('run-simulation-btn');
  if (runBtn) {
    runBtn.addEventListener('click', () => panel.runSimulation());
  }

  const refreshBtn = document.getElementById('refresh-jobs-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', () => panel.pollSimulationStatus());
  }

  const clearFailedBtn = document.getElementById('clear-failed-jobs-btn');
  if (clearFailedBtn) {
    clearFailedBtn.addEventListener('click', async () => clearFailedSimulations(panel));
  }

  const jobList = document.getElementById('simulation-jobs-list');
  if (jobList) {
    jobList.addEventListener('click', async (event) => {
      const target = event.target;
      if (!(target instanceof HTMLElement)) return;
      const action = target.dataset.jobAction;
      const jobId = target.dataset.jobId;
      if (!action || !jobId) return;

      if (action === 'stop') {
        panel.activeJobId = jobId;
        panel.currentJobId = jobId;
        await stopSimulation(panel);
      }

      if (action === 'view') {
        await viewJobResults(panel, jobId);
      }

      if (action === 'export') {
        await exportJobResults(panel, jobId);
      }

      if (action === 'load-script') {
        loadJobScript(panel, jobId);
      }

      if (action === 'redo') {
        redoJob(panel, jobId);
      }

      if (action === 'remove') {
        await removeJobFromFeed(panel, jobId);
      }
    });
  }

  // Store reference so dispose() can remove the listener.
  panel._onStateUpdated = (state) => { panel.syncSimulationSettings(state); };
  AppEvents.on('state:updated', panel._onStateUpdated);

  function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      const isActive = btn.dataset.tab === tabName;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-selected', isActive ? 'true' : 'false');
      btn.setAttribute('tabindex', isActive ? '0' : '-1');
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach((content) => {
      const isActive = content.id === `${tabName}-tab`;
      content.classList.toggle('active', isActive);
      content.hidden = !isActive;
    });

    AppEvents.emit('ui:tab-changed', { tab: tabName });
  }
}

function handleTabKeydown(event, buttons) {
  if (!(event.currentTarget instanceof HTMLElement) || buttons.length === 0) return;
  const currentIndex = buttons.findIndex((button) => button === event.currentTarget);
  if (currentIndex < 0) return;

  let nextIndex = -1;
  if (event.key === 'ArrowRight') nextIndex = (currentIndex + 1) % buttons.length;
  if (event.key === 'ArrowLeft') nextIndex = (currentIndex - 1 + buttons.length) % buttons.length;
  if (event.key === 'Home') nextIndex = 0;
  if (event.key === 'End') nextIndex = buttons.length - 1;

  if (nextIndex === -1) return;
  event.preventDefault();
  buttons[nextIndex].focus();
  buttons[nextIndex].click();
}
