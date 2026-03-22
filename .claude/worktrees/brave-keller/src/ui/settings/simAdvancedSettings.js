// simAdvancedSettings.js — Stable public advanced simulation settings persistence.

const SETTINGS_KEY = "waveguide-sim-advanced-settings";
const SCHEMA_VERSION = 3;

export const RECOMMENDED_DEFAULTS = {
  useBurtonMiller: true,
};

let _current = null;

export function loadSimAdvancedSettings() {
  if (typeof localStorage === "undefined") {
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
    if (!stored || typeof stored !== "object" || Array.isArray(stored)) {
      _current = { ...RECOMMENDED_DEFAULTS };
      return _current;
    }

    _current = {
      useBurtonMiller:
        typeof stored.useBurtonMiller === "boolean"
          ? stored.useBurtonMiller
          : RECOMMENDED_DEFAULTS.useBurtonMiller,
    };
    return _current;
  } catch {
    _current = { ...RECOMMENDED_DEFAULTS };
    return _current;
  }
}

export function saveSimAdvancedSettings(settings) {
  if (typeof localStorage === "undefined") return;

  _current = {
    useBurtonMiller: Boolean(
      settings?.useBurtonMiller ?? RECOMMENDED_DEFAULTS.useBurtonMiller,
    ),
  };

  try {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ schemaVersion: SCHEMA_VERSION, simAdvanced: _current }),
    );
  } catch {
    // Ignore storage errors.
  }
}

export function getCurrentSimAdvancedSettings() {
  return _current ?? loadSimAdvancedSettings();
}

export function getUseBurtonMiller() {
  const el =
    typeof document !== "undefined"
      ? document.getElementById("simadvanced-useBurtonMiller")
      : null;
  if (el) return el.checked;
  return _current?.useBurtonMiller ?? RECOMMENDED_DEFAULTS.useBurtonMiller;
}

export function resetSimAdvancedSettings() {
  const newSettings = { ...RECOMMENDED_DEFAULTS };
  saveSimAdvancedSettings(newSettings);
  return newSettings;
}
