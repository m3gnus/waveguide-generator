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
import {
  showCommandSuggestion,
  showError,
  showMessage,
  showSuccess
} from '../../ui/feedback.js';
import {
  deriveExportFieldsFromFileName,
  setExportFields,
  resetParameterChangeTracking,
  selectOutputFolder
} from '../../ui/fileOps.js';

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

export function isFolderSelectionSupported(targetWindow = window) {
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

export function showUiError(message, duration) {
  return showError(message, duration);
}

export function showUiMessage(message, options) {
  return showMessage(message, options);
}

export function showUiSuccess(message, duration) {
  return showSuccess(message, duration);
}

export function showUiCommandSuggestion(options = {}) {
  return showCommandSuggestion(options);
}

export function deriveExportFieldsFromImportedFileName(fileName, options = {}) {
  return deriveExportFieldsFromFileName(fileName, options);
}

export function setAppExportFields(fields = {}, doc = document) {
  return setExportFields(fields, doc);
}

export function resetAppParameterChangeTracking(options = {}) {
  return resetParameterChangeTracking(options);
}

export async function chooseOutputFolder() {
  return selectOutputFolder();
}
