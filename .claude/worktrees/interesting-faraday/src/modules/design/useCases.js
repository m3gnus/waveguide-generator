import { MWGConfigParser } from '../../config/index.js';
import {
  coerceConfigParams,
  applyAthImportDefaults,
  isMWGConfig
} from '../../geometry/params.js';

/**
 * Process a configuration file content and update GlobalState.
 * Establish appropriate export fields based on the file name.
 * 
 * @param {string} content Config file text content
 * @param {string} fileName Original file name for deriving export fields
 * @returns {Object} Result { success: boolean, error?: string, type?: string }
 */
export function importMWGConfig(content, fileName) {
  try {
    const parsed = MWGConfigParser.parse(content);
    if (!parsed.type) {
      return { success: false, error: 'Could not find OSSE or R-OSSE block in config file.' };
    }

    const typedParams = coerceConfigParams(parsed.params);

    if (parsed.blocks && Object.keys(parsed.blocks).length > 0) {
      typedParams._blocks = parsed.blocks;
    }

    if (!isMWGConfig(content)) {
      applyAthImportDefaults(parsed, typedParams);
    }

    // establish a new baseline (skip next change tracking update)
    // we return the data to the caller (App) which will update GlobalState
    return {
      success: true,
      params: typedParams,
      type: parsed.type,
      fileName
    };
  } catch (error) {
    return { success: false, error: error.message };
  }
}
