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
import { exportSTL, exportMWGConfig, exportProfileCSV, exportGmshGeo, exportMSH, exportABECProject } from './exports.js';
import { provideMeshForSimulation } from './mesh.js';
import { initCADWorker, isCADReady, downloadSTEP } from '../cad/index.js';

export class App {
  constructor() {
    this.container = document.getElementById('canvas-container');
    this.stats = document.getElementById('stats');
    this.renderRequested = false;
    this.useCAD = false; // CAD export available (STEP, MSH, ABEC)

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
    });

    // Subscribe to simulation events
    AppEvents.on('simulation:mesh-requested', () => {
      this.provideMeshForSimulation();
    });

    AppEvents.on('ui:tab-changed', () => {
      this.schedulePanelAutoSize();
    });

    // Initialize CAD worker in background
    this.initCAD();
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

  exportGmshGeo() {
    return exportGmshGeo(this);
  }

  exportMSH() {
    return exportMSH(this);
  }

  exportABECProject() {
    return exportABECProject(this);
  }

  provideMeshForSimulation() {
    return provideMeshForSimulation(this);
  }

  async initCAD() {
    try {
      await initCADWorker((stage, message) => {
        this.stats.innerText = message;
      });
      this.useCAD = true;
      console.log('[App] CAD system ready — STEP/MSH/ABEC export uses parametric geometry');
      // Enable STEP export button
      const stepBtn = document.getElementById('export-step-btn');
      if (stepBtn) stepBtn.disabled = false;
    } catch (err) {
      console.warn('[App] CAD system unavailable, using legacy mesh:', err.message);
      this.useCAD = false;
    }
  }

  async exportSTEP() {
    if (!isCADReady()) {
      alert('CAD system not ready. Please wait for OpenCascade to load.');
      return;
    }
    const params = this.prepareParamsForMesh({ forceFullQuadrants: true });
    const prefix = document.getElementById('export-prefix')?.value || 'horn';
    const counter = document.getElementById('export-counter')?.value || '1';
    const filename = `${prefix}_${counter.padStart(3, '0')}.step`;
    this.stats.innerText = 'Exporting STEP...';
    try {
      await downloadSTEP(params, filename, {
        numStations: params.lengthSegments || 40,
        numAngles: params.angularSegments || 80,
        includeSource: true,
        wallThickness: Number(params.wallThickness) || 0
      });
      this.stats.innerText = `Exported: ${filename}`;
    } catch (err) {
      this.stats.innerText = `STEP export failed: ${err.message}`;
      console.error('[App] STEP export error:', err);
    }
  }
}
