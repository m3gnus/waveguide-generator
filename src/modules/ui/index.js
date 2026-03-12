import { AppEvents } from '../../events.js';
import { ParamPanel } from '../../ui/paramPanel.js';
import {
  getLiveUpdateEnabled,
  getDisplayMode,
  openSettingsModal
} from '../../ui/settings/modal.js';
import {
  loadViewerSettings,
  applyViewerSettingsToControls,
  setInvertWheelZoom,
  getCurrentViewerSettings
} from '../../ui/settings/viewerSettings.js';

const UI_MODULE_ID = 'ui';
const UI_IMPORT_STAGE = 'import';
const UI_TASK_STAGE = 'task';

const UI_KINDS = Object.freeze({
  APP: 'app',
  SIMULATION_PANEL: 'simulation-panel'
});

function isObject(value) {
  return value !== null && typeof value === 'object';
}

function createUiImportEnvelope(kind, payload) {
  return Object.freeze({
    module: UI_MODULE_ID,
    stage: UI_IMPORT_STAGE,
    kind,
    ...payload
  });
}

function assertUiImportEnvelope(input, expectedKind = null) {
  if (
    !isObject(input) ||
    input.module !== UI_MODULE_ID ||
    input.stage !== UI_IMPORT_STAGE
  ) {
    throw new Error('UI module task requires input created by UiModule import helpers.');
  }
  if (expectedKind && input.kind !== expectedKind) {
    throw new Error(`UI module task expected "${expectedKind}" input but received "${input.kind}".`);
  }
}

function assertUiTaskEnvelope(result, expectedKind = null) {
  if (
    !isObject(result) ||
    result.module !== UI_MODULE_ID ||
    result.stage !== UI_TASK_STAGE ||
    !isObject(result.coordinator)
  ) {
    throw new Error('UI module output requires a result from UiModule.task().');
  }
  if (expectedKind && result.kind !== expectedKind) {
    throw new Error(`UI module output expected "${expectedKind}" result but received "${result.kind}".`);
  }
}

function validateSimulationMeshPayload(meshData) {
  if (!meshData || !Array.isArray(meshData.vertices) || meshData.vertices.length === 0) {
    throw new Error('No horn geometry available. Please generate a horn first.');
  }
  if (!Array.isArray(meshData.surfaceTags) || meshData.surfaceTags.length !== meshData.indices.length / 3) {
    throw new Error('Mesh payload is missing valid surface tags.');
  }
  if (typeof meshData.format !== 'string' || !meshData.format.trim()) {
    throw new Error('Mesh payload is missing a format value.');
  }
  if (!meshData.boundaryConditions || typeof meshData.boundaryConditions !== 'object') {
    throw new Error('Mesh payload is missing boundary conditions.');
  }
  return meshData;
}

