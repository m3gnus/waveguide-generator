import { MWGConfigParser } from '../config/index.js';
import { GlobalState } from '../state.js';
import { showError } from '../ui/feedback.js';
import {
  deriveExportFieldsFromFileName,
  setExportFields,
  resetParameterChangeTracking
} from '../ui/fileOps.js';
import {
  coerceConfigParams,
  applyAthImportDefaults,
  isMWGConfig
} from '../geometry/index.js';

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
      const content = e.target.result;
      const parsed = MWGConfigParser.parse(content);
      if (parsed.type) {
        const typedParams = coerceConfigParams(parsed.params);

        if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
          typedParams._blocks = parsed.blocks;
        }

        if (!isMWGConfig(content)) {
          applyAthImportDefaults(parsed, typedParams);
        }

        // Loading a config establishes a new baseline; skip this update as a "user change".
        resetParameterChangeTracking({ skipNext: true });
        GlobalState.update(typedParams, parsed.type);
        setExportFields(deriveExportFieldsFromFileName(file.name));
      } else {
        showError('Could not find OSSE or R-OSSE block in config file.');
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
