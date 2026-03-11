import { importMWGConfig } from '../modules/design/useCases.js';
import { GlobalState } from '../state.js';
import { showError } from '../ui/feedback.js';
import {
  deriveExportFieldsFromFileName,
  setExportFields,
  resetParameterChangeTracking
} from '../ui/fileOps.js';

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
        resetParameterChangeTracking({ skipNext: true });
        GlobalState.update(result.params, result.type);
        setExportFields(deriveExportFieldsFromFileName(file.name));
      } else {
        showError(result.error || 'Failed to parse config file.');
      }
    } finally {
      resetInputValue();
    }
  };
  reader.onerror = () => {
    showError('Failed to read config file.');
    resetInputValue();
  };
  reader.readAsText(file);
}