function buildAppCoordinator(input) {
  const app = input.app;
  const loadSimulationPanel = input.loadSimulationPanel || (() => import('../../ui/simulation/SimulationPanel.js'));
  const feedback = input.feedback || {};
  const fileOps = input.fileOps || {};
  let simulationPanelInitPromise = null;
  let eventsBound = false;

  const onStateUpdated = (state) => {
    app.onStateUpdate(state);
    fileOps.markParametersChanged?.();
  };
  const onMeshRequested = () => {
    app.provideMeshForSimulation();
  };
  const onTabChanged = () => {
    app.schedulePanelAutoSize();
  };

  return Object.freeze({
    bind() {
      if (eventsBound) {
        return;
      }
      AppEvents.on('state:updated', onStateUpdated);
      AppEvents.on('simulation:mesh-requested', onMeshRequested);
      AppEvents.on('ui:tab-changed', onTabChanged);
      eventsBound = true;
    },

    async ensureSimulationPanel() {
      if (app.simulationPanel) {
        return app.simulationPanel;
      }
      if (simulationPanelInitPromise) {
        return simulationPanelInitPromise;
      }
      if (typeof loadSimulationPanel !== 'function') {
        throw new Error('UI app coordinator requires a simulation panel loader.');
      }

      simulationPanelInitPromise = Promise.resolve(loadSimulationPanel()).then(({ SimulationPanel }) => {
        if (!app.simulationPanel) {
          app.simulationPanel = new SimulationPanel({ app });
          if (!app.simulationPanel.app) {
            app.simulationPanel.app = app;
          }
        }
        return app.simulationPanel;
      });

      return simulationPanelInitPromise;
    },

    publishSimulationMesh(payload) {
      AppEvents.emit('simulation:mesh-ready', payload);
      return payload;
    },

    publishSimulationMeshError(message) {
      AppEvents.emit('simulation:mesh-error', { message });
      return null;
    },

    showError(message, duration) {
      return feedback.showError?.(message, duration);
    },

    showMessage(message, options) {
      return feedback.showMessage?.(message, options);
    },

    showSuccess(message, duration) {
      return feedback.showSuccess?.(message, duration);
    },

    showCommandSuggestion(options = {}) {
      return feedback.showCommandSuggestion?.(options);
    },

    deriveExportFieldsFromFileName(fileName, options = {}) {
      return fileOps.deriveExportFieldsFromFileName?.(fileName, options);
    },

    setExportFields(fields = {}, doc) {
      return fileOps.setExportFields?.(fields, doc);
    },

    resetParameterChangeTracking(options = {}) {
      return fileOps.resetParameterChangeTracking?.(options);
    },

    chooseOutputFolder() {
      return fileOps.selectOutputFolder?.();
    },

    readLiveUpdateSetting() {
      return getLiveUpdateEnabled();
    },

    readDisplayModeSetting() {
      return getDisplayMode();
    },

    openSettings(options = {}) {
      return openSettingsModal(options);
    },

    isFolderSelectionSupported(targetWindow) {
      return typeof targetWindow?.showDirectoryPicker === 'function';
    },

    loadViewerSettings() {
      return loadViewerSettings();
    },

    applyViewerSettingsToControls(controls, settings) {
      return applyViewerSettingsToControls(controls, settings);
    },

    configureWheelZoomInversion(domElement, invertEnabled) {
      return setInvertWheelZoom(domElement, invertEnabled);
    },

    getViewerSettings() {
      return getCurrentViewerSettings();
    },

    createParamPanel(containerId = 'param-container') {
      return new ParamPanel(containerId);
    },

    dispose() {
      if (!eventsBound) {
        return;
      }
      AppEvents.off('state:updated', onStateUpdated);
      AppEvents.off('simulation:mesh-requested', onMeshRequested);
      AppEvents.off('ui:tab-changed', onTabChanged);
      eventsBound = false;
    }
  });
}

