const SETTINGS_KEY = 'waveguide-simulation-management-settings';
const SCHEMA_VERSION = 2;
const SUPPORTED_SCHEMA_VERSIONS = new Set([1, SCHEMA_VERSION]);
const LEGACY_DEFAULT_SELECTED_FORMATS = Object.freeze([
  'png',
  'csv',
  'json',
  'txt',
  'polar_csv',
  'impedance_csv',
  'vacs',
  'stl',
  'fusion_csv',
]);
const TASK_LIST_SORT_CONTROL_IDS = Object.freeze([
  'simmanage-default-sort',
  'simulation-jobs-sort',
]);
const TASK_LIST_MIN_RATING_CONTROL_IDS = Object.freeze([
  'simmanage-min-rating',
  'simulation-jobs-min-rating',
]);

export const SIMULATION_EXPORT_FORMAT_IDS = Object.freeze([
  'mwg_config',
  'step',
  'png',
  'csv',
  'json',
  'txt',
  'polar_csv',
  'impedance_csv',
  'vacs',
  'stl',
  'fusion_csv',
]);

export const RECOMMENDED_DEFAULTS = Object.freeze({
  autoExportOnComplete: false,
  selectedFormats: [],
  defaultSort: 'completed_desc',
  minRatingFilter: 0,
});

let currentSettings = null;

function formatsMatchIgnoringOrder(value, expected) {
  if (!Array.isArray(value) || value.length !== expected.length) {
    return false;
  }
  const normalized = normalizeSelectedFormats(value);
  return normalized.length === expected.length && expected.every((id) => normalized.includes(id));
}

function normalizeSelectedFormats(value) {
  if (!Array.isArray(value)) {
    return [...RECOMMENDED_DEFAULTS.selectedFormats];
  }

  const seen = new Set();
  const normalized = [];
  for (const raw of value) {
    const id = String(raw || '').trim();
    if (!SIMULATION_EXPORT_FORMAT_IDS.includes(id) || seen.has(id)) {
      continue;
    }
    seen.add(id);
    normalized.push(id);
  }

  return normalized;
}

function normalizeSettings(raw = {}) {
  return {
    autoExportOnComplete:
      typeof raw.autoExportOnComplete === 'boolean'
        ? raw.autoExportOnComplete
        : RECOMMENDED_DEFAULTS.autoExportOnComplete,
    selectedFormats: normalizeSelectedFormats(raw.selectedFormats),
    defaultSort:
      typeof raw.defaultSort === 'string' && raw.defaultSort.trim()
        ? raw.defaultSort
        : RECOMMENDED_DEFAULTS.defaultSort,
    minRatingFilter: Number.isFinite(Number(raw.minRatingFilter))
      ? Math.max(0, Math.min(5, Number(raw.minRatingFilter)))
      : RECOMMENDED_DEFAULTS.minRatingFilter,
  };
}

function migrateSettings(parsed) {
  const settings = { ...(parsed.simulationManagement || {}) };
  if (
    parsed.schemaVersion === 1 &&
    formatsMatchIgnoringOrder(settings.selectedFormats, LEGACY_DEFAULT_SELECTED_FORMATS)
  ) {
    settings.selectedFormats = [];
  }
  return settings;
}

export function loadSimulationManagementSettings() {
  if (typeof localStorage === 'undefined') {
    currentSettings = normalizeSettings();
    return currentSettings;
  }

  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) {
      currentSettings = normalizeSettings();
      return currentSettings;
    }

    const parsed = JSON.parse(raw);
    if (
      !parsed ||
      !SUPPORTED_SCHEMA_VERSIONS.has(parsed.schemaVersion) ||
      typeof parsed.simulationManagement !== 'object'
    ) {
      currentSettings = normalizeSettings();
      return currentSettings;
    }

    currentSettings = normalizeSettings(migrateSettings(parsed));
    return currentSettings;
  } catch {
    currentSettings = normalizeSettings();
    return currentSettings;
  }
}

export function saveSimulationManagementSettings(settings) {
  currentSettings = normalizeSettings(settings);

  if (typeof localStorage === 'undefined') {
    return currentSettings;
  }

  try {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({
        schemaVersion: SCHEMA_VERSION,
        simulationManagement: currentSettings,
      })
    );
  } catch {
    // Storage failures should not block runtime behavior.
  }

  return currentSettings;
}

export function getCurrentSimulationManagementSettings() {
  return currentSettings ?? loadSimulationManagementSettings();
}

export function getAutoExportOnComplete() {
  const el =
    typeof document !== 'undefined' ? document.getElementById('simmanage-auto-export') : null;
  if (el) {
    return el.checked;
  }
  return getCurrentSimulationManagementSettings().autoExportOnComplete;
}

export function getSelectedExportFormats() {
  if (typeof document !== 'undefined') {
    const controls = Array.from(document.querySelectorAll('input[data-sim-management-format]'));
    if (controls.length > 0) {
      const selected = controls
        .filter((input) => Boolean(input?.checked))
        .map((input) => String(input.getAttribute('data-sim-management-format') || '').trim())
        .filter(Boolean);

      return normalizeSelectedFormats(selected);
    }
  }

  return [...getCurrentSimulationManagementSettings().selectedFormats];
}

export function getTaskListSortPreference() {
  const el = getFirstPresentElement(TASK_LIST_SORT_CONTROL_IDS);
  if (el && typeof el.value === 'string' && el.value.trim()) {
    return el.value;
  }
  return getCurrentSimulationManagementSettings().defaultSort;
}

export function getTaskListMinRatingFilter() {
  const el = getFirstPresentElement(TASK_LIST_MIN_RATING_CONTROL_IDS);
  if (el) {
    const value = Number(el.value);
    if (Number.isFinite(value)) {
      return Math.max(0, Math.min(5, value));
    }
  }
  return getCurrentSimulationManagementSettings().minRatingFilter;
}

export function updateTaskListPreferences(updates = {}) {
  const current = getCurrentSimulationManagementSettings();
  return saveSimulationManagementSettings({
    ...current,
    defaultSort: updates.defaultSort ?? current.defaultSort,
    minRatingFilter: updates.minRatingFilter ?? current.minRatingFilter,
  });
}

export function resetSimulationManagementSettings() {
  return saveSimulationManagementSettings(RECOMMENDED_DEFAULTS);
}

function getFirstPresentElement(ids) {
  if (typeof document === 'undefined' || typeof document.getElementById !== 'function') {
    return null;
  }

  for (const id of ids) {
    const element = document.getElementById(id);
    if (element) {
      return element;
    }
  }

  return null;
}
