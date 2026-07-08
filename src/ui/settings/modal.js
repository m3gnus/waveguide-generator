/**
 * Settings modal — popup with grouped sections for viewer behavior,
 * simulation defaults, task exports, workspace routing, and system actions.
 *
 * Interaction style mirrors the View Results popup: backdrop click or ESC closes.
 */

import { trapFocus } from '../focusTrap.js';

import {
  RECOMMENDED_DEFAULTS,
  getCurrentViewerSettings,
  debouncedSaveViewerSettings,
  applyViewerSettingsToControls,
  setInvertWheelZoom,
  resetViewerSection,
  resetAllViewerSettings,
} from './viewerSettings.js';

import {
  RECOMMENDED_DEFAULTS as SIM_BASIC_DEFAULTS,
  getCurrentSimBasicSettings,
  saveSimBasicSettings,
  resetSimBasicSettings,
} from './simBasicSettings.js';
import {
  RECOMMENDED_DEFAULTS as SIM_ADVANCED_DEFAULTS,
  getCurrentSimAdvancedSettings,
  getSolverBackend,
  saveSimAdvancedSettings,
} from './simAdvancedSettings.js';
import {
  SIMULATION_EXPORT_FORMAT_IDS,
  getCurrentSimulationManagementSettings,
  resetSimulationManagementSettings,
  saveSimulationManagementSettings,
} from './simulationManagementSettings.js';

import {
  getSelectedFolderLabel,
  requestBackendFolderSelection,
  subscribeFolderWorkspace,
  fetchWorkspacePath,
  openWorkspaceInFinder,
} from '../workspace/folderWorkspace.js';

import { getChartTheme, setChartTheme, resetAppearanceSettings } from './appearanceSettings.js';

import { DEFAULT_BACKEND_URL } from '../../config/backendUrl.js';

// DOM IDs of controls that now live in Settings (used by events.js wiring)
export const SETTINGS_CONTROL_IDS = {
  liveUpdate: 'live-update',

  downloadSimMesh: 'download-sim-mesh',
  checkUpdates: 'check-updates-btn',
};

// In-memory settings state so preferences survive modal close/reopen
const _state = {
  liveUpdate: true,
  displayMode: 'clay',
  downloadSimMesh: false,
};
const SIMULATION_MANAGEMENT_HELP = Object.freeze({
  downloadMesh: 'Automatically downloads the solver mesh file (.msh) when a job starts.',
  defaultSort: 'Sets the default order used in the Simulation Jobs list.',
  minRatingFilter: 'Hides completed jobs rated below this threshold in the Simulation Jobs list.',
  autoExportOnComplete:
    'Automatically exports results in the selected formats when a simulation completes.',
  selectedFormats: 'Selects which file formats are included when using Export.',
});
const VIEWER_HELP = Object.freeze({
  liveUpdate:
    'Applies geometry and viewport updates as soon as parameters change. Turn this off if you prefer to review changes manually before re-rendering.',
  rotateSpeed: 'Controls how quickly the camera orbits the model while dragging.',
  zoomSpeed: 'Controls how quickly scroll and pinch gestures move the camera toward the model.',
  panSpeed: 'Controls how quickly the viewport shifts when you pan the camera.',
  dampingEnabled: 'Keeps orbit movement eased instead of stopping abruptly after drag input ends.',
  dampingFactor: 'Adjusts how quickly the eased orbit motion settles after input stops.',
  startupCameraMode: 'Sets which camera projection opens by default the next time the app starts.',
  invertWheelZoom: 'Reverses the mouse-wheel zoom direction for viewport navigation.',
  keyboardPanEnabled:
    'Enables arrow-key style camera panning shortcuts while the viewport is focused.',
});
const SIMULATION_BASIC_HELP = Object.freeze({
  meshValidationMode:
    'Controls what happens when the mesh may be too coarse for the requested frequency range. Warn (default) flags issues but lets the solve proceed. Strict aborts the solve on a mesh warning. Off skips validation entirely. Recommended default: Warn.',
  frequencySpacing:
    'Determines how the N frequency points are placed between the start and end frequency. Log spaces them evenly on a logarithmic scale (equal ratios between steps — perceptually uniform for audio). Linear spaces them evenly in Hz. Recommended default: Log.',
  verbose:
    'Emits per-frequency solver progress and diagnostic messages to the server log and job status stream. Useful for monitoring long sweeps or diagnosing convergence issues; adds minor overhead. Recommended default: Off.',
});
const SIMULATION_ADVANCED_HELP = Object.freeze({
  solverBackend:
    'Chooses the solver for new jobs. Auto uses the Metal BEM release-helper path on Apple Silicon and falls back to Bempp on other hosts.',
});
const ADVANCED_CONTROL_COPY = Object.freeze({
  solver_backend: { label: 'Solver Backend' },
});
const APPEARANCE_HELP = Object.freeze({
  chartTheme:
    'Selects the color theme used to render simulation result charts (frequency response, directivity heatmap, directivity index, impedance). The preview shows all four canonical charts in the chosen theme. New renders and exports use the selected theme. "Dark" matches the app\'s built-in look.',
});
// Static fallback used only if GET /api/themes is unreachable (backend down).
// Mirrors the hornlab-plots registry order and labels.
const FALLBACK_CHART_THEMES = Object.freeze([
  { name: 'hornlab', label: 'HornLab — Arctic Night', default: false },
  { name: 'dark', label: 'Dark — Arctic Night', default: true },
  { name: 'granite', label: 'Granite — light paper', default: false },
  { name: 'abyss', label: 'Abyss — dark studio', default: false },
  { name: 'blueprint', label: 'Blueprint — drafting blue', default: false },
  { name: 'journal', label: 'Journal — print / grayscale', default: false },
  { name: 'contrast', label: 'High Contrast', default: false },
  { name: 'sepia', label: 'Sepia — warm paper', default: false },
  { name: 'phosphor', label: 'Phosphor — CRT green', default: false },
  { name: 'ember', label: 'Ember — warm charcoal', default: false },
  { name: 'classic', label: 'Classic — Klippel report', default: false },
]);
const SETTINGS_SECTION_ITEMS = Object.freeze([
  { key: 'viewer', label: 'Viewer' },
  { key: 'appearance', label: 'Appearance' },
  { key: 'simulation', label: 'Simulation' },
  { key: 'task-exports', label: 'Export Settings' },
  { key: 'workspace', label: 'Workspace' },
  { key: 'system', label: 'System' },
]);

/**
 * Get the current live-update preference.
 * Returns the DOM value when modal is open, otherwise the stored value.
 */
export function getLiveUpdateEnabled() {
  const el = document.getElementById('live-update');
  if (el) return el.checked;
  return _state.liveUpdate;
}

/**
 * Get the current display-mode value.
 */
export function getDisplayMode() {
  return _state.displayMode;
}

/**
 * Set the current display-mode value.
 */
