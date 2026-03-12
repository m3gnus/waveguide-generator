import { GlobalState } from '../state.js';
import { UiModule } from '../modules/ui/index.js';

import { initializeLogging } from './logging.js';
import { setupScene, onResize, renderModel, focusOnModel, zoom, toggleCamera } from './scene.js';
import { setupEventListeners } from './events.js';
import { setupPanelSizing, schedulePanelAutoSize } from './panelSizing.js';
import { handleFileUpload } from './configImport.js';
import { provideMeshForSimulation } from './mesh.js';
import { checkForUpdates } from './updates.js';
import {
  exportMwgConfigFromApp,
  exportProfileCsvFromApp,
  exportStlFromApp,
  registerBackendDiagnosticTool
} from './exports.js';

export class App {
  constructor() {
    this.container = document.getElementById('canvas-container');
    this.stats = document.getElementById('stats');
    this.viewportMeshStats = null;
    this.simulationMeshStats = null;
    this.activeMeshStatsSource = 'viewport';
    this.currentState = null;
    this.renderRequested = false;
    this.simulationPanel = null;
    this.uiCoordinator = UiModule.output.app(
      UiModule.task(
        UiModule.importApp(this)
      )
    );

    // Initialize change logging
    this.initializeLogging();

    // Init UI
    this.paramPanel = this.uiCoordinator.createParamPanel('param-container');
    this.uiCoordinator.bind();
    this.ensureSimulationPanel().catch((error) => {
      console.error('Failed to initialize simulation panel:', error);
    });
    registerBackendDiagnosticTool();

    this.setupScene();
    this.setupEventListeners();
    this.setupPanelSizing();

    // Initial render — viewport always uses formula-based mesh
    this.onStateUpdate(GlobalState.get());
  }

  async ensureSimulationPanel() {
    return this.uiCoordinator.ensureSimulationPanel();
  }

  initializeLogging() {
    return initializeLogging();
  }

  setupScene() {
    return setupScene(this);
  }

  onResize() {
    return onResize(this);
  }

  setupEventListeners() {
    return setupEventListeners(this);
  }

  setupPanelSizing() {
    return setupPanelSizing(this);
  }

  schedulePanelAutoSize() {
    return schedulePanelAutoSize(this);
  }

  onStateUpdate(state) {
    this.currentState = state;
    // 1. Rebuild Param UI
    this.paramPanel.createFullPanel();
    this.schedulePanelAutoSize();

    // 2. Render
    if (this.uiCoordinator.readLiveUpdateSetting()) {
      this.requestRender();
    }
  }

  requestRender() {
    if (!this.renderRequested) {
      this.renderRequested = true;
      requestAnimationFrame(() => {
        this.renderModel();
        this.renderRequested = false;
      });
    }
  }


  handleFileUpload(event) {
    return handleFileUpload(event, this.uiCoordinator);
  }

  renderModel() {
    return renderModel(this);
  }

  focusOnModel() {
    return focusOnModel(this);
  }

  zoom(factor) {
    return zoom(this, factor);
  }

  toggleCamera() {
    return toggleCamera(this);
  }

  async exportSTL() {
    return exportStlFromApp();
  }

  async exportMWGConfig() {
    return exportMwgConfigFromApp();
  }

  async exportProfileCSV() {
    const vertices = this.hornMesh?.geometry?.attributes?.position?.array;
    return exportProfileCsvFromApp(vertices);
  }

  async provideMeshForSimulation() {
    return provideMeshForSimulation(this);
  }

  publishSimulationMesh(payload) {
    return this.uiCoordinator.publishSimulationMesh(payload);
  }

  publishSimulationMeshError(message) {
    return this.uiCoordinator.publishSimulationMeshError(message);
  }

  setViewportMeshStats(meshStats) {
    this.viewportMeshStats = normalizeMeshStats(meshStats);
    this.activeMeshStatsSource = 'viewport';
    this.renderMeshStats();
    return this.viewportMeshStats;
  }

  setSimulationMeshStats(meshStats) {
    const normalized = normalizeMeshStats(meshStats);
    if (!normalized) {
      return null;
    }
    this.simulationMeshStats = normalized;
    this.activeMeshStatsSource = 'simulation';
    this.renderMeshStats();
    return this.simulationMeshStats;
  }

  renderMeshStats() {
    if (!this.stats) {
      return;
    }
    const activeStats = this.activeMeshStatsSource === 'simulation'
      ? this.simulationMeshStats
      : this.viewportMeshStats;
    if (!activeStats) {
      return;
    }
    const label = this.activeMeshStatsSource === 'simulation'
      ? 'Simulation'
      : 'Viewport';
    this.stats.innerText = `${label}: ${activeStats.vertexCount} vertices | ${activeStats.triangleCount} triangles`;
  }

  async checkForUpdates(buttonEl) {
    return checkForUpdates(buttonEl, this.uiCoordinator);
  }
}

function normalizeMeshStats(meshStats = null) {
  const vertexCount = Number(meshStats?.vertexCount ?? meshStats?.vertex_count);
  const triangleCount = Number(meshStats?.triangleCount ?? meshStats?.triangle_count);
  if (!Number.isFinite(vertexCount) || !Number.isFinite(triangleCount)) {
    return null;
  }
  return {
    vertexCount: Math.max(0, Math.floor(vertexCount)),
    triangleCount: Math.max(0, Math.floor(triangleCount))
  };
}
