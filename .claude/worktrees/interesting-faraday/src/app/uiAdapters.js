import {
  deriveExportFieldsFromFileName,
  markParametersChanged,
  resetParameterChangeTracking,
  selectOutputFolder,
  setExportFields
} from '../ui/fileOps.js';
import {
  showCommandSuggestion,
  showError,
  showMessage,
  showSuccess
} from '../ui/feedback.js';

export const appUiFeedback = Object.freeze({
  showCommandSuggestion,
  showError,
  showMessage,
  showSuccess
});

export const appUiFileOps = Object.freeze({
  deriveExportFieldsFromFileName,
  markParametersChanged,
  resetParameterChangeTracking,
  selectOutputFolder,
  setExportFields
});
