// simAdvancedSettings.js — Optimized-solver advanced settings persistence.

const SETTINGS_KEY = "waveguide-sim-advanced-settings";
const SCHEMA_VERSION = 2;

export const RECOMMENDED_DEFAULTS = {
  enableWarmup: true,
  bemPrecision: "single",
  useBurtonMiller: true,
};

let _current = null;

function normalizeBemPrecision(value, fallback) {
  const normalized = String(value || "")
    .trim()
    .toLowerCase();
  if (normalized === "single" || normalized === "double") {
    return normalized;
  }
  return fallback;
}

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
      enableWarmup:
        typeof stored.enableWarmup === "boolean"
          ? stored.enableWarmup
          : RECOMMENDED_DEFAULTS.enableWarmup,
      bemPrecision: normalizeBemPrecision(
        stored.bemPrecision,
        RECOMMENDED_DEFAULTS.bemPrecision,
      ),
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
    enableWarmup: Boolean(
      settings?.enableWarmup ?? RECOMMENDED_DEFAULTS.enableWarmup,
    ),
    bemPrecision: normalizeBemPrecision(
      settings?.bemPrecision,
      RECOMMENDED_DEFAULTS.bemPrecision,
    ),
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

export function getEnableWarmup() {
  const el =
    typeof document !== "undefined"
      ? document.getElementById("simadvanced-enableWarmup")
      : null;
  if (el) return el.checked;
  return _current?.enableWarmup ?? RECOMMENDED_DEFAULTS.enableWarmup;
}

export function getBemPrecision() {
  const el =
    typeof document !== "undefined"
      ? document.getElementById("simadvanced-bemPrecision")
      : null;
  if (el) {
    return normalizeBemPrecision(el.value, RECOMMENDED_DEFAULTS.bemPrecision);
  }
  return _current?.bemPrecision ?? RECOMMENDED_DEFAULTS.bemPrecision;
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
