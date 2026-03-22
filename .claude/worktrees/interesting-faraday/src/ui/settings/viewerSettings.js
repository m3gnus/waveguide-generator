// viewerSettings.js — viewer settings persistence service
// Single source of truth for viewer settings: schema, defaults, localStorage
// read/write with tolerant merge, runtime apply to OrbitControls,
// wheel inversion interceptor, and reset helpers.

const SETTINGS_KEY = 'waveguide-app-settings';
const SCHEMA_VERSION = 1;

/**
 * Recommended defaults for all viewer settings.
 * Exported for use by modal UI to show "recommended" indicators.
 */
export const RECOMMENDED_DEFAULTS = {
  rotateSpeed: 1.0,
  zoomSpeed: 1.0,
  panSpeed: 1.0,
  dampingEnabled: true,
  dampingFactor: 0.05,
  invertWheelZoom: false,
  startupCameraMode: 'perspective',
  keyboardPanEnabled: false,   // matches current scene.js: listenToKeyEvents never called
};

/**
 * Maps sub-section key to the field names it owns.
 * Used by resetViewerSection to know which fields to reset.
 */
const SECTION_KEYS = {
  orbit: ['rotateSpeed', 'zoomSpeed', 'panSpeed', 'dampingEnabled', 'dampingFactor'],
  camera: ['startupCameraMode'],
  input: ['invertWheelZoom', 'keyboardPanEnabled'],
};

// Module-level in-memory cache — lazily populated by loadViewerSettings().
let _current = null;

// Module-level wheel interceptor reference — used by setInvertWheelZoom().
let _wheelInterceptor = null;

// Module-level debounce timer — used by debouncedSaveViewerSettings().
let _saveTimer = null;

/**
 * Load viewer settings from localStorage with tolerant merge.
 * - Returns RECOMMENDED_DEFAULTS spread when localStorage is unavailable or empty.
 * - Returns RECOMMENDED_DEFAULTS when schema version mismatches.
 * - Known fields are copied from stored data only when they have the correct type.
 * - Unknown fields from stored data are discarded.
 * - Missing fields are filled with RECOMMENDED_DEFAULTS values.
 * - Caches result in _current for getCurrentViewerSettings().
 */
export function loadViewerSettings() {
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

    const stored = parsed.viewer;
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
 * Save viewer settings to localStorage.
 * Also updates the in-memory cache.
 * Silently ignores quota errors.
 */
export function saveViewerSettings(settings) {
  if (typeof localStorage === 'undefined') return;

  _current = settings;

  try {
    localStorage.setItem(
      SETTINGS_KEY,
      JSON.stringify({ schemaVersion: SCHEMA_VERSION, viewer: settings })
    );
  } catch {
    // Silently ignore quota exceeded or other storage errors.
  }
}

/**
 * Return the cached in-memory settings, loading from localStorage on first call.
 * Used by toggleCamera in scene.js to re-apply settings after OrbitControls recreation.
 */
export function getCurrentViewerSettings() {
  return _current ?? loadViewerSettings();
}

/**
 * Debounced save — defers write to localStorage by 300 ms.
 * Use for slider/input change handlers to avoid write storms.
 */
export function debouncedSaveViewerSettings(settings) {
  clearTimeout(_saveTimer);
  _saveTimer = setTimeout(() => saveViewerSettings(settings), 300);
}

/**
 * Apply viewer settings to an OrbitControls instance.
 * Must be called after new OrbitControls(...) is created.
 * NOTE: invertWheelZoom is NOT applied here — call setInvertWheelZoom() separately.
 */
export function applyViewerSettingsToControls(controls, settings) {
  if (!controls) return;

  controls.rotateSpeed = settings.rotateSpeed;
  controls.zoomSpeed = settings.zoomSpeed;
  controls.panSpeed = settings.panSpeed;
  controls.enableDamping = settings.dampingEnabled;
  controls.dampingFactor = settings.dampingFactor;

  if (settings.keyboardPanEnabled) {
    controls.listenToKeyEvents(window);
  } else if (controls._domElementKeyEvents) {
    controls.stopListenToKeyEvents();
  }
}

/**
 * Register or remove a capture-phase wheel interceptor that inverts deltaY.
 * Safe to call multiple times — previous interceptor is always removed first.
 *
 * When invertEnabled is true, wheel events on the domElement are intercepted
 * before OrbitControls sees them; a new WheelEvent with -deltaY is re-dispatched
 * so zoom direction is reversed.
 */
export function setInvertWheelZoom(domElement, invertEnabled) {
  // Always remove any existing interceptor to prevent accumulation.
  if (_wheelInterceptor) {
    domElement.removeEventListener('wheel', _wheelInterceptor, { capture: true });
    _wheelInterceptor = null;
  }

  if (!invertEnabled) return;

  _wheelInterceptor = function invertedWheelHandler(e) {
    e.stopImmediatePropagation();

    const inverted = new WheelEvent('wheel', {
      deltaMode: e.deltaMode,
      deltaX: e.deltaX,
      deltaY: -e.deltaY,
      deltaZ: e.deltaZ,
      ctrlKey: e.ctrlKey,
      shiftKey: e.shiftKey,
      altKey: e.altKey,
      metaKey: e.metaKey,
      bubbles: e.bubbles,
      cancelable: e.cancelable,
    });

    e.target.dispatchEvent(inverted);
  };

  domElement.addEventListener('wheel', _wheelInterceptor, { capture: true });
}

/**
 * Reset one section's fields to RECOMMENDED_DEFAULTS.
 * Unknown section keys are ignored (returns current settings unchanged).
 * Saves immediately (not debounced — reset is intentional).
 * Returns the new full settings object.
 */
export function resetViewerSection(sectionKey) {
  const fields = SECTION_KEYS[sectionKey];
  if (!fields) {
    return getCurrentViewerSettings();
  }

  const updated = { ...(getCurrentViewerSettings()) };
  for (const field of fields) {
    updated[field] = RECOMMENDED_DEFAULTS[field];
  }

  saveViewerSettings(updated);
  return updated;
}

/**
 * Reset all viewer settings to RECOMMENDED_DEFAULTS.
 * Saves immediately. Returns the new settings object.
 */
export function resetAllViewerSettings() {
  const newSettings = { ...RECOMMENDED_DEFAULTS };
  saveViewerSettings(newSettings);
  return newSettings;
}