function buildSimulationPanelCoordinator(input) {
  const panel = input.panel;
  let eventsBound = false;
  let pendingMeshResolve = null;
  let pendingMeshReject = null;
  let pendingMeshTimeout = null;

  function clearPendingMeshRequest() {
    if (pendingMeshTimeout !== null) {
      clearTimeout(pendingMeshTimeout);
      pendingMeshTimeout = null;
    }
    pendingMeshResolve = null;
    pendingMeshReject = null;
  }

  function rejectPendingMeshRequest(error) {
    if (!pendingMeshReject) {
      clearPendingMeshRequest();
      return;
    }
    const reject = pendingMeshReject;
    clearPendingMeshRequest();
    reject(error instanceof Error ? error : new Error(String(error)));
  }

  const onStateUpdated = (state) => {
    panel.syncSimulationSettings(state);
  };
  const onMeshReady = (meshData) => {
    if (!pendingMeshResolve) {
      return;
    }
    try {
      const resolve = pendingMeshResolve;
      const validated = validateSimulationMeshPayload(meshData);
      clearPendingMeshRequest();
      resolve(validated);
    } catch (error) {
      rejectPendingMeshRequest(error);
    }
  };
  const onMeshError = (errorData) => {
    if (!pendingMeshReject) {
      return;
    }
    const message = errorData?.message || 'Simulation mesh generation failed.';
    rejectPendingMeshRequest(new Error(message));
  };
  const onFolderWorkspaceChanged = () => {
    if (typeof panel.refreshJobFeed !== 'function') {
      return;
    }
    Promise.resolve(panel.refreshJobFeed()).catch((error) => {
      console.warn('Failed to refresh simulation jobs after workspace change:', error);
    });
  };

  return Object.freeze({
    bind() {
      if (eventsBound) {
        return;
      }
      AppEvents.on('state:updated', onStateUpdated);
      AppEvents.on('simulation:mesh-ready', onMeshReady);
      AppEvents.on('simulation:mesh-error', onMeshError);
      AppEvents.on('ui:folder-workspace-changed', onFolderWorkspaceChanged);
      eventsBound = true;
    },

    prepareMesh(timeoutMs = 10000) {
      rejectPendingMeshRequest(new Error('Simulation mesh request was interrupted by a newer request.'));

      return new Promise((resolve, reject) => {
        pendingMeshResolve = resolve;
        pendingMeshReject = reject;
        pendingMeshTimeout = setTimeout(() => {
          rejectPendingMeshRequest(new Error('Timeout waiting for mesh data'));
        }, timeoutMs);

        AppEvents.emit('simulation:mesh-requested');
      });
    },

    emitTabChanged(tabName) {
      AppEvents.emit('ui:tab-changed', { tab: tabName });
      return tabName;
    },

    dispose() {
      rejectPendingMeshRequest(new Error('Simulation panel disposed while waiting for mesh data.'));
      if (!eventsBound) {
        return;
      }
      AppEvents.off('state:updated', onStateUpdated);
      AppEvents.off('simulation:mesh-ready', onMeshReady);
      AppEvents.off('simulation:mesh-error', onMeshError);
      AppEvents.off('ui:folder-workspace-changed', onFolderWorkspaceChanged);
      eventsBound = false;
    }
  });
}

export function importAppUi(app, options = {}) {
  return createUiImportEnvelope(UI_KINDS.APP, {
    app,
    loadSimulationPanel: options.loadSimulationPanel,
    feedback: options.feedback,
    fileOps: options.fileOps
  });
}

export function importSimulationPanelUi(panel) {
  return createUiImportEnvelope(UI_KINDS.SIMULATION_PANEL, { panel });
}

export function runUiTask(input) {
  assertUiImportEnvelope(input);

  if (input.kind === UI_KINDS.APP) {
    return Object.freeze({
      module: UI_MODULE_ID,
      stage: UI_TASK_STAGE,
      kind: UI_KINDS.APP,
      coordinator: buildAppCoordinator(input)
    });
  }

  if (input.kind === UI_KINDS.SIMULATION_PANEL) {
    return Object.freeze({
      module: UI_MODULE_ID,
      stage: UI_TASK_STAGE,
      kind: UI_KINDS.SIMULATION_PANEL,
      coordinator: buildSimulationPanelCoordinator(input)
    });
  }

  throw new Error(`Unsupported UI module kind "${input.kind}".`);
}

export function getAppUiOutput(result) {
  assertUiTaskEnvelope(result, UI_KINDS.APP);
  return result.coordinator;
}

export function getSimulationPanelUiOutput(result) {
  assertUiTaskEnvelope(result, UI_KINDS.SIMULATION_PANEL);
  return result.coordinator;
}

export const UiModule = Object.freeze({
  id: UI_MODULE_ID,
  importApp: importAppUi,
  importSimulationPanel: importSimulationPanelUi,
  task: runUiTask,
  output: Object.freeze({
    app: getAppUiOutput,
    simulationPanel: getSimulationPanelUiOutput
  })
});
