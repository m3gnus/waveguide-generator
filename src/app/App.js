import { GlobalState } from '../state.js';
import { ParamPanel } from '../ui/paramPanel.js';
import { AppEvents } from '../events.js';

import { initializeLogging } from './logging.js';
import { prepareParamsForMesh } from './params.js';
import { setupScene, onResize, renderModel, focusOnModel, zoom, toggleCamera } from './scene.js';
import { setupEventListeners } from './events.js';
import { setupPanelSizing, schedulePanelAutoSize } from './panelSizing.js';
import { handleFileUpload } from './configImport.js';
import { provideMeshForSimulation } from './mesh.js';
import { checkForUpdates } from './updates.js';
import { markParametersChanged } from '../ui/fileOps.js';
import { isDevRuntime } from '../config/runtimeMode.js';
import { getLiveUpdateEnabled } from '../ui/settings/modal.js';

let simulationPanelModulePromise = null;
function loadSimulationPanelModule() {
  if (!simulationPanelModulePromise) {
    simulationPanelModulePromise = import('../ui/simulationPanel.js');
  }
  return simulationPanelModulePromise;
}

let appExportsModulePromise = null;
function loadAppExportsModule() {
  if (!appExportsModulePromise) {
    appExportsModulePromise = import('./exports.js');
  }
  return appExportsModulePromise;
}

export class App {
  constructor() {
    this.container = document.getElementById('canvas-container');
    this.stats = document.getElementById('stats');
    this.renderRequested = false;
    this.simulationPanel = null;
    this._simulationPanelInitPromise = null;

    // Initialize change logging
    this.initializeLogging();

    // Init UI
    this.paramPanel = new ParamPanel('param-container');
    this.ensureSimulationPanel().catch((error) => {
      console.error('Failed to initialize simulation panel:', error);
    });

    this.setupScene();
    this.setupEventListeners();
    this.setupPanelSizing();

    // Initial render — viewport always uses formula-based mesh
    this.onStateUpdate(GlobalState.get());

    // Subscribe to state updates
    AppEvents.on('state:updated', (state) => {
      this.onStateUpdate(state);
      markParametersChanged();
    });

    // Subscribe to simulation events - canonical mesh payload generation for simulation
    AppEvents.on('simulation:mesh-requested', () => {
      this.provideMeshForSimulation();
    });

    AppEvents.on('ui:tab-changed', () => {
      this.schedulePanelAutoSize();
    });
  }

  async ensureSimulationPanel() {
    if (this.simulationPanel) {
      return this.simulationPanel;
    }
    if (this._simulationPanelInitPromise) {
      return this._simulationPanelInitPromise;
    }

    this._simulationPanelInitPromise = loadSimulationPanelModule().then(({ SimulationPanel }) => {
      if (!this.simulationPanel) {
        this.simulationPanel = new SimulationPanel();
        this.simulationPanel.app = this;
      }
      if (typeof window !== 'undefined' && isDevRuntime()) {
        window.__waveguideApp = this;
      }
      return this.simulationPanel;
    });

    return this._simulationPanelInitPromise;
  }

  initializeLogging() {
    return initializeLogging();
  }

  prepareParamsForMesh(options) {
    return prepareParamsForMesh(options);
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
    if (getLiveUpdateEnabled()) {
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
    const { exportSTL } = await loadAppExportsModule();
    return exportSTL(this);
  }

  async exportMWGConfig() {
    const { exportMWGConfig } = await loadAppExportsModule();
    return exportMWGConfig();
  }

  async exportProfileCSV() {
    const { exportProfileCSV } = await loadAppExportsModule();
    return exportProfileCSV(this);
  }

  async provideMeshForSimulation() {
    return provideMeshForSimulation(this);
  }

  async checkForUpdates() {
    return checkForUpdates();
  }
}
