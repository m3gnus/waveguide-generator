// appearanceSettings.js — Appearance settings persistence service
// Single source of truth for the result-chart theme: schema, default,
// localStorage read/write with tolerant merge, and a DOM-first getter.
// Mirrors the simBasicSettings.js pattern (a sibling localStorage key).

const SETTINGS_KEY = 'waveguide-appearance-settings';
const SCHEMA_VERSION = 1;

// Default chart theme. MUST match the backend default (DEFAULT_CHART_THEME in
// server/solver/theme_preview.py). 'hornlab' (Arctic Night) is byte-identical to
// the former 'dark' default, so first-run renders keep the dark look; 'classic'
// leads the picker order but is not the default.
export const DEFAULT_CHART_THEME = 'hornlab';

/**
 * Recommended defaults for all Appearance settings.
 * Exported for use by modal UI to show "Default" indicators.
 */
export const RECOMMENDED_DEFAULTS = {
  chartTheme: DEFAULT_CHART_THEME,
};

// Module-level in-memory cache — lazily populated by loadAppearanceSettings().
let _current = null;

/**
 * Load Appearance settings from localStorage with tolerant merge.
 * - Returns RECOMMENDED_DEFAULTS spread when localStorage is unavailable or empty.
 * - Returns RECOMMENDED_DEFAULTS when schema version mismatches.
 * - Known fields are copied from stored data only when they have the correct type.
 * - Unknown fields from stored data are discarded.
 * - Missing fields are filled with RECOMMENDED_DEFAULTS values.
 * - Caches result in _current for getCurrentAppearanceSettings().
 */
export function loadAppearanceSettings() {
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

    const stored = parsed.appearance;
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
 * Save Appearance settings to localStorage.
 * Also updates the in-memory cache.
 * Silently ignores quota errors.
 */
export function saveAppearanceSettings(settings) {
  if (typeof localStorage === 'undefined') {
    _current = settings;
    return;
  }

  _current = settings;

  try {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ schemaVersion: SCHEMA_VERSION, appearance: settings })
    );
  } catch {
    // Silently ignore quota exceeded or other storage errors.
  }
}

/**
 * Return the cached in-memory settings, loading from localStorage on first call.
 */
export function getCurrentAppearanceSettings() {
  return _current ?? loadAppearanceSettings();
}

/**
 * DOM-first getter for the selected chart theme.
 * Reads the Appearance picker when the settings modal is open, otherwise the
 * persisted value. Falls back to the default theme.
 */
export function getChartTheme() {
  const el =
    typeof document !== 'undefined' ? document.getElementById('appearance-chartTheme') : null;
  if (el && el.value) return el.value;
  return getCurrentAppearanceSettings().chartTheme || DEFAULT_CHART_THEME;
}

/**
 * Persist a chart theme selection. Returns the new settings object.
 */
export function setChartTheme(theme) {
  const updated = { ...getCurrentAppearanceSettings(), chartTheme: theme };
  saveAppearanceSettings(updated);
  return updated;
}

/**
 * Reset all Appearance settings to RECOMMENDED_DEFAULTS.
 * Saves immediately. Returns the new settings object.
 */
export function resetAppearanceSettings() {
  const newSettings = { ...RECOMMENDED_DEFAULTS };
  saveAppearanceSettings(newSettings);
  return newSettings;
}
