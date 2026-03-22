import { importMWGConfig } from '../modules/design/useCases.js';
import { GlobalState } from '../state.js';

export function handleFileUpload(event, ui = {}) {
  const target = event?.target;
  const file = target?.files?.[0];
  if (!file) return;
  const showError = typeof ui.showError === 'function'
    ? ui.showError.bind(ui)
    : (message) => console.error(message);

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
        ui.resetParameterChangeTracking?.({ skipNext: true });
        GlobalState.update(result.params, result.type);
        ui.setExportFields?.(
          ui.deriveExportFieldsFromFileName?.(file.name),
          document
        );
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
