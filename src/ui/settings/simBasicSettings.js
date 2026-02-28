// simBasicSettings.js — Simulation Basic settings persistence service
// Single source of truth for all Sim Basic settings: schema, defaults, localStorage
// read/write with tolerant merge and DOM-first getters.
// Mirrors the viewerSettings.js pattern exactly.

const SETTINGS_KEY = 'waveguide-sim-basic-settings';
const SCHEMA_VERSION = 1;

/**
 * Recommended defaults for all Simulation Basic settings.
 * Match the hardcoded values currently in jobActions.js.
 * Exported for use by modal UI to show "Default" indicators.
 */
export const RECOMMENDED_DEFAULTS = {
  deviceMode: 'auto',
  meshValidationMode: 'warn',
  frequencySpacing: 'log',
  useOptimized: true,
  enableSymmetry: true,
  verbose: true,
};

// Module-level in-memory cache — lazily populated by loadSimBasicSettings().
let _current = null;

/**
 * Load Sim Basic settings from localStorage with tolerant merge.
 * - Returns RECOMMENDED_DEFAULTS spread when localStorage is unavailable or empty.
 * - Returns RECOMMENDED_DEFAULTS when schema version mismatches.
 * - Known fields are copied from stored data only when they have the correct type.
 * - Unknown fields from stored data are discarded.
 * - Missing fields are filled with RECOMMENDED_DEFAULTS values.
 * - Caches result in _current for getCurrentSimBasicSettings().
 */
export function loadSimBasicSettings() {
  if (typeof localStorage === 'undefined') {
    _current = { ...RECOMMENDED_DEFAULTS };
    return _current;
  }

  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) {
      _current = { ...RECOMMENDED_DEFAULTS };
      return _current;
    }

    const parsed = JSON.parse(raw);

    if (!parsed || parsed.schemaVersion !== SCHEMA_VERSION) {
      _current = { ...RECOMMENDED_DEFAULTS };
      return _current;
    }

    const stored = parsed.simBasic;
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) {
      _current = { ...RECOMMENDED_DEFAULTS };
      return _current;
    }

    // Tolerant merge: start from defaults, overlay known fields with correct types.
    const merged = { ...RECOMMENDED_DEFAULTS };
    for (const key of Object.keys(RECOMMENDED_DEFAULTS)) {
      if (key in stored && typeof stored[key] === typeof RECOMMENDED_DEFAULTS[key]) {
        merged[key] = stored[key];
      }
    }

    _current = merged;
    return _current;
  } catch {
    _current = { ...RECOMMENDED_DEFAULTS };
    return _current;
  }
}

/**
 * Save Sim Basic settings to localStorage.
 * Also updates the in-memory cache.
 * Silently ignores quota errors.
 */
export function saveSimBasicSettings(settings) {
  if (typeof localStorage === 'undefined') return;

  _current = settings;

  try {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ schemaVersion: SCHEMA_VERSION, simBasic: settings })
    );
  } catch {
    // Silently ignore quota exceeded or other storage errors.
  }
}

/**
 * Return the cached in-memory settings, loading from localStorage on first call.
 */
export function getCurrentSimBasicSettings() {
  return _current ?? loadSimBasicSettings();
}

/**
 * DOM-first getter for device mode.
 * Returns the live DOM value when the modal is open, falls back to cached/default.
 */
export function getDeviceMode() {
  const el = typeof document !== 'undefined' ? document.getElementById('simbasic-deviceMode') : null;
  if (el) return el.value;
  return _current?.deviceMode ?? RECOMMENDED_DEFAULTS.deviceMode;
}

/**
 * DOM-first getter for mesh validation mode.
 */
export function getMeshValidationMode() {
  const el = typeof document !== 'undefined' ? document.getElementById('simbasic-meshValidationMode') : null;
  if (el) return el.value;
  return _current?.meshValidationMode ?? RECOMMENDED_DEFAULTS.meshValidationMode;
}

/**
 * DOM-first getter for frequency spacing.
 */
export function getFrequencySpacing() {
  const el = typeof document !== 'undefined' ? document.getElementById('simbasic-frequencySpacing') : null;
  if (el) return el.value;
  return _current?.frequencySpacing ?? RECOMMENDED_DEFAULTS.frequencySpacing;
}

/**
 * DOM-first getter for use_optimized.
 * Uses ?? (nullish coalescing) — false is a valid setting value.
 */
export function getUseOptimized() {
  const el = typeof document !== 'undefined' ? document.getElementById('simbasic-useOptimized') : null;
  if (el) return el.checked;
  return _current?.useOptimized ?? RECOMMENDED_DEFAULTS.useOptimized;
}

/**
 * DOM-first getter for enable_symmetry.
 */
export function getEnableSymmetry() {
  const el = typeof document !== 'undefined' ? document.getElementById('simbasic-enableSymmetry') : null;
  if (el) return el.checked;
  return _current?.enableSymmetry ?? RECOMMENDED_DEFAULTS.enableSymmetry;
}

/**
 * DOM-first getter for verbose.
 */
export function getVerbose() {
  const el = typeof document !== 'undefined' ? document.getElementById('simbasic-verbose') : null;
  if (el) return el.checked;
  return _current?.verbose ?? RECOMMENDED_DEFAULTS.verbose;
}

/**
 * Reset all Sim Basic settings to RECOMMENDED_DEFAULTS.
 * Saves immediately (not debounced — reset is intentional).
 * Returns the new settings object.
 */
export function resetSimBasicSettings() {
  const newSettings = { ...RECOMMENDED_DEFAULTS };
  saveSimBasicSettings(newSettings);
  return newSettings;
}

/**
 * Update the device mode in the persisted state.
 * Used by fallback logic in Plan 02 to reflect runtime mode resolution.
 */
export function updateDeviceModeSelection(mode) {
  const current = getCurrentSimBasicSettings();
  current.deviceMode = mode;
  saveSimBasicSettings(current);
}
