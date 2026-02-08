import { AppEvents } from '../../events.js';
import { stopSimulation } from './actions.js';

export function setupEventListeners(panel) {
  // Stop simulation button
  const stopBtn = document.getElementById('stop-simulation-btn');
  if (stopBtn) {
    stopBtn.addEventListener('click', () => stopSimulation(panel));
  }

  // Tab switching
  document.querySelectorAll('.tab-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      const tab = e.target.dataset.tab;
      switchTab(tab);
    });
  });

  // Run simulation button
  const runBtn = document.getElementById('run-simulation-btn');
  if (runBtn) {
    runBtn.addEventListener('click', () => panel.runSimulation());
  }

  // Export results button
  const exportBtn = document.getElementById('export-results-btn');
  if (exportBtn) {
    exportBtn.addEventListener('click', () => panel.exportResults());
  }

  AppEvents.on('state:updated', (state) => {
    panel.syncSimulationSettings(state);
  });

  function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-btn').forEach((btn) => {
      btn.classList.toggle('active', btn.dataset.tab === tabName);
    });

    // Update tab content
    document.querySelectorAll('.tab-content').forEach((content) => {
      content.classList.toggle('active', content.id === `${tabName}-tab`);
    });

    AppEvents.emit('ui:tab-changed', { tab: tabName });
  }
}
