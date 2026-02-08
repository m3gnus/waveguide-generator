import { MWGConfigParser } from '../config/index.js';
import { GlobalState } from '../state.js';
import { isNumericString } from './params.js';

export function handleFileUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = (e) => {
    const content = e.target.result;
    const parsed = MWGConfigParser.parse(content);
    if (parsed.type) {
      // Convert string values to proper types
      const typedParams = {};
      for (const [key, value] of Object.entries(parsed.params)) {
        if (value === undefined || value === null) continue;

        const stringValue = String(value).trim();
        if (isNumericString(stringValue)) {
          typedParams[key] = Number(stringValue);
        } else {
          // Keep as string (expressions, etc.)
          typedParams[key] = stringValue;
        }
      }

      if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
        typedParams._blocks = parsed.blocks;
      }

      const isMWG = isMWGConfig(content);
      if (!isMWG) {
        applyAthImportDefaults(parsed, typedParams);
      }

      GlobalState.update(typedParams, parsed.type);
    } else {
      alert('Could not find OSSE or R-OSSE block in config file.');
    }
  };
  reader.readAsText(file);
}

function isMWGConfig(content) {
  if (typeof content !== 'string') return false;
  return /;\s*MWG config/i.test(content);
}

function applyAthImportDefaults(parsed, typedParams) {
  if (!parsed || !parsed.type) return;

  const isOSSE = parsed.type === 'OSSE';
  if (typedParams.morphTarget === undefined) {
    typedParams.morphTarget = 0;
  }
  const hasQuadrants =
    typedParams.quadrants !== undefined &&
    typedParams.quadrants !== null &&
    typedParams.quadrants !== '';
  if (!hasQuadrants) {
    typedParams.quadrants = isOSSE ? '14' : '1';
  }

  const hasMeshEnclosure = parsed.blocks && parsed.blocks['Mesh.Enclosure'];
  if (!hasMeshEnclosure && typedParams.encDepth === undefined) {
    typedParams.encDepth = 0;
  }

  if (isOSSE) {
    if (typedParams.k === undefined) {
      typedParams.k = 1;
    }
    if (typedParams.h === undefined) {
      typedParams.h = 0;
    }
  }
}
