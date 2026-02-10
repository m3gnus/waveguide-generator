import { GlobalState } from '../state.js';
import { ParamPanel } from '../ui/paramPanel.js';
import { SimulationPanel } from '../ui/simulationPanel.js';
import { AppEvents } from '../events.js';

import { initializeLogging } from './logging.js';
import { prepareParamsForMesh } from './params.js';
import { setupScene, onResize, renderModel, focusOnModel, zoom, toggleCamera } from './scene.js';
import { setupEventListeners } from './events.js';
import { setupPanelSizing, schedulePanelAutoSize } from './panelSizing.js';
import { handleFileUpload } from './configImport.js';
import { exportSTL, exportMWGConfig, exportProfileCSV, exportABECProject } from './exports.js';
import { provideMeshForSimulation } from './mesh.js';
import { checkForUpdates } from './updates.js';
import { markParametersChanged } from '../ui/fileOps.js';

export class App {
  constructor() {
    this.container = document.getElementById('canvas-container');
    this.stats = document.getElementById('stats');
    this.renderRequested = false;

    // Initialize change logging
    this.initializeLogging();

    // Init UI
    this.paramPanel = new ParamPanel('param-container');
    this.simulationPanel = new SimulationPanel();

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
    if (document.getElementById('live-update')?.checked !== false) {
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

  toggleModelType() {
    // Legacy support function called by legacy listeners if any.
    this.requestRender();
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

  exportSTL() {
    return exportSTL(this);
  }

  exportMWGConfig() {
    return exportMWGConfig();
  }

  exportProfileCSV() {
    return exportProfileCSV(this);
  }

  exportABECProject() {
    return exportABECProject(this);
  }

  async provideMeshForSimulation() {
    return provideMeshForSimulation(this);
  }

  async checkForUpdates() {
    return checkForUpdates();
  }
}
