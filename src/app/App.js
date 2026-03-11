import { GlobalState } from '../state.js';
import { ParamPanel } from '../ui/paramPanel.js';
import { UiModule } from '../modules/ui/index.js';

import { initializeLogging } from './logging.js';
import { setupScene, onResize, renderModel, focusOnModel, zoom, toggleCamera } from './scene.js';
import { setupEventListeners } from './events.js';
import { setupPanelSizing, schedulePanelAutoSize } from './panelSizing.js';
import { handleFileUpload } from './configImport.js';
import { provideMeshForSimulation } from './mesh.js';
import { checkForUpdates } from './updates.js';
import { readLiveUpdateSetting } from '../modules/ui/useCases.js';

let simulationPanelModulePromise = null;
function loadSimulationPanelModule() {
  if (!simulationPanelModulePromise) {
    simulationPanelModulePromise = import('../ui/simulationPanel.js');
  }
  return simulationPanelModulePromise;
}

let exportUseCasesPromise = null;
function loadExportUseCases() {
  if (!exportUseCasesPromise) {
    exportUseCasesPromise = import('../modules/export/useCases.js');
  }
  return exportUseCasesPromise;
}

export class App {
  constructor() {
    this.container = document.getElementById('canvas-container');
    this.stats = document.getElementById('stats');
    this.renderRequested = false;
    this.simulationPanel = null;
    this.uiCoordinator = UiModule.output.app(
      UiModule.task(
        UiModule.importApp(this, {
          loadSimulationPanel: loadSimulationPanelModule
        })
      )
    );

    // Initialize change logging
    this.initializeLogging();

    // Init UI
    this.paramPanel = new ParamPanel('param-container');
    this.uiCoordinator.bind();
    this.ensureSimulationPanel().catch((error) => {
      console.error('Failed to initialize simulation panel:', error);
    });

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
    // 1. Rebuild Param UI
    this.paramPanel.createFullPanel();
    this.schedulePanelAutoSize();

    // 2. Render
    if (readLiveUpdateSetting()) {
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
    return handleFileUpload(event);
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
    const { exportSTL } = await loadExportUseCases();
    return exportSTL();
  }

  async exportMWGConfig() {
    const { exportMWGConfig } = await loadExportUseCases();
    return exportMWGConfig();
  }

  async exportProfileCSV() {
    const { exportProfileCSV } = await loadExportUseCases();
    const vertices = this.hornMesh?.geometry?.attributes?.position?.array;
    return exportProfileCSV(vertices);
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

  async checkForUpdates(buttonEl) {
    return checkForUpdates(buttonEl);
  }
}
