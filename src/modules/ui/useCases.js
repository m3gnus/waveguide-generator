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
import { supportsFolderSelection } from '../../ui/workspace/folderWorkspace.js';

export function readLiveUpdateSetting() {
  return getLiveUpdateEnabled();
}

export function readDisplayModeSetting() {
  return getDisplayMode();
}

export function openAppSettings() {
  return openSettingsModal();
}

export function isFolderSelectionSupported(targetWindow = window) {
  return supportsFolderSelection(targetWindow);
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
