import { MWGConfigParser } from '../config/index.js';
import { GlobalState } from '../state.js';
import { showError } from '../ui/feedback.js';
import {
  coerceConfigParams,
  applyAthImportDefaults,
  isMWGConfig
} from '../geometry/index.js';

export function handleFileUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
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

      GlobalState.update(typedParams, parsed.type);
    } else {
      showError('Could not find OSSE or R-OSSE block in config file.');
    }
  };
  reader.readAsText(file);
}