export function setDisplayMode(mode) {
  _state.displayMode = mode;
}

/**
 * Get the current download-sim-mesh preference.
 */
export function getDownloadSimMeshEnabled() {
  const el = document.getElementById('download-sim-mesh');
  if (el) return el.checked;
  return _state.downloadSimMesh;
}

/**
 * Open the settings modal. Creates it on-demand and appends to document.body.
 * Returns the backdrop element so callers can await removal if needed.
 */
export function openSettingsModal(options = {}) {
  const viewerRuntime = _resolveViewerRuntime(options.viewerRuntime);
  // Prevent duplicate modals
  const existing = document.getElementById('settings-modal-backdrop');
  if (existing) {
    const dialog = existing.querySelector('[role="dialog"]');
    if (dialog) dialog.focus();
    return existing;
  }

  const { backdrop, cleanup } = _buildModal(viewerRuntime);
  document.body.appendChild(backdrop);

  const dialog = backdrop.querySelector('[role="dialog"]');
  const closeBtn = backdrop.querySelector('.settings-modal-close');
  const releaseFocus = trapFocus(dialog, { initialFocus: closeBtn });
  cleanup.push(releaseFocus);

  return backdrop;
}

// ---------------------------------------------------------------------------
// Internal builders
// ---------------------------------------------------------------------------

function _resolveViewerRuntime(runtime = {}) {
  return {
    getControls: typeof runtime?.getControls === 'function' ? runtime.getControls : () => null,
    getDomElement:
      typeof runtime?.getDomElement === 'function' ? runtime.getDomElement : () => null,
  };
}

function _buildModal(viewerRuntime) {
  const backdrop = document.createElement('div');
  backdrop.id = 'settings-modal-backdrop';
  backdrop.className = 'settings-modal-backdrop';
  const cleanupFns = [];

  const dialog = document.createElement('div');
  dialog.className = 'settings-modal-dialog';
  dialog.setAttribute('role', 'dialog');
  dialog.setAttribute('aria-modal', 'true');
  dialog.setAttribute('aria-label', 'Settings');
  dialog.setAttribute('tabindex', '-1');

  // Header
  const header = document.createElement('div');
  header.className = 'settings-modal-header';

  const title = document.createElement('h2');
  title.className = 'settings-modal-title';
  title.textContent = 'Settings';
  header.appendChild(title);

  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'settings-modal-close';
  closeBtn.textContent = '\u00d7';
  closeBtn.title = 'Close (Escape)';
  closeBtn.setAttribute('aria-label', 'Close settings');
  header.appendChild(closeBtn);

  dialog.appendChild(header);

  // Body: sidebar nav + content area
  const body = document.createElement('div');
  body.className = 'settings-modal-body';

  const nav = _buildNav(SETTINGS_SECTION_ITEMS);
  const content = _buildContent(viewerRuntime, cleanupFns);

  body.appendChild(nav);
  body.appendChild(content);
  dialog.appendChild(body);
  backdrop.appendChild(dialog);

  // --- Persist state changes from within the modal ---
  backdrop.addEventListener('change', (event) => {
    const t = event.target;
    if (!t) return;
    if (t.id === 'live-update') _state.liveUpdate = t.checked;
    if (t.id === 'download-sim-mesh') _state.downloadSimMesh = t.checked;

    // Sim Basic settings: save on any simbasic-* control change
    if (t.id && t.id.startsWith('simbasic-')) {
      _saveSimBasicSettingsFromModal(backdrop);
    }

    if (t.id && t.id.startsWith('simadvanced-')) {
      _saveSimAdvancedSettingsFromModal(backdrop);
    }

    if (_isSimulationManagementControl(t)) {
      const settings = _readSimulationManagementSettings(backdrop);
      saveSimulationManagementSettings(settings);
      _syncTaskListPreferenceControls(settings, {
        dispatchToolbarChange: t.id === 'simmanage-default-sort' || t.id === 'simmanage-min-rating',
      });
    }
  });

  // --- Close handlers ---
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener('keydown', onKeyDown);
    cleanupFns.splice(0).forEach((cleanup) => {
      try {
        cleanup();
      } catch (error) {
        console.warn('settings modal cleanup failed:', error);
      }
    });
    backdrop.remove();
  };

  const onKeyDown = (event) => {
    if (event.key === 'Escape') {
      event.preventDefault();
      close();
    }
  };

  closeBtn.addEventListener('click', close);
  backdrop.addEventListener('click', (event) => {
    if (event.target === backdrop) close();
  });
  window.addEventListener('keydown', onKeyDown);

  // --- Section nav tab switching ---
  const sectionBtns = nav.querySelectorAll('.settings-nav-btn');
  const sections = content.querySelectorAll('.settings-section');

  sectionBtns.forEach((btn) => {
    btn.addEventListener('click', () => {
      const target = btn.dataset.section;
      sectionBtns.forEach((b) => {
        b.classList.toggle('active', b.dataset.section === target);
        b.setAttribute('aria-selected', b.dataset.section === target ? 'true' : 'false');
      });
      sections.forEach((sec) => {
        sec.hidden = sec.id !== `settings-section-${target}`;
      });
    });
  });

  return { backdrop, cleanup: cleanupFns };
}

function _buildNav(items = SETTINGS_SECTION_ITEMS) {
  const nav = document.createElement('nav');
  nav.className = 'settings-modal-nav';
  nav.setAttribute('aria-label', 'Settings sections');

  items.forEach((item, i) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'settings-nav-btn' + (i === 0 ? ' active' : '');
    btn.dataset.section = item.key;
    btn.textContent = item.label;
    btn.setAttribute('role', 'tab');
    btn.setAttribute('aria-selected', i === 0 ? 'true' : 'false');
    nav.appendChild(btn);
  });

  return nav;
}

function _buildContent(viewerRuntime, cleanupFns = []) {
  const content = document.createElement('div');
  content.className = 'settings-modal-content';

  content.appendChild(_buildViewerSection(viewerRuntime));
  content.appendChild(_buildAppearanceSection(cleanupFns));
  content.appendChild(_buildSimulationSection());
  content.appendChild(_buildTaskExportsSection());
  content.appendChild(_buildWorkspaceSection(cleanupFns));
  content.appendChild(_buildSystemSection(viewerRuntime));

  return content;
}

