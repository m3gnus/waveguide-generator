// layoutSettings.js — Results-layout settings persistence service
// Single source of truth for split-view layout preferences: schema, defaults,
// localStorage read/write with a validated tolerant merge, and field helpers.

const SETTINGS_KEY = 'waveguide-layout-settings';
const SCHEMA_VERSION = 1;

const RESULTS_LAYOUTS = new Set(['classic', 'split']);
const PANEL_MODES = new Set(['auto', '1', '2']);
export const CHART_KEYS = [
  'directivity_map',
  'impedance',
  'directivity_index',
  'frequency_response',
];
const VALID_CHART_KEYS = new Set(CHART_KEYS);
const MIN_SPLIT_FRACTION = 0.15;
const MAX_SPLIT_FRACTION = 0.7;

export const RECOMMENDED_DEFAULTS = {
  resultsLayout: 'classic',
  panelMode: 'auto',
  splitFraction: 0.38,
  panelCharts: ['directivity_map', 'frequency_response'],
};

let _current = null;

function defaultSettings() {
  return {
    ...RECOMMENDED_DEFAULTS,
    panelCharts: [...RECOMMENDED_DEFAULTS.panelCharts],
  };
}

function clampSplitFraction(value) {
  if (!Number.isFinite(value)) return RECOMMENDED_DEFAULTS.splitFraction;
  return Math.max(MIN_SPLIT_FRACTION, Math.min(MAX_SPLIT_FRACTION, value));
}

function normalizeSettings(stored) {
  const normalized = defaultSettings();
  if (!stored || typeof stored !== 'object' || Array.isArray(stored)) return normalized;

  if (RESULTS_LAYOUTS.has(stored.resultsLayout)) {
    normalized.resultsLayout = stored.resultsLayout;
  }
  if (PANEL_MODES.has(stored.panelMode)) {
    normalized.panelMode = stored.panelMode;
  }
  if (typeof stored.splitFraction === 'number') {
    normalized.splitFraction = clampSplitFraction(stored.splitFraction);
  }
  if (Array.isArray(stored.panelCharts)) {
    normalized.panelCharts = RECOMMENDED_DEFAULTS.panelCharts.map((fallback, index) =>
      VALID_CHART_KEYS.has(stored.panelCharts[index]) ? stored.panelCharts[index] : fallback
    );
  }

  return normalized;
}

export function loadLayoutSettings() {
  if (typeof localStorage === 'undefined') {
    _current = defaultSettings();
    return _current;
  }

  try {
    const raw = localStorage.getItem(SETTINGS_KEY);
    if (!raw) {
      _current = defaultSettings();
      return _current;
    }

    const parsed = JSON.parse(raw);
    if (!parsed || parsed.schemaVersion !== SCHEMA_VERSION) {
      _current = defaultSettings();
      return _current;
    }

    _current = normalizeSettings(parsed.layout);
    return _current;
  } catch {
    _current = defaultSettings();
    return _current;
  }
}

export function saveLayoutSettings(settings) {
  const normalized = normalizeSettings(settings);
  _current = normalized;

  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(
        SETTINGS_KEY,
        JSON.stringify({ schemaVersion: SCHEMA_VERSION, layout: normalized })
      );
    } catch {
      // Silently ignore quota exceeded or other storage errors.
    }
  }

  return normalized;
}

export function getCurrentLayoutSettings() {
  return _current ?? loadLayoutSettings();
}

export function getResultsLayout() {
  return getCurrentLayoutSettings().resultsLayout;
}

export function setResultsLayout(resultsLayout) {
  return saveLayoutSettings({ ...getCurrentLayoutSettings(), resultsLayout });
}

export function getPanelMode() {
  return getCurrentLayoutSettings().panelMode;
}

export function setPanelMode(panelMode) {
  return saveLayoutSettings({ ...getCurrentLayoutSettings(), panelMode });
}

export function getSplitFraction() {
  return getCurrentLayoutSettings().splitFraction;
}

export function setSplitFraction(splitFraction) {
  return saveLayoutSettings({ ...getCurrentLayoutSettings(), splitFraction });
}

export function getPanelCharts() {
  return [...getCurrentLayoutSettings().panelCharts];
}

export function getPanelChart(index) {
  return getCurrentLayoutSettings().panelCharts[index] ?? RECOMMENDED_DEFAULTS.panelCharts[index];
}

export function setPanelCharts(panelCharts) {
  return saveLayoutSettings({ ...getCurrentLayoutSettings(), panelCharts });
}

export function setPanelChart(index, chartKey) {
  if (index !== 0 && index !== 1) return getCurrentLayoutSettings();
  const panelCharts = getPanelCharts();
  panelCharts[index] = chartKey;
  return setPanelCharts(panelCharts);
}

export function resetLayoutSettings() {
  return saveLayoutSettings(defaultSettings());
}
