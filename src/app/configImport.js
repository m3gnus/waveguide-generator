import { importMWGConfig } from '../modules/design/useCases.js';
import { GlobalState } from '../state.js';
import {
  showUiError,
  deriveExportFieldsFromImportedFileName,
  setAppExportFields,
  resetAppParameterChangeTracking
} from '../modules/ui/useCases.js';

export function handleFileUpload(event) {
  const target = event?.target;
  const file = target?.files?.[0];
  if (!file) return;

  const resetInputValue = () => {
    if (target && 'value' in target) {
      target.value = '';
    }
  };

  const reader = new FileReader();
  reader.onload = (e) => {
    try {
      const result = importMWGConfig(e.target.result, file.name);
      if (result.success) {
        // Loading a config establishes a new baseline; skip this update as a "user change".
        resetAppParameterChangeTracking({ skipNext: true });
        GlobalState.update(result.params, result.type);
        setAppExportFields(deriveExportFieldsFromImportedFileName(file.name));
      } else {
        showUiError(result.error || 'Failed to parse config file.');
      }
    } finally {
      resetInputValue();
    }
  };
  reader.onerror = () => {
    showUiError('Failed to read config file.');
    resetInputValue();
  };
  reader.readAsText(file);
}
