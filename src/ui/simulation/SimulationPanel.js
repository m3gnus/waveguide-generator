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
import { runSimulation, pollSimulationStatus, runMockSimulation } from './actions.js';
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
    this.currentJobId = null;
    this.pollInterval = null;
    this.connectionPollTimer = null;
    this.pendingMeshResolve = null;
    this.lastResults = null;
    this.stageStatusActive = false;
    this.completedStatusMessage = null;
    this.simulationStartedAtMs = null;
    this.lastSimulationDurationMs = null;
    this.currentSmoothing = 'none';
    this.simulationParamBindings = [
      { id: 'freq-start', key: 'abecF1', parse: (value) => parseFloat(value) },
      { id: 'freq-end', key: 'abecF2', parse: (value) => parseFloat(value) },
      { id: 'freq-steps', key: 'abecNumFreq', parse: (value) => parseInt(value, 10) },
      { id: 'sim-type', key: 'abecSimType', parse: (value) => parseInt(value, 10) },
      { id: 'circsym-profile', key: 'abecSimProfile', parse: (value) => parseInt(value, 10) }
    ];

    this.setupEventListeners();
    this.setupMeshListener();
    this.setupSmoothingListener();
    this.setupKeyboardShortcuts();
    this.setupSimulationParamBindings();
    this.checkSolverConnection();
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
