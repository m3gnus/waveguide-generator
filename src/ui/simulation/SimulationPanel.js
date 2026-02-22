/**
 * Simulation Panel UI Module
 *
 * Handles the BEM simulation interface including:
 * - Connection status monitoring
 * - Simulation controls
 * - Progress tracking
 * - Results display coordination
 */

import { BemSolver } from '../../solver/index.js';
import { setupEventListeners } from './events.js';
import { setupMeshListener, prepareMeshForSimulation } from './mesh.js';
import { setupSmoothingListener, setupKeyboardShortcuts } from './smoothing.js';
import { setupSimulationParamBindings, syncSimulationSettings } from './settings.js';
import { checkSolverConnection } from './connection.js';
import { runSimulation, pollSimulationStatus, runMockSimulation, renderJobList } from './actions.js';
import {
  createJobTracker,
  loadLocalIndex,
  mergeJobs,
  setJobsFromEntries,
  persistPanelJobs
} from './jobTracker.js';
import { displayResults, renderBemResults, renderValidationReport } from './results.js';
import {
  exportResults,
  exportAsMatplotlibPNG,
  exportAsCSV,
  exportAsJSON,
  exportAsText
} from './exports.js';
import { openViewResultsModal } from './viewResults.js';

export class SimulationPanel {
  constructor() {
    this.solver = new BemSolver();
    this.currentJobId = null; // Backward-compatible alias for activeJobId
    this.pollInterval = null; // Backward-compatible alias for pollTimer
    this.connectionPollTimer = null;
    this.pendingMeshResolve = null;
    this.lastResults = null;
    this.jobs = new Map();
    this.resultCache = new Map();
    this.activeJobId = null;
    this.pollTimer = null;
    this.pollDelayMs = 1000;
    this.pollBackoffMs = 1000;
    this.isPolling = false;
    this.stageStatusActive = false;
    this.completedStatusMessage = null;
    this.simulationStartedAtMs = null;
    this.lastSimulationDurationMs = null;
    this.currentSmoothing = 'none';
    this.simulationParamBindings = [
      { id: 'freq-start', key: 'freqStart', parse: (value) => parseFloat(value) },
      { id: 'freq-end', key: 'freqEnd', parse: (value) => parseFloat(value) },
      { id: 'freq-steps', key: 'numFreqs', parse: (value) => parseInt(value, 10) }
    ];

    this.setupEventListeners();
    this.setupMeshListener();
    this.setupSmoothingListener();
    this.setupKeyboardShortcuts();
    this.setupSimulationParamBindings();
    this.checkSolverConnection();
    this.restoreJobs();
  }

  async restoreJobs() {
    const tracker = createJobTracker();
    this.jobs = tracker.jobs;
    this.resultCache = tracker.resultCache;
    this.activeJobId = tracker.activeJobId;
    this.pollTimer = tracker.pollTimer;
    this.pollDelayMs = tracker.pollDelayMs;
    this.pollBackoffMs = tracker.pollBackoffMs;
    this.isPolling = tracker.isPolling;

    const local = loadLocalIndex();
    setJobsFromEntries(this, local);
    renderJobList(this);

    try {
      const remote = await this.solver.listJobs({ limit: 200, offset: 0 });
      const merged = mergeJobs(local, remote.items || []);
      setJobsFromEntries(this, merged);
      persistPanelJobs(this);
      renderJobList(this);
      if (this.activeJobId || merged.some((job) => job.status === 'queued' || job.status === 'running')) {
        this.pollSimulationStatus();
      }
    } catch (_error) {
      persistPanelJobs(this);
      renderJobList(this);
    }
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

  displayResults(results = null) {
    return displayResults(this, results);
  }

  renderBemResults(results) {
    return renderBemResults(this, results);
  }

  renderValidationReport(report) {
    return renderValidationReport(report);
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
}