function _buildAppearanceSection(cleanupFns = []) {
  const sec = document.createElement('div');
  sec.id = 'settings-section-appearance';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(sec, 'Appearance', 'Theme used to render simulation result charts.');

  // Reset restores the default theme (also persists it).
  const header = _buildSubSectionHeader('Result Chart Theme', () => {
    const reset = resetAppearanceSettings();
    applyThemeSelection(reset.chartTheme, { persist: false });
  });
  sec.appendChild(header);

  // Compact <select> control (accessible, always present).
  const select = document.createElement('select');
  select.id = 'appearance-chartTheme';
  const initialTheme = getChartTheme();
  const seedOption = document.createElement('option');
  seedOption.value = initialTheme;
  seedOption.textContent = initialTheme;
  seedOption.selected = true;
  select.appendChild(seedOption);

  _appendInlineRow(sec, {
    labelText: 'Chart Theme',
    labelFor: 'appearance-chartTheme',
    helpText: APPEARANCE_HELP.chartTheme,
    controlNode: select,
  });

  // Grid of clickable theme cards (visual picker).
  const grid = document.createElement('div');
  grid.className = 'theme-card-grid';
  grid.id = 'appearance-theme-grid';
  sec.appendChild(grid);

  // Montage preview for the focused/selected theme (lazy-loaded per theme).
  const previewWrap = document.createElement('div');
  previewWrap.className = 'theme-preview';

  const previewImg = document.createElement('img');
  previewImg.className = 'theme-preview-img';
  previewImg.alt = 'Result-chart theme preview montage';
  previewImg.loading = 'lazy';

  const previewStatus = document.createElement('div');
  previewStatus.className = 'theme-preview-status';

  previewWrap.appendChild(previewImg);
  previewWrap.appendChild(previewStatus);
  sec.appendChild(previewWrap);

  const backendUrl = DEFAULT_BACKEND_URL;
  let selectedTheme = initialTheme;
  const previewCache = new Map();

  // The theme list/preview load asynchronously; if the modal closes (or a test
  // tears down the DOM) first, stop so no continuation writes to detached or
  // undefined nodes. `document === undefined` covers teardown without a close.
  let cancelled = false;
  cleanupFns.push(() => {
    cancelled = true;
  });
  const stopped = () => cancelled || typeof document === 'undefined';

  function highlightCards() {
    grid.querySelectorAll('.theme-card').forEach((card) => {
      const active = card.dataset.theme === selectedTheme;
      card.classList.toggle('active', active);
      card.setAttribute('aria-pressed', active ? 'true' : 'false');
    });
  }

  async function loadPreview(theme) {
    if (stopped()) return;
    if (previewCache.has(theme)) {
      previewImg.src = previewCache.get(theme);
      previewStatus.textContent = '';
      return;
    }
    previewStatus.textContent = 'Rendering preview…';
    try {
      const res = await fetch(`${backendUrl}/api/theme-preview?theme=${encodeURIComponent(theme)}`);
      if (stopped()) return;
      if (!res.ok) throw new Error(`status ${res.status}`);
      const data = await res.json();
      if (stopped()) return;
      if (!data.image) throw new Error('empty preview');
      previewCache.set(theme, data.image);
      // Only apply if this is still the active selection (avoid races).
      if (selectedTheme === theme) {
        previewImg.src = data.image;
        previewStatus.textContent = '';
      }
    } catch {
      if (!stopped() && selectedTheme === theme) {
        previewStatus.textContent = 'Preview unavailable — is the backend running?';
      }
    }
  }

  function applyThemeSelection(theme, { persist = true } = {}) {
    selectedTheme = theme;
    if (![...select.options].some((opt) => opt.value === theme)) {
      const opt = document.createElement('option');
      opt.value = theme;
      opt.textContent = theme;
      select.appendChild(opt);
    }
    select.value = theme;
    if (persist) setChartTheme(theme);
    highlightCards();
    loadPreview(theme);
  }

  select.addEventListener('change', () => applyThemeSelection(select.value));

  // Populate themes from the backend (non-blocking); fall back to static list.
  (async () => {
    let themes = null;
    try {
      const res = await fetch(`${backendUrl}/api/themes`);
      if (stopped()) return;
      if (res.ok) {
        const body = await res.json();
        if (stopped()) return;
        if (Array.isArray(body.themes) && body.themes.length > 0) {
          themes = body.themes;
        }
      }
    } catch {
      // fall through to the static fallback
    }
    if (stopped()) return;
    if (!themes) themes = FALLBACK_CHART_THEMES;

    // Rebuild the select from the resolved theme list.
    select.innerHTML = '';
    themes.forEach((theme) => {
      const opt = document.createElement('option');
      opt.value = theme.name;
      opt.textContent = theme.label || theme.name;
      select.appendChild(opt);
    });

    // Keep the stored selection if still valid, else fall back to the default
    // and persist the repair so a stale/invalid stored theme cannot keep
    // 422-ing chart renders.
    if (!themes.some((theme) => theme.name === selectedTheme)) {
      const fallback = themes.find((theme) => theme.default) || themes[0];
      selectedTheme = fallback.name;
      setChartTheme(selectedTheme);
    }
    select.value = selectedTheme;

    // Build the visual card grid.
    grid.innerHTML = '';
    themes.forEach((theme) => {
      const card = document.createElement('button');
      card.type = 'button';
      card.className = 'theme-card';
      card.dataset.theme = theme.name;
      card.setAttribute('aria-pressed', theme.name === selectedTheme ? 'true' : 'false');

      const name = document.createElement('span');
      name.className = 'theme-card-name';
      name.textContent = theme.label || theme.name;

      const tag = document.createElement('span');
      tag.className = 'theme-card-tag';
      tag.textContent = theme.default ? `${theme.name} · default` : theme.name;

      card.appendChild(name);
      card.appendChild(tag);
      card.addEventListener('click', () => applyThemeSelection(theme.name));
      grid.appendChild(card);
    });

    highlightCards();
    loadPreview(selectedTheme);
  })();

  return sec;
}

// ---------------------------------------------------------------------------
// Section builders — controls are the actual interactive elements
// ---------------------------------------------------------------------------

