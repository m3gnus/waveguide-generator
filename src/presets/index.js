/**
 * Preset management module for ATH Horn Design Platform.
 * Enables fast starting points and reproducibility with known-good horn archetypes.
 * @module presets
 */

/**
 * Preset structure
 * @typedef {Object} Preset
 * @property {string} name - Human-readable name of the preset
 * @property {string} description - Description of what the preset represents
 * @property {Object} parameters - Geometry and mesh parameters
 * @property {Object} bemSettings - BEM simulation settings
 * @property {Object} optimization - Default optimization objectives
 * @property {string} type - Type of horn (R-OSSE, OSSE, OS-GOS)
 * @property {Date} createdAt - When the preset was created
 * @property {Date} updatedAt - When the preset was last updated
 */

/**
 * Create a new preset manager
 * @returns {Object} Preset manager with methods to manage presets
 */
export function createPresetManager() {
  let presets = [];

  /**
   * Load all available presets
   * @returns {Array<Preset>} Array of all loaded presets
   */
  function loadPresets() {
    // In a real implementation, this would load from storage or files
    return presets;
  }

  /**
   * Save a preset to storage
   * @param {Preset} preset - The preset to save
   * @returns {void}
   */
  function savePreset(preset) {
    // In a real implementation, this would persist to storage
    presets.push({
      ...preset,
      createdAt: new Date(),
      updatedAt: new Date()
    });
  }

  /**
   * Load a preset by name
   * @param {string} name - Name of the preset to load
   * @returns {Preset|null} The loaded preset or null if not found
   */
  function loadPreset(name) {
    return presets.find(p => p.name === name) || null;
  }

  /**
   * Export a preset as JSON
   * @param {Preset} preset - The preset to export
   * @returns {string} JSON string representation of the preset
   */
  function exportPreset(preset) {
    return JSON.stringify(preset, null, 2);
  }

  /**
   * Import a preset from JSON
   * @param {string} json - JSON string representation of the preset
   * @returns {Preset|null} The parsed preset or null if invalid
   */
  function importPreset(json) {
    try {
      const preset = JSON.parse(json);
      savePreset(preset);
      return preset;
    } catch (error) {
      console.error('Failed to import preset:', error);
      return null;
    }
  }

  /**
   * Get all available presets for a specific horn type
   * @param {string} type - Type of horn (R-OSSE, OSSE, OS-GOS)
   * @returns {Array<Preset>} Array of presets matching the type
   */
  function getPresetsByType(type) {
    return presets.filter(p => p.type === type);
  }

  /**
   * Get all available preset types
   * @returns {Array<string>} Array of unique preset types
   */
  function getAvailableTypes() {
    const types = new Set(presets.map(p => p.type));
    return Array.from(types);
  }

  /**
   * Create a default ATH-style preset
   * @param {string} name - Name of the preset
   * @param {string} type - Type of horn (R-OSSE, OSSE, OS-GOS)
   * @returns {Preset} Default preset object
   */
  function createDefaultPreset(name, type) {
    return {
      name,
      description: `Default ${type} preset`,
      type,
      parameters: {
        // Default geometry parameters
        R: '140 * (abs(cos(p)/1.6)^3 + abs(sin(p)/1)^4)^(-1/4.5)',
        a: '25 * (abs(cos(p)/1.2)^4 + abs(sin(p)/1)^3)^(-1/2.5)',
        a0: 15.5,
        r0: 12.7,
        k: 2.0,
        m: 0.85,
        b: 0.2,
        r: 0.4,
        q: 3.4,
        tmax: 1.0,
        // Default mesh parameters
        angularSegments: 80,
        lengthSegments: 20,
        cornerSegments: 4,
        quadrants: '1234',
        wallThickness: 5.0,
        rearShape: 0
      },
      bemSettings: {
        abecSimType: 1,
        abecF1: 400,
        abecF2: 16000,
        abecNumFreq: 40
      },
      optimization: {
        objectives: ['flat_response', 'directivity_control'],
        weights: {
          flat_response: 0.5,
          directivity_control: 0.5
        }
      },
      createdAt: new Date(),
      updatedAt: new Date()
    };
  }

  /**
   * Create a known-good horn archetype preset
   * @param {string} name - Name of the archetype
   * @param {Object} params - Parameters for the archetype
   * @returns {Preset} Archetype preset object
   */
  function createArchetypePreset(name, params) {
    return {
      name,
      description: `Known-good ${name} horn archetype`,
      type: params.type || 'OSSE',
      parameters: {
        ...params
      },
      bemSettings: {
        abecSimType: 1,
        abecF1: 400,
        abecF2: 16000,
        abecNumFreq: 40
      },
      optimization: {
        objectives: ['flat_response', 'directivity_control'],
        weights: {
          flat_response: 0.5,
          directivity_control: 0.5
        }
      },
      createdAt: new Date(),
      updatedAt: new Date()
    };
  }

  return {
    loadPresets,
    savePreset,
    loadPreset,
    exportPreset,
    importPreset,
    getPresetsByType,
    getAvailableTypes,
    createDefaultPreset,
    createArchetypePreset
  };
}

// Export the preset manager as default
export default createPresetManager();