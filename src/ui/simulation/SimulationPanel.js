// @ts-check

/**
 * Simulation Panel UI Module
 *
 * Handles the BEM simulation interface including:
 * - Connection status monitoring
 * - Simulation controls
 * - Progress tracking
 * - Results display coordination
 */
import { showMessage } from '../feedback.js';
import { setupEventListeners } from './events.js';
import { setupMeshListener, prepareMeshForSimulation } from './mesh.js';
import { setupSmoothingListener, setupKeyboardShortcuts } from './smoothing.js';
import { setupSimulationParamBindings, syncSimulationSettings } from './settings.js';
import { checkSolverConnection } from './connection.js';
import { pollSimulationStatus } from './polling.js';
import { runSimulation, runMockSimulation, renderJobList } from './jobActions.js';
import {
  createSimulationPanelRuntime,
  restoreSimulationPanelRuntime,
  disposeSimulationPanelRuntime
} from './controller.js';
import { subscribeAppFolderWorkspace } from '../../modules/ui/useCases.js';
import { displayResults } from './results.js';
import {
  exportResults,
  exportAsMatplotlibPNG,
  exportAsCSV,
  exportAsJSON,
  exportAsText
} from './exports.js';
import { openViewResultsModal } from './viewResults.js';

/**
 * @typedef {Object} SimulationBinding
 * @property {string} id
 * @property {string} key
 * @property {(value: string) => number} parse
 */

export class SimulationPanel {
  constructor() {
    this.runtime = createSimulationPanelRuntime(this);
    this.controller = this.runtime.controller;
    this.uiCoordinator = this.runtime.uiCoordinator;
    this.folderWorkspaceUnsubscribe = null;

    this.setupEventListeners();
    this.setupMeshListener();
    this.setupSmoothingListener();
    this.setupKeyboardShortcuts();
    this.setupSimulationParamBindings();
    this.checkSolverConnection();
    this.restoreJobs();
    this.bindFolderWorkspaceRefresh();
  }

  async restoreJobs() {
    return restoreSimulationPanelRuntime(this.runtime, {
      onJobsUpdated: () => {
        renderJobList(this);
      },
      onStartPolling: () => {
        this.pollSimulationStatus();
      },
      onRecoverFromManifests: () => {
        showMessage('Recovered folder task history from manifests.', { type: 'warning', duration: 2800 });
      }
    });
  }

  bindFolderWorkspaceRefresh() {
    let sawInitialSnapshot = false;
    this.folderWorkspaceUnsubscribe = subscribeAppFolderWorkspace(() => {
      if (!sawInitialSnapshot) {
        sawInitialSnapshot = true;
        return;
      }
      this.refreshJobFeed();
    });
  }

  async refreshJobFeed() {
    if (this.pollTimer) {
      clearTimeout(this.pollTimer);
      this.pollTimer = null;
      this.pollInterval = null;
      this.isPolling = false;
    }
    return this.restoreJobs();
  }

  setupEventListeners() {
    return setupEventListeners(this);
  }

  setupMeshListener() {
    return setupMeshListener(this);
  }

  setupSmoothingListener() {
    return setupSmoothingListener(this);
  }

  setupKeyboardShortcuts() {
    return setupKeyboardShortcuts(this);
  }

  setupSimulationParamBindings() {
    return setupSimulationParamBindings(this);
  }

  syncSimulationSettings(state) {
    return syncSimulationSettings(this, state);
  }

  checkSolverConnection() {
    return checkSolverConnection(this);
  }

  runSimulation() {
    return runSimulation(this);
  }

  runMockSimulation(config) {
    return runMockSimulation(config);
  }

  pollSimulationStatus() {
    return pollSimulationStatus(this);
  }

  prepareMeshForSimulation() {
    return prepareMeshForSimulation(this);
  }

  emitTabChanged(tabName) {
    return this.uiCoordinator.emitTabChanged(tabName);
  }

  displayResults(results = null) {
    return displayResults(this, results);
  }

  openViewResults() {
    return openViewResultsModal(this);
  }

  exportResults() {
    return exportResults(this);
  }

  exportAsMatplotlibPNG() {
    return exportAsMatplotlibPNG(this);
  }

  exportAsCSV() {
    return exportAsCSV(this);
  }

  exportAsJSON() {
    return exportAsJSON(this);
  }

  exportAsText() {
    return exportAsText(this);
  }

  /**
   * Release all timers and EventBus listeners registered by this panel.
   * Call when the panel is being unmounted or replaced.
   */
  dispose() {
    if (typeof this.folderWorkspaceUnsubscribe === 'function') {
      this.folderWorkspaceUnsubscribe();
      this.folderWorkspaceUnsubscribe = null;
    }
    disposeSimulationPanelRuntime(this.runtime);
  }
}