function _buildViewerSection(viewerRuntime) {
  const sec = document.createElement('div');
  sec.id = 'settings-section-viewer';
  sec.className = 'settings-section';
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(sec, 'Viewer', 'Viewport display and rendering preferences.');

  // Real-time Updates control
  _appendInlineRow(sec, {
    labelText: 'Real-time Updates',
    labelFor: 'live-update',
    helpText: VIEWER_HELP.liveUpdate,
    controlHtml: `<input type="checkbox" id="live-update"${_state.liveUpdate ? ' checked' : ''}>`,
  });

  // --- Viewer sub-sections (Orbit Controls, Camera, Input) ---
  const currentSettings = getCurrentViewerSettings();

  // Mutable closure state — separate from the modal _state object
  let _viewerState = { ...currentSettings };

  // Live-apply helper: push _viewerState into OrbitControls and wheel zoom
  function _applyLive() {
    const controls = viewerRuntime.getControls();
    applyViewerSettingsToControls(controls, _viewerState);
    const domEl = viewerRuntime.getDomElement();
    if (domEl) setInvertWheelZoom(domEl, _viewerState.invertWheelZoom);
  }

  // Helper: update badge visibility based on current vs recommended value
  function _updateBadge(badgeEl, currentValue, recommendedValue) {
    badgeEl.hidden = currentValue !== recommendedValue;
  }

  // ---------- SUB-SECTION: Orbit Controls ----------
  const orbitHeader = _buildSubSectionHeader('Orbit Controls', onResetOrbit);
  sec.appendChild(orbitHeader);

  const rotateResult = _buildSliderRow(
    'Rotate Speed',
    'rotateSpeed',
    0.1,
    5.0,
    0.1,
    _viewerState,
    VIEWER_HELP.rotateSpeed
  );
  sec.appendChild(rotateResult.row);

  const zoomResult = _buildSliderRow(
    'Zoom Speed',
    'zoomSpeed',
    0.1,
    5.0,
    0.1,
    _viewerState,
    VIEWER_HELP.zoomSpeed
  );
  sec.appendChild(zoomResult.row);

  const panResult = _buildSliderRow(
    'Pan Speed',
    'panSpeed',
    0.1,
    5.0,
    0.1,
    _viewerState,
    VIEWER_HELP.panSpeed
  );
  sec.appendChild(panResult.row);

  // Damping enabled toggle
  const dampingEnabledResult = _buildToggleRow(
    'Enable Damping',
    'dampingEnabled',
    _viewerState,
    VIEWER_HELP.dampingEnabled,
    {
      onChange: (checked, badge) => {
        _viewerState.dampingEnabled = checked;
        dampingFactorResult.row.hidden = !checked;
        _updateBadge(badge, checked, RECOMMENDED_DEFAULTS.dampingEnabled);
        debouncedSaveViewerSettings(_viewerState);
        _applyLive();
      },
    }
  );
  const dampingToggle = dampingEnabledResult.checkbox;
  sec.appendChild(dampingEnabledResult.row);

  // Damping factor slider (hidden when damping disabled)
  const dampingFactorResult = _buildSliderRow(
    'Damping Factor',
    'dampingFactor',
    0.01,
    0.5,
    0.01,
    _viewerState,
    VIEWER_HELP.dampingFactor
  );
  dampingFactorResult.row.hidden = !_viewerState.dampingEnabled;
  sec.appendChild(dampingFactorResult.row);

  // ---------- SUB-SECTION: Camera ----------
  const cameraHeader = _buildSubSectionHeader('Camera', onResetCamera);
  sec.appendChild(cameraHeader);

  // Startup Camera Mode radio row
  const cameraRow = document.createElement('div');
  cameraRow.className = 'settings-control-row';

  cameraRow.appendChild(
    _buildSettingsLabelCopy('Startup Camera Mode', '', VIEWER_HELP.startupCameraMode)
  );

  const cameraValueWrapper = document.createElement('div');
  cameraValueWrapper.className = 'settings-control-value';

  const cameraBadge = document.createElement('span');
  cameraBadge.className = 'settings-recommended-badge';
  cameraBadge.textContent = 'Recommended';
  cameraBadge.hidden = _viewerState.startupCameraMode !== RECOMMENDED_DEFAULTS.startupCameraMode;

  const radioGroup = document.createElement('div');
  radioGroup.setAttribute('style', 'display:flex;gap:12px;align-items:center;');

  const radioOptions = [
    { value: 'perspective', label: 'Perspective' },
    { value: 'orthographic', label: 'Orthographic' },
  ];

  let perspectiveRadio;
  let orthographicRadio;
  radioOptions.forEach((opt) => {
    const radioId = `viewer-startupCameraMode-${opt.value}`;
    const radioLabel = document.createElement('label');
    radioLabel.setAttribute('style', 'display:flex;align-items:center;gap:4px;cursor:pointer;');

    const radio = document.createElement('input');
    radio.type = 'radio';
    radio.id = radioId;
    radio.name = 'viewer-startupCameraMode';
    radio.value = opt.value;
    radio.checked = _viewerState.startupCameraMode === opt.value;
    radio.setAttribute('style', 'margin:0;');

    if (opt.value === 'perspective') perspectiveRadio = radio;
    if (opt.value === 'orthographic') orthographicRadio = radio;

    radio.addEventListener('change', (e) => {
      if (e.target.checked) {
        _viewerState.startupCameraMode = opt.value;
        _updateBadge(cameraBadge, opt.value, RECOMMENDED_DEFAULTS.startupCameraMode);
        debouncedSaveViewerSettings(_viewerState);
        // NOTE: does NOT call _applyLive() — startup camera mode takes effect on next launch
      }
    });

    const radioText = document.createElement('span');
    radioText.textContent = opt.label;
    radioText.setAttribute('style', 'font-size:0.85rem;');

    radioLabel.appendChild(radio);
    radioLabel.appendChild(radioText);
    radioGroup.appendChild(radioLabel);
  });

  cameraValueWrapper.appendChild(radioGroup);
  cameraValueWrapper.appendChild(cameraBadge);
  cameraRow.appendChild(cameraValueWrapper);
  sec.appendChild(cameraRow);

  const cameraHelp = document.createElement('p');
  cameraHelp.className = 'settings-section-help';
  cameraHelp.setAttribute('style', 'margin-top:4px;font-style:italic;');
  cameraHelp.textContent = 'Takes effect on next launch.';
  sec.appendChild(cameraHelp);

  // ---------- SUB-SECTION: Input ----------
  const inputHeader = _buildSubSectionHeader('Input', onResetInput);
  sec.appendChild(inputHeader);

  const invertWheelResult = _buildToggleRow(
    'Invert Scroll Zoom',
    'invertWheelZoom',
    _viewerState,
    VIEWER_HELP.invertWheelZoom
  );
  sec.appendChild(invertWheelResult.row);

  const keyboardPanResult = _buildToggleRow(
    'Keyboard Pan Shortcuts',
    'keyboardPanEnabled',
    _viewerState,
    VIEWER_HELP.keyboardPanEnabled
  );
  sec.appendChild(keyboardPanResult.row);

  // ---------- Per-section reset handlers ----------

  function onResetOrbit() {
    const newSettings = resetViewerSection('orbit');
    _viewerState = { ..._viewerState, ...newSettings };

    // Update orbit slider DOMs
    _syncSliderRow(rotateResult, _viewerState.rotateSpeed, RECOMMENDED_DEFAULTS.rotateSpeed);
    _syncSliderRow(zoomResult, _viewerState.zoomSpeed, RECOMMENDED_DEFAULTS.zoomSpeed);
    _syncSliderRow(panResult, _viewerState.panSpeed, RECOMMENDED_DEFAULTS.panSpeed);

    // Update dampingEnabled toggle
    dampingToggle.checked = _viewerState.dampingEnabled;
    _updateBadge(
      dampingEnabledResult.badge,
      _viewerState.dampingEnabled,
      RECOMMENDED_DEFAULTS.dampingEnabled
    );
    dampingFactorResult.row.hidden = !_viewerState.dampingEnabled;

    // Update dampingFactor slider
    _syncSliderRow(
      dampingFactorResult,
      _viewerState.dampingFactor,
      RECOMMENDED_DEFAULTS.dampingFactor
    );

    _applyLive();
  }

  function onResetCamera() {
    const newSettings = resetViewerSection('camera');
    _viewerState = { ..._viewerState, ...newSettings };

    // Update radio buttons
    if (perspectiveRadio)
      perspectiveRadio.checked = _viewerState.startupCameraMode === 'perspective';
    if (orthographicRadio)
      orthographicRadio.checked = _viewerState.startupCameraMode === 'orthographic';
    _updateBadge(
      cameraBadge,
      _viewerState.startupCameraMode,
      RECOMMENDED_DEFAULTS.startupCameraMode
    );
    // NOTE: does NOT call _applyLive() — startup camera mode takes effect on next launch
  }

  function onResetInput() {
    const newSettings = resetViewerSection('input');
    _viewerState = { ..._viewerState, ...newSettings };

    // Update invertWheelZoom toggle
    invertWheelResult.checkbox.checked = _viewerState.invertWheelZoom;
    _updateBadge(
      invertWheelResult.badge,
      _viewerState.invertWheelZoom,
      RECOMMENDED_DEFAULTS.invertWheelZoom
    );

    // Update keyboardPanEnabled toggle
    keyboardPanResult.checkbox.checked = _viewerState.keyboardPanEnabled;
    _updateBadge(
      keyboardPanResult.badge,
      _viewerState.keyboardPanEnabled,
      RECOMMENDED_DEFAULTS.keyboardPanEnabled
    );

    _applyLive();
  }

  // ---------- Private helpers (closures over _viewerState) ----------

  function _buildSliderRow(
    labelText,
    settingKey,
    min,
    max,
    step,
    currentSettingsSnapshot,
    helpText = ''
  ) {
    const row = document.createElement('div');
    row.className = 'settings-control-row';

    const inputId = `viewer-${settingKey}`;

    row.appendChild(_buildSettingsLabelCopy(labelText, inputId, helpText));

    const valueWrapper = document.createElement('div');
    valueWrapper.className = 'settings-control-value';

    const sliderGroup = document.createElement('div');
    sliderGroup.className = 'settings-slider-group';

    const slider = document.createElement('input');
    slider.type = 'range';
    slider.id = inputId;
    slider.min = String(min);
    slider.max = String(max);
    slider.step = String(step);
    slider.value = String(currentSettingsSnapshot[settingKey]);

    const readout = document.createElement('span');
    readout.className = 'settings-slider-readout';
    readout.textContent = _formatSliderValue(currentSettingsSnapshot[settingKey], step);

    const badge = document.createElement('span');
    badge.className = 'settings-recommended-badge';
    badge.textContent = 'Recommended';
    badge.hidden = currentSettingsSnapshot[settingKey] !== RECOMMENDED_DEFAULTS[settingKey];

    slider.addEventListener('input', () => {
      const val = parseFloat(slider.value);
      readout.textContent = _formatSliderValue(val, step);
      _updateBadge(badge, val, RECOMMENDED_DEFAULTS[settingKey]);
      _viewerState[settingKey] = val;
      debouncedSaveViewerSettings(_viewerState);
      _applyLive();
    });

    sliderGroup.appendChild(slider);
    sliderGroup.appendChild(readout);
    sliderGroup.appendChild(badge);
    valueWrapper.appendChild(sliderGroup);
    row.appendChild(valueWrapper);

    return { row, badge, slider, readout };
  }

  function _buildToggleRow(
    labelText,
    settingKey,
    currentSettingsSnapshot,
    helpText = '',
    { onChange = null } = {}
  ) {
    const row = document.createElement('div');
    row.className = 'settings-control-row';

    const inputId = `viewer-${settingKey}`;

    row.appendChild(_buildSettingsLabelCopy(labelText, inputId, helpText));

    const valueWrapper = document.createElement('div');
    valueWrapper.className = 'settings-control-value';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = inputId;
    checkbox.checked = currentSettingsSnapshot[settingKey];

    const badge = document.createElement('span');
    badge.className = 'settings-recommended-badge';
    badge.textContent = 'Recommended';
    badge.hidden = currentSettingsSnapshot[settingKey] !== RECOMMENDED_DEFAULTS[settingKey];

    checkbox.addEventListener('change', (event) => {
      const checked = event.target.checked;
      if (typeof onChange === 'function') {
        onChange(checked, badge);
        return;
      }
      _viewerState[settingKey] = checked;
      _updateBadge(badge, checked, RECOMMENDED_DEFAULTS[settingKey]);
      debouncedSaveViewerSettings(_viewerState);
      _applyLive();
    });

    valueWrapper.appendChild(checkbox);
    valueWrapper.appendChild(badge);
    row.appendChild(valueWrapper);

    return { row, badge, checkbox };
  }

  function _syncSliderRow(result, newValue, recommendedValue) {
    result.slider.value = String(newValue);
    // Determine step from slider to format value correctly
    const step = parseFloat(result.slider.step);
    result.readout.textContent = _formatSliderValue(newValue, step);
    _updateBadge(result.badge, newValue, recommendedValue);
  }

  function _formatSliderValue(val, step) {
    // Show decimal places matching the step precision
    const decimals = step < 0.1 ? 2 : step < 1 ? 1 : 0;
    return val.toFixed(decimals);
  }

  return sec;
}

function _buildSimulationSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-simulation';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Simulation',
    'Persistent solve defaults live here. Advanced controls expose only stable public runtime overrides.'
  );

  const currentSimBasic = getCurrentSimBasicSettings();
  const solverHeader = _buildSubSectionHeader('Solve Defaults', () => {
    resetSimBasicSettings();
    const mvm = document.getElementById('simbasic-meshValidationMode');
    if (mvm) mvm.value = SIM_BASIC_DEFAULTS.meshValidationMode;
    const fs = document.getElementById('simbasic-frequencySpacing');
    if (fs) fs.value = SIM_BASIC_DEFAULTS.frequencySpacing;
    const vb = document.getElementById('simbasic-verbose');
    if (vb) vb.checked = SIM_BASIC_DEFAULTS.verbose;
    if (mvmBadge) mvmBadge.hidden = true;
    if (fsBadge) fsBadge.hidden = true;
    if (vbBadge) vbBadge.hidden = true;
  });
  sec.appendChild(solverHeader);

  const mvmResult = _buildSimBasicSelectRow(
    'Mesh Validation Policy',
    'simbasic-meshValidationMode',
    [
      { value: 'warn', label: 'Warn' },
      { value: 'strict', label: 'Strict' },
      { value: 'off', label: 'Off' },
    ],
    currentSimBasic.meshValidationMode,
    SIM_BASIC_DEFAULTS.meshValidationMode,
    SIMULATION_BASIC_HELP.meshValidationMode
  );
  sec.appendChild(mvmResult.row);
  let mvmBadge = mvmResult.badge;

  const fsResult = _buildSimBasicSelectRow(
    'Sweep Spacing',
    'simbasic-frequencySpacing',
    [
      { value: 'log', label: 'Logarithmic' },
      { value: 'linear', label: 'Linear' },
    ],
    currentSimBasic.frequencySpacing,
    SIM_BASIC_DEFAULTS.frequencySpacing,
    SIMULATION_BASIC_HELP.frequencySpacing
  );
  sec.appendChild(fsResult.row);
  let fsBadge = fsResult.badge;

  const vbResult = _buildSimBasicCheckboxRow(
    'Verbose Backend Logging',
    'simbasic-verbose',
    currentSimBasic.verbose,
    SIM_BASIC_DEFAULTS.verbose,
    SIMULATION_BASIC_HELP.verbose
  );
  sec.appendChild(vbResult.row);
  let vbBadge = vbResult.badge;

  const advancedHeader = _buildSubSectionHeader('Advanced Solver Controls');
  sec.appendChild(advancedHeader);

  const currentSimAdvanced = getCurrentSimAdvancedSettings();
  const advancedIntro = document.createElement('div');
  advancedIntro.className = 'settings-section-help';
  advancedIntro.innerHTML =
    'Auto uses the Metal BEM release-helper path on Apple Silicon and Bempp on other hosts. ' +
    'Bempp is cross-platform and uses OpenCL acceleration when available, with a numba CPU fallback.';
  sec.appendChild(advancedIntro);

  const advancedActiveHeader = _buildSubSectionHeader('Active Contract Overrides', () => {
    saveSimAdvancedSettings({ ...SIM_ADVANCED_DEFAULTS });
    const sb = document.getElementById('simadvanced-solverBackend');
    if (sb) sb.value = SIM_ADVANCED_DEFAULTS.solverBackend;
    if (typeof sbBadge !== 'undefined' && sbBadge) sbBadge.hidden = true;
  });
  sec.appendChild(advancedActiveHeader);

  const sbResult = _buildSimBasicSelectRow(
    ADVANCED_CONTROL_COPY.solver_backend.label,
    'simadvanced-solverBackend',
    [
      { value: 'auto', label: 'Auto' },
      { value: 'metal', label: 'Metal BEM' },
      { value: 'bempp', label: 'Bempp (cross-platform)' },
    ],
    currentSimAdvanced.solverBackend,
    SIM_ADVANCED_DEFAULTS.solverBackend,
    SIMULATION_ADVANCED_HELP.solverBackend
  );
  sec.appendChild(sbResult.row);
  let sbBadge = sbResult.badge;

  return sec;
}

function _buildTaskExportsSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-task-exports';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Export Settings',
    'Job-list preferences, automatic result bundles, and manual export formats all live together here.'
  );

  const managementSettings = getCurrentSimulationManagementSettings();

  const taskListHeader = _buildSubSectionHeader('Simulation Jobs Toolbar');
  sec.appendChild(taskListHeader);

  _appendInlineRow(sec, {
    labelText: 'Default Task Sort',
    labelFor: 'simmanage-default-sort',
    helpText: SIMULATION_MANAGEMENT_HELP.defaultSort,
    controlNode: _buildSelectElement('simmanage-default-sort', managementSettings.defaultSort, [
      { value: 'completed_desc', label: 'Newest First' },
      { value: 'rating_desc', label: 'Highest Rated' },
      { value: 'label_asc', label: 'Label A-Z' },
    ]),
  });

  _appendInlineRow(sec, {
    labelText: 'Minimum Rating Filter',
    labelFor: 'simmanage-min-rating',
    helpText: SIMULATION_MANAGEMENT_HELP.minRatingFilter,
    controlNode: _buildSelectElement(
      'simmanage-min-rating',
      String(managementSettings.minRatingFilter),
      [
        { value: '0', label: 'All Ratings' },
        { value: '1', label: '1 star or higher' },
        { value: '2', label: '2 stars or higher' },
        { value: '3', label: '3 stars or higher' },
        { value: '4', label: '4 stars or higher' },
        { value: '5', label: '5 stars only' },
      ]
    ),
  });

  const exportHeader = _buildSubSectionHeader('Completed Task Bundles', () => {
    const resetSettings = resetSimulationManagementSettings();
    const defaultSort = document.getElementById('simmanage-default-sort');
    if (defaultSort) {
      defaultSort.value = resetSettings.defaultSort;
    }
    const minRating = document.getElementById('simmanage-min-rating');
    if (minRating) {
      minRating.value = String(resetSettings.minRatingFilter);
    }
    _syncTaskListPreferenceControls(resetSettings);
  });
  sec.appendChild(exportHeader);

  _appendInlineRow(sec, {
    labelText: 'Auto-download solve mesh (.msh)',
    labelFor: 'download-sim-mesh',
    helpText: SIMULATION_MANAGEMENT_HELP.downloadMesh,
    controlHtml: `<input type="checkbox" id="download-sim-mesh"${_state.downloadSimMesh ? ' checked' : ''}>`,
  });

  return sec;
}

function _buildWorkspaceSection(cleanupFns = []) {
  const sec = document.createElement('div');
  sec.id = 'settings-section-workspace';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Workspace',
    'Manage the folder workspace used for manual exports and completed simulation-task bundles.'
  );

  const statusRow = document.createElement('div');
  statusRow.className = 'settings-control-row';
  statusRow.appendChild(
    _buildSettingsLabelCopy(
      'Selected Folder',
      '',
      'The backend workspace folder used for all exports.'
    )
  );
  const statusValue = document.createElement('div');
  statusValue.className = 'settings-control-value';
  const statusText = document.createElement('span');
  statusText.id = 'settings-workspace-folder-label';
  statusValue.appendChild(statusText);
  statusRow.appendChild(statusValue);
  sec.appendChild(statusRow);

  // Path display row
  const pathRow = document.createElement('div');
  pathRow.className = 'settings-control-row';
  const pathLabel = document.createElement('div');
  pathLabel.className = 'settings-control-label';
  pathLabel.textContent = 'Output Folder Path';
  pathRow.appendChild(pathLabel);
  const pathValueBox = document.createElement('pre');
  pathValueBox.className = 'ui-command-box settings-workspace-path-box';
  pathValueBox.textContent = 'Loading…';
  const pathValueWrap = document.createElement('div');
  pathValueWrap.className = 'settings-control-value';
  pathValueWrap.appendChild(pathValueBox);
  pathRow.appendChild(pathValueWrap);
  sec.appendChild(pathRow);

  // "Open in Finder" button row
  const finderRow = document.createElement('div');
  finderRow.className = 'settings-action-row';
  const finderBtn = document.createElement('button');
  finderBtn.type = 'button';
  finderBtn.className = 'secondary';
  finderBtn.textContent = 'Open in Finder';
  const finderHelp = document.createElement('p');
  finderHelp.className = 'settings-action-help';
  finderHelp.textContent = 'Opens the output folder in the OS file manager (Finder / Explorer).';
  finderRow.appendChild(finderBtn);
  finderRow.appendChild(finderHelp);
  sec.appendChild(finderRow);

  const chooseRow = document.createElement('div');
  chooseRow.className = 'settings-action-row';

  const chooseBtn = document.createElement('button');
  chooseBtn.type = 'button';
  chooseBtn.id = 'settings-choose-folder-btn';
  chooseBtn.className = 'secondary';
  chooseRow.appendChild(chooseBtn);

  const chooseHelp = document.createElement('p');
  chooseHelp.id = 'settings-workspace-support';
  chooseHelp.className = 'settings-action-help';
  chooseRow.appendChild(chooseHelp);
  sec.appendChild(chooseRow);

  const routingNote = document.createElement('p');
  routingNote.id = 'settings-workspace-routing';
  routingNote.className = 'settings-section-help';
  sec.appendChild(routingNote);

  finderBtn.addEventListener('click', async () => {
    finderBtn.disabled = true;
    const ok = await openWorkspaceInFinder();
    finderBtn.disabled = false;
    if (!ok) {
      finderHelp.textContent = 'Could not open folder — is the backend running?';
    }
  });

  const refreshWorkspaceCopy = () => {
    const selectedLabel = getSelectedFolderLabel();
    statusText.textContent = selectedLabel;
    chooseBtn.textContent =
      selectedLabel === 'No folder selected' ? 'Choose Folder' : 'Change Folder';
    chooseBtn.disabled = false;

    chooseHelp.textContent = 'Opens a native folder picker via the backend server.';
    routingNote.textContent =
      'Exports are saved to the folder shown above. Use Choose Folder to change it.';

    fetchWorkspacePath().then((path) => {
      pathValueBox.textContent = path || 'Backend unavailable — path unknown.';
    });
  };

  chooseBtn.addEventListener('click', async () => {
    chooseBtn.disabled = true;
    chooseHelp.textContent = 'Waiting for folder selection…';
    const selectedPath = await requestBackendFolderSelection();
    chooseBtn.disabled = false;
    if (selectedPath) {
      pathValueBox.textContent = selectedPath;
    }
    refreshWorkspaceCopy();
  });

  const unsubscribe = subscribeFolderWorkspace(() => {
    refreshWorkspaceCopy();
  });
  cleanupFns.push(unsubscribe);
  refreshWorkspaceCopy();

  return sec;
}

function _buildSubSectionHeader(titleText, onReset = null) {
  const hdr = document.createElement('div');
  hdr.className = 'settings-subsection-header';

  const h4 = document.createElement('h4');
  h4.className = 'settings-subsection-title';
  h4.textContent = titleText;
  hdr.appendChild(h4);

  if (typeof onReset === 'function') {
    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'settings-reset-btn';
    resetBtn.textContent = 'Reset';
    resetBtn.addEventListener('click', onReset);
    hdr.appendChild(resetBtn);
  }

  return hdr;
}

function _makeDefaultBadge(currentValue, defaultValue) {
  const badge = document.createElement('span');
  badge.setAttribute('style', 'font-size:0.7rem;opacity:0.6;margin-left:6px;');
  badge.textContent = 'Default';
  badge.hidden = currentValue === defaultValue;
  return badge;
}

function _buildSimBasicSelectRow(
  labelText,
  selectId,
  options,
  currentValue,
  defaultValue,
  helpText = ''
) {
  const row = document.createElement('div');
  row.className = 'settings-control-row';
  row.appendChild(_buildSettingsLabelCopy(labelText, selectId, helpText));

  const valueWrapper = document.createElement('div');
  valueWrapper.className = 'settings-control-value';

  const select = document.createElement('select');
  select.id = selectId;

  for (const { value, label: optLabel } of options) {
    const opt = document.createElement('option');
    opt.value = value;
    opt.textContent = optLabel;
    if (value === currentValue) opt.selected = true;
    select.appendChild(opt);
  }

  const badge = _makeDefaultBadge(currentValue, defaultValue);

  select.addEventListener('change', () => {
    badge.hidden = select.value === defaultValue;
  });

  valueWrapper.appendChild(select);
  valueWrapper.appendChild(badge);
  row.appendChild(valueWrapper);

  return { row, badge, select };
}

function _buildSimBasicCheckboxRow(
  labelText,
  checkboxId,
  currentValue,
  defaultValue,
  helpText = ''
) {
  const row = document.createElement('div');
  row.className = 'settings-control-row';
  row.appendChild(_buildSettingsLabelCopy(labelText, checkboxId, helpText));

  const valueWrapper = document.createElement('div');
  valueWrapper.className = 'settings-control-value';

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.id = checkboxId;
  checkbox.checked = currentValue;

  const badge = _makeDefaultBadge(currentValue, defaultValue);

  checkbox.addEventListener('change', () => {
    badge.hidden = checkbox.checked === defaultValue;
  });

  valueWrapper.appendChild(checkbox);
  valueWrapper.appendChild(badge);
  row.appendChild(valueWrapper);

  return { row, badge, checkbox };
}

function _buildSimulationExportFormatsRow(managementSettings) {
  const exportFormatsRow = document.createElement('div');
  exportFormatsRow.className = 'settings-control-row';
  exportFormatsRow.appendChild(
    _buildSettingsLabelCopy('Bundle Formats', '', SIMULATION_MANAGEMENT_HELP.selectedFormats)
  );

  const exportFormatsValue = document.createElement('div');
  exportFormatsValue.className = 'settings-control-value';
  exportFormatsValue.setAttribute(
    'style',
    'display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));gap:8px 12px;align-items:start;'
  );

  const exportFormatLabels = new Map([
    ['mwg_config', 'Parameter Config (.txt)'],
    ['step', 'Waveguide STEP'],
    ['png', 'Chart Images (PNG)'],
    ['csv', 'Frequency Data CSV'],
    ['json', 'Full Results JSON'],
    ['txt', 'Summary Text Report'],
    ['polar_csv', 'Polar Directivity CSV'],
    ['impedance_csv', 'Impedance CSV'],
    ['vacs', 'ABEC Spectrum (VACS)'],
    ['stl', 'Waveguide STL'],
    ['fusion_csv', 'Fusion 360 CSV Curves'],
  ]);

  SIMULATION_EXPORT_FORMAT_IDS.forEach((formatId) => {
    const option = document.createElement('label');
    option.setAttribute('style', 'display:flex;align-items:center;gap:8px;');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.id = `simmanage-format-${formatId}`;
    checkbox.setAttribute('data-sim-management-format', formatId);
    checkbox.checked = managementSettings.selectedFormats.includes(formatId);
    option.appendChild(checkbox);
    const text = document.createElement('span');
    text.textContent = exportFormatLabels.get(formatId) || formatId;
    option.appendChild(text);
    exportFormatsValue.appendChild(option);
  });

  exportFormatsRow.appendChild(exportFormatsValue);
  return exportFormatsRow;
}

