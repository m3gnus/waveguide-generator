// simAdvancedSettings.js — Stable public advanced simulation settings persistence.

const SETTINGS_KEY = "waveguide-sim-advanced-settings";
const SCHEMA_VERSION = 4;

export const RECOMMENDED_DEFAULTS = {
  useBurtonMiller: false,
  quadratureRegular: 4,
  workgroupSizeMultiple: 1,
  assemblyBackend: "opencl",
};

let _current = null;

function _coerceInt(value, fallback, min, max) {
  const n = Number(value);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

function _coerceBackend(value) {
  const v = String(value || "").trim().toLowerCase();
  if (v === "numba" || v === "opencl") return v;
  return RECOMMENDED_DEFAULTS.assemblyBackend;
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
      useBurtonMiller:
        typeof stored.useBurtonMiller === "boolean"
          ? stored.useBurtonMiller
          : RECOMMENDED_DEFAULTS.useBurtonMiller,
      quadratureRegular: _coerceInt(
        stored.quadratureRegular,
        RECOMMENDED_DEFAULTS.quadratureRegular,
        1,
        10,
      ),
      workgroupSizeMultiple: _coerceInt(
        stored.workgroupSizeMultiple,
        RECOMMENDED_DEFAULTS.workgroupSizeMultiple,
        1,
        8,
      ),
      assemblyBackend: _coerceBackend(stored.assemblyBackend),
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
    quadratureRegular: _coerceInt(
      settings?.quadratureRegular,
      RECOMMENDED_DEFAULTS.quadratureRegular,
      1,
      10,
    ),
    workgroupSizeMultiple: _coerceInt(
      settings?.workgroupSizeMultiple,
      RECOMMENDED_DEFAULTS.workgroupSizeMultiple,
      1,
      8,
    ),
    assemblyBackend: _coerceBackend(settings?.assemblyBackend),
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

export function getQuadratureRegular() {
  const el =
    typeof document !== "undefined"
      ? document.getElementById("simadvanced-quadratureRegular")
      : null;
  if (el) return _coerceInt(el.value, RECOMMENDED_DEFAULTS.quadratureRegular, 1, 10);
  return _current?.quadratureRegular ?? RECOMMENDED_DEFAULTS.quadratureRegular;
}

export function getWorkgroupSizeMultiple() {
  const el =
    typeof document !== "undefined"
      ? document.getElementById("simadvanced-workgroupSizeMultiple")
      : null;
  if (el) return _coerceInt(el.value, RECOMMENDED_DEFAULTS.workgroupSizeMultiple, 1, 8);
  return _current?.workgroupSizeMultiple ?? RECOMMENDED_DEFAULTS.workgroupSizeMultiple;
}

export function getAssemblyBackend() {
  const el =
    typeof document !== "undefined"
      ? document.getElementById("simadvanced-assemblyBackend")
      : null;
  if (el) return _coerceBackend(el.value);
  return _current?.assemblyBackend ?? RECOMMENDED_DEFAULTS.assemblyBackend;
}

export function resetSimAdvancedSettings() {
  const newSettings = { ...RECOMMENDED_DEFAULTS };
  saveSimAdvancedSettings(newSettings);
  return newSettings;
}
