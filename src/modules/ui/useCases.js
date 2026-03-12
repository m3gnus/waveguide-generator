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
import { ParamPanel } from '../../ui/paramPanel.js';

let simulationPanelModulePromise = null;

export function readLiveUpdateSetting() {
  return getLiveUpdateEnabled();
}

export function readDisplayModeSetting() {
  return getDisplayMode();
}

export function openAppSettings(options = {}) {
  return openSettingsModal(options);
}

export function isFolderSelectionSupported(targetWindow) {
  return typeof targetWindow?.showDirectoryPicker === 'function';
}

export function loadAppViewerSettings() {
  return loadViewerSettings();
}

export function applyAppViewerSettingsToControls(controls, settings) {
  return applyViewerSettingsToControls(controls, settings);
}

export function configureWheelZoomInversion(domElement, invertEnabled) {
  return setInvertWheelZoom(domElement, invertEnabled);
}

export function getAppViewerSettings() {
  return getCurrentViewerSettings();
}

export function createAppParamPanel(containerId = 'param-container') {
  return new ParamPanel(containerId);
}

export function loadSimulationPanelModule() {
  if (!simulationPanelModulePromise) {
    simulationPanelModulePromise = import('../../ui/simulation/SimulationPanel.js');
  }
  return simulationPanelModulePromise;
}