function _buildSystemSection(viewerRuntime) {
  const sec = document.createElement('div');
  sec.id = 'settings-section-system';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(sec, 'System', 'Application updates and system information.');

  const updateRow = document.createElement('div');
  updateRow.className = 'settings-action-row';

  const updateBtn = document.createElement('button');
  updateBtn.type = 'button';
  updateBtn.id = 'check-updates-btn';
  updateBtn.className = 'secondary';
  updateBtn.textContent = 'Check for App Updates';
  updateRow.appendChild(updateBtn);

  const updateHelp = document.createElement('p');
  updateHelp.className = 'settings-action-help';
  updateHelp.textContent =
    'Queries the backend for the latest commit on the default remote branch and reports whether the local copy is behind, ahead, or up to date.';
  updateRow.appendChild(updateHelp);

  sec.appendChild(updateRow);

  // Reset All Settings action row
  const resetAllRow = document.createElement('div');
  resetAllRow.className = 'settings-action-row';

  const resetAllBtn = document.createElement('button');
  resetAllBtn.type = 'button';
  resetAllBtn.id = 'reset-all-settings-btn';
  resetAllBtn.className = 'secondary';
  resetAllBtn.textContent = 'Reset Viewer Settings to Defaults';
  resetAllRow.appendChild(resetAllBtn);

  const resetAllHelp = document.createElement('p');
  resetAllHelp.className = 'settings-action-help';
  resetAllHelp.textContent =
    'Restores viewer controls to their recommended default values. Simulation, export, and workspace preferences stay unchanged.';
  resetAllRow.appendChild(resetAllHelp);

  resetAllBtn.addEventListener('click', () => {
    resetAllViewerSettings();
    applyViewerSettingsToControls(viewerRuntime.getControls(), RECOMMENDED_DEFAULTS);
    const domEl = viewerRuntime.getDomElement();
    if (domEl) setInvertWheelZoom(domEl, RECOMMENDED_DEFAULTS.invertWheelZoom);
  });

  sec.appendChild(resetAllRow);

  return sec;
}

// ---------------------------------------------------------------------------
// Runtime capability refresh
// ---------------------------------------------------------------------------

// The simulation settings UI currently renders only stable controls and no
// longer needs runtime capability refresh logic.

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function _appendSectionHeading(parent, title, helpText) {
  const h = document.createElement('h3');
  h.className = 'settings-section-title';
  h.textContent = title;
  parent.appendChild(h);

  if (helpText) {
    const p = document.createElement('p');
    p.className = 'settings-section-help';
    p.textContent = helpText;
    parent.appendChild(p);
  }
}

function _appendInlineRow(
  parent,
  { labelText, labelFor, controlHtml = '', controlNode = null, helpText = '' }
) {
  const row = document.createElement('div');
  row.className = 'settings-control-row';
  row.appendChild(_buildSettingsLabelCopy(labelText, labelFor, helpText));

  const wrapper = document.createElement('div');
  wrapper.className = 'settings-control-value';
  if (controlNode) {
    wrapper.appendChild(controlNode);
  } else {
    wrapper.innerHTML = controlHtml;
  }
  row.appendChild(wrapper);

  parent.appendChild(row);
}

function _buildSettingsLabelCopy(labelText, labelFor, helpText = '') {
  const copy = document.createElement('div');
  copy.className = 'settings-control-copy';

  const label = document.createElement('label');
  if (labelFor) {
    label.setAttribute('for', labelFor);
  }
  label.textContent = labelText;
  if (helpText) {
    label.setAttribute('data-help-text', helpText);
  }
  copy.appendChild(label);

  return copy;
}

function _buildSelectElement(id, currentValue, options) {
  const select = document.createElement('select');
  select.id = id;

  options.forEach(({ value, label }) => {
    const option = document.createElement('option');
    option.value = value;
    option.textContent = label;
    if (String(value) === String(currentValue)) {
      option.selected = true;
    }
    select.appendChild(option);
  });

  return select;
}

function _isSimulationManagementControl(target) {
  return Boolean(
    target?.id === 'simmanage-auto-export' ||
    target?.id === 'simmanage-default-sort' ||
    target?.id === 'simmanage-min-rating' ||
    target?.getAttribute?.('data-sim-management-format')
  );
}

function _getControl(root, id) {
  return root?.querySelector?.(`#${id}`) || null;
}

function _saveSimBasicSettingsFromModal(root) {
  const current = getCurrentSimBasicSettings();
  saveSimBasicSettings({
    ...current,
    meshValidationMode:
      _getControl(root, 'simbasic-meshValidationMode')?.value ?? current.meshValidationMode,
    frequencySpacing:
      _getControl(root, 'simbasic-frequencySpacing')?.value ?? current.frequencySpacing,
    verbose: _getControl(root, 'simbasic-verbose')?.checked ?? current.verbose,
  });
}

function _saveSimAdvancedSettingsFromModal(root) {
  const current = getCurrentSimAdvancedSettings();
  saveSimAdvancedSettings({
    ...current,
    solverBackend: _getControl(root, 'simadvanced-solverBackend')?.value ?? getSolverBackend(),
  });
}

function _readSimulationManagementSettings(root) {
  const current = getCurrentSimulationManagementSettings();
  const selectedFormats = Array.from(root.querySelectorAll('input[data-sim-management-format]'))
    .filter((input) => input.checked)
    .map((input) => input.getAttribute('data-sim-management-format'))
    .filter(Boolean);
  const minRating = Number(_getControl(root, 'simmanage-min-rating')?.value);

  return {
    ...current,
    autoExportOnComplete:
      _getControl(root, 'simmanage-auto-export')?.checked ?? current.autoExportOnComplete,
    selectedFormats,
    defaultSort: _getControl(root, 'simmanage-default-sort')?.value || current.defaultSort,
    minRatingFilter: Number.isFinite(minRating)
      ? Math.max(0, Math.min(5, minRating))
      : current.minRatingFilter,
  };
}

function _syncTaskListPreferenceControls(settings, { dispatchToolbarChange = false } = {}) {
  const desiredSort = settings?.defaultSort;
  const desiredMinRating = String(settings?.minRatingFilter ?? 0);
  const syncValue = (id, nextValue, shouldDispatch) => {
    const element = document.getElementById(id);
    if (!element || element.value === nextValue) {
      return;
    }
    element.value = nextValue;
    if (
      shouldDispatch &&
      typeof Event === 'function' &&
      typeof element.dispatchEvent === 'function'
    ) {
      element.dispatchEvent(new Event('change', { bubbles: true }));
    }
  };

  if (desiredSort) {
    syncValue('simmanage-default-sort', desiredSort, false);
    syncValue('simulation-jobs-sort', desiredSort, dispatchToolbarChange);
  }
  syncValue('simmanage-min-rating', desiredMinRating, false);
  syncValue('simulation-jobs-min-rating', desiredMinRating, dispatchToolbarChange);
}
