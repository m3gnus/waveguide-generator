// simAdvancedSettings.js — Stable public advanced simulation settings persistence.

const SETTINGS_KEY = 'waveguide-sim-advanced-settings';
const SCHEMA_VERSION = 6;

export const RECOMMENDED_DEFAULTS = {
  solverBackend: 'auto',
};

let _current = null;

function _coerceSolverBackend(value) {
  const v = String(value || '')
    .trim()
    .toLowerCase();
  if (v === 'auto' || v === 'metal' || v === 'bempp') return v;
  return RECOMMENDED_DEFAULTS.solverBackend;
}

export function loadSimAdvancedSettings() {
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

    const stored = parsed.simAdvanced;
    if (!stored || typeof stored !== 'object' || Array.isArray(stored)) {
      _current = { ...RECOMMENDED_DEFAULTS };
      return _current;
    }

    _current = {
      solverBackend: _coerceSolverBackend(stored.solverBackend),
    };
    return _current;
  } catch {
    _current = { ...RECOMMENDED_DEFAULTS };
    return _current;
  }
}

export function saveSimAdvancedSettings(settings) {
  if (typeof localStorage === 'undefined') return;

  _current = {
    solverBackend: _coerceSolverBackend(settings?.solverBackend),
  };

  try {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ schemaVersion: SCHEMA_VERSION, simAdvanced: _current })
    );
  } catch {
    // Ignore storage errors.
  }
}

export function getSolverBackend() {
  const el =
    typeof document !== 'undefined' ? document.getElementById('simadvanced-solverBackend') : null;
  if (el) return _coerceSolverBackend(el.value);
  return _current?.solverBackend ?? RECOMMENDED_DEFAULTS.solverBackend;
}

export function getCurrentSimAdvancedSettings() {
  return _current ?? loadSimAdvancedSettings();
}

export function resetSimAdvancedSettings() {
  const newSettings = { ...RECOMMENDED_DEFAULTS };
  saveSimAdvancedSettings(newSettings);
  return newSettings;
}
