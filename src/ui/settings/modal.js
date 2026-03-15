/**
 * Settings modal — popup with grouped sections for viewer behavior,
 * simulation defaults, task exports, workspace routing, and system actions.
 *
 * Interaction style mirrors the View Results popup: backdrop click or ESC closes.
 */

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
  getBemPrecision,
  getCurrentSimAdvancedSettings,
  getEnableWarmup,
  getUseBurtonMiller,
  resetSimAdvancedSettings,
  saveSimAdvancedSettings,
} from './simAdvancedSettings.js';
import {
  SIMULATION_EXPORT_FORMAT_IDS,
  getCurrentSimulationManagementSettings,
  resetSimulationManagementSettings,
  saveSimulationManagementSettings,
} from './simulationManagementSettings.js';
import {
  describeSimBasicDeviceAvailability,
  fetchRuntimeHealth,
  getCachedRuntimeHealth,
  summarizeRuntimeCapabilities,
} from '../runtimeCapabilities.js';
import { createHelpTrigger } from '../helpAffordance.js';
import {
  getSelectedFolderLabel,
  requestFolderSelection,
  subscribeFolderWorkspace,
  supportsFolderSelection,
} from '../workspace/folderWorkspace.js';

export { describeSimBasicDeviceAvailability } from '../runtimeCapabilities.js';

// DOM IDs of controls that now live in Settings (used by events.js wiring)
export const SETTINGS_CONTROL_IDS = {
  liveUpdate: 'live-update',
  displayMode: 'display-mode',
  downloadSimMesh: 'download-sim-mesh',
  checkUpdates: 'check-updates-btn',
};

// In-memory settings state so preferences survive modal close/reopen
const _state = {
  liveUpdate: true,
  displayMode: 'standard',
  downloadSimMesh: false,
};
const SIMULATION_MANAGEMENT_HELP = Object.freeze({
  downloadMesh: 'Automatically downloads the solver mesh file (.msh) when a job starts.',
  defaultSort: 'Sets the default order used in the Simulation Jobs list.',
  minRatingFilter: 'Hides completed jobs rated below this threshold in the Simulation Jobs list.',
  autoExportOnComplete: 'Automatically exports results in the selected formats when a simulation completes.',
  selectedFormats: 'Selects which file formats are included when exporting results.'
});
const VIEWER_HELP = Object.freeze({
  liveUpdate: 'Applies geometry and viewport updates as soon as parameters change. Turn this off if you prefer to review changes manually before re-rendering.',
  displayMode: 'Switches the viewport shading mode used to inspect the current waveguide surface.',
  rotateSpeed: 'Controls how quickly the camera orbits the model while dragging.',
  zoomSpeed: 'Controls how quickly scroll and pinch gestures move the camera toward the model.',
  panSpeed: 'Controls how quickly the viewport shifts when you pan the camera.',
  dampingEnabled: 'Keeps orbit movement eased instead of stopping abruptly after drag input ends.',
  dampingFactor: 'Adjusts how quickly the eased orbit motion settles after input stops.',
  startupCameraMode: 'Sets which camera projection opens by default the next time the app starts.',
  invertWheelZoom: 'Reverses the mouse-wheel zoom direction for viewport navigation.',
  keyboardPanEnabled: 'Enables arrow-key style camera panning shortcuts while the viewport is focused.'
});
const SIMULATION_BASIC_HELP = Object.freeze({
  deviceMode: 'Selects the compute device for solving. Auto picks the best available option.',
  meshValidationMode: 'Controls whether mesh validation blocks or only warns before submitting a solve.',
  frequencySpacing: 'Controls how solved frequencies are distributed between the sweep start and end values.',
  useOptimized: 'Enables the faster solve path when available.',
  enableSymmetry: 'Reduces solve time by exploiting geometry symmetry when possible.',
  verbose: 'Shows detailed solver output in job progress and server logs.'
});
const SIMULATION_ADVANCED_HELP = Object.freeze({
  enableWarmup: 'Optimized solver only. Warms up operator and OpenCL caches before the frequency loop starts.',
  bemPrecision: 'Optimized solver only. Single precision is faster; double is more accurate.',
  useBurtonMiller: 'Optimized solver only. Keeps Burton-Miller formulation active for better high-frequency accuracy.',
});
const ADVANCED_CONTROL_COPY = Object.freeze({
  enable_warmup: {
    label: 'Warm-up Pass',
    help: 'Warms up OpenCL and operator caches before the frequency loop starts, for more consistent timing.'
  },
  bem_precision: {
    label: 'BEM Precision',
    help: 'Single precision is faster; double precision is more accurate. Only applies to the optimized solve path.'
  },
  method: {
    label: 'Linear Solver Method',
    help: 'Override the iterative solver method (e.g. GMRES). Not yet active.'
  },
  tol: {
    label: 'Linear Solver Tolerance',
    help: 'Override the convergence tolerance for iterative solving. Not yet active.'
  },
  restart: {
    label: 'GMRES Restart',
    help: 'Override the GMRES restart window size. Not yet active.'
  },
  maxiter: {
    label: 'Max Iterations',
    help: 'Override the maximum number of solver iterations. Not yet active.'
  },
  strong_form: {
    label: 'Strong-form Preconditioner',
    help: 'Override the preconditioner policy for solver tuning. Not yet active.'
  },
  use_burton_miller: {
    label: 'Burton-Miller Coupling',
    help: 'Uses the Burton-Miller formulation for better high-frequency accuracy and fewer spurious solutions.'
  },
});
const ACTIVE_ADVANCED_CONTROL_IDS = Object.freeze([
  'enable_warmup',
  'bem_precision',
  'use_burton_miller',
]);
const SETTINGS_SECTION_ITEMS = Object.freeze([
  { key: 'viewer', label: 'Viewer' },
  { key: 'simulation', label: 'Simulation' },
  { key: 'task-exports', label: 'Task Exports' },
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
  const el = document.getElementById('display-mode');
  if (el) return el.value;
  return _state.displayMode;
}

/**
 * Get the current download-sim-mesh preference.
 */
export function getDownloadSimMeshEnabled() {
  const el = document.getElementById('download-sim-mesh');
  if (el) return el.checked;
  return _state.downloadSimMesh;
}

function _getDocument() {
  return typeof document !== 'undefined' ? document : null;
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
    existing.focus();
    return existing;
  }

  const backdrop = _buildModal(viewerRuntime);
  document.body.appendChild(backdrop);

  // Focus the dialog for keyboard access
  const dialog = backdrop.querySelector('[role="dialog"]');
  if (dialog) dialog.focus();

  return backdrop;
}

// ---------------------------------------------------------------------------
// Internal builders
// ---------------------------------------------------------------------------

function _resolveViewerRuntime(runtime = {}) {
  return {
    getControls: typeof runtime?.getControls === 'function' ? runtime.getControls : () => null,
    getDomElement: typeof runtime?.getDomElement === 'function' ? runtime.getDomElement : () => null
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
    if (t.id === 'display-mode') _state.displayMode = t.value;
    if (t.id === 'download-sim-mesh') _state.downloadSimMesh = t.checked;

    // Sim Basic settings: save on any simbasic-* control change
    if (t.id && t.id.startsWith('simbasic-')) {
      const settings = getCurrentSimBasicSettings();
      settings.deviceMode = document.getElementById('simbasic-deviceMode')?.value ?? settings.deviceMode;
      settings.meshValidationMode = document.getElementById('simbasic-meshValidationMode')?.value ?? settings.meshValidationMode;
      settings.frequencySpacing = document.getElementById('simbasic-frequencySpacing')?.value ?? settings.frequencySpacing;
      settings.useOptimized = document.getElementById('simbasic-useOptimized')?.checked ?? settings.useOptimized;
      settings.enableSymmetry = document.getElementById('simbasic-enableSymmetry')?.checked ?? settings.enableSymmetry;
      settings.verbose = document.getElementById('simbasic-verbose')?.checked ?? settings.verbose;
      saveSimBasicSettings(settings);
    }

    if (t.id && t.id.startsWith('simadvanced-')) {
      const settings = getCurrentSimAdvancedSettings();
      settings.enableWarmup = getEnableWarmup();
      settings.bemPrecision = getBemPrecision();
      settings.useBurtonMiller = getUseBurtonMiller();
      saveSimAdvancedSettings(settings);
    }

    if (_isSimulationManagementControl(t)) {
      const settings = _readSimulationManagementSettings(backdrop);
      saveSimulationManagementSettings(settings);
      _syncTaskListPreferenceControls(settings, {
        dispatchToolbarChange: t.id === 'simmanage-default-sort' || t.id === 'simmanage-min-rating'
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

  return backdrop;
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
  content.appendChild(_buildSimulationSection());
  content.appendChild(_buildTaskExportsSection());
  content.appendChild(_buildWorkspaceSection(cleanupFns));
  content.appendChild(_buildSystemSection(viewerRuntime));

  void _refreshSimulationCapabilityState();

  return content;
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

  // Display Mode control
  const modeOptions = [
    { value: 'standard', label: 'Standard (Metal)' },
    { value: 'zebra', label: 'Zebra Stripes' },
    { value: 'grid', label: 'Grid / Wireframe' },
    { value: 'curvature', label: 'Curvature Map' },
  ];
  const modeOptionsHtml = modeOptions.map(
    (o) => `<option value="${o.value}"${_state.displayMode === o.value ? ' selected' : ''}>${o.label}</option>`
  ).join('');
  _appendInlineRow(sec, {
    labelText: 'Display Mode',
    labelFor: 'display-mode',
    helpText: VIEWER_HELP.displayMode,
    controlHtml: `<select id="display-mode">${modeOptionsHtml}</select>`,
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

  const rotateResult = _buildSliderRow('Rotate Speed', 'rotateSpeed', 0.1, 5.0, 0.1, _viewerState, VIEWER_HELP.rotateSpeed);
  sec.appendChild(rotateResult.row);

  const zoomResult = _buildSliderRow('Zoom Speed', 'zoomSpeed', 0.1, 5.0, 0.1, _viewerState, VIEWER_HELP.zoomSpeed);
  sec.appendChild(zoomResult.row);

  const panResult = _buildSliderRow('Pan Speed', 'panSpeed', 0.1, 5.0, 0.1, _viewerState, VIEWER_HELP.panSpeed);
  sec.appendChild(panResult.row);

  // Damping enabled toggle
  const dampingEnabledResult = _buildToggleRow('Enable Damping', 'dampingEnabled', _viewerState, VIEWER_HELP.dampingEnabled);
  const dampingEnabledBadge = dampingEnabledResult.badge;
  const dampingToggle = dampingEnabledResult.checkbox;
  sec.appendChild(dampingEnabledResult.row);

  // Damping factor slider (hidden when damping disabled)
  const dampingFactorResult = _buildSliderRow('Damping Factor', 'dampingFactor', 0.01, 0.5, 0.01, _viewerState, VIEWER_HELP.dampingFactor);
  dampingFactorResult.row.hidden = !_viewerState.dampingEnabled;
  sec.appendChild(dampingFactorResult.row);

  // Override the default dampingEnabled change handler (need to show/hide dampingFactor row)
  // Remove default handler by replacing the element (simpler: add the special handler here)
  dampingToggle.addEventListener('change', (e) => {
    _viewerState.dampingEnabled = e.target.checked;
    dampingFactorResult.row.hidden = !e.target.checked;
    _updateBadge(dampingEnabledBadge, e.target.checked, RECOMMENDED_DEFAULTS.dampingEnabled);
    debouncedSaveViewerSettings(_viewerState);
    _applyLive();
  });

  // ---------- SUB-SECTION: Camera ----------
  const cameraHeader = _buildSubSectionHeader('Camera', onResetCamera);
  sec.appendChild(cameraHeader);

  // Startup Camera Mode radio row
  const cameraRow = document.createElement('div');
  cameraRow.className = 'settings-control-row';

  cameraRow.appendChild(_buildSettingsLabelCopy('Startup Camera Mode', '', VIEWER_HELP.startupCameraMode));

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

  const invertWheelResult = _buildToggleRow('Invert Scroll Zoom', 'invertWheelZoom', _viewerState, VIEWER_HELP.invertWheelZoom);
  sec.appendChild(invertWheelResult.row);

  const keyboardPanResult = _buildToggleRow('Keyboard Pan Shortcuts', 'keyboardPanEnabled', _viewerState, VIEWER_HELP.keyboardPanEnabled);
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
    _updateBadge(dampingEnabledBadge, _viewerState.dampingEnabled, RECOMMENDED_DEFAULTS.dampingEnabled);
    dampingFactorResult.row.hidden = !_viewerState.dampingEnabled;

    // Update dampingFactor slider
    _syncSliderRow(dampingFactorResult, _viewerState.dampingFactor, RECOMMENDED_DEFAULTS.dampingFactor);

    _applyLive();
  }

  function onResetCamera() {
    const newSettings = resetViewerSection('camera');
    _viewerState = { ..._viewerState, ...newSettings };

    // Update radio buttons
    if (perspectiveRadio) perspectiveRadio.checked = _viewerState.startupCameraMode === 'perspective';
    if (orthographicRadio) orthographicRadio.checked = _viewerState.startupCameraMode === 'orthographic';
    _updateBadge(cameraBadge, _viewerState.startupCameraMode, RECOMMENDED_DEFAULTS.startupCameraMode);
    // NOTE: does NOT call _applyLive() — startup camera mode takes effect on next launch
  }

  function onResetInput() {
    const newSettings = resetViewerSection('input');
    _viewerState = { ..._viewerState, ...newSettings };

    // Update invertWheelZoom toggle
    invertWheelResult.checkbox.checked = _viewerState.invertWheelZoom;
    _updateBadge(invertWheelResult.badge, _viewerState.invertWheelZoom, RECOMMENDED_DEFAULTS.invertWheelZoom);

    // Update keyboardPanEnabled toggle
    keyboardPanResult.checkbox.checked = _viewerState.keyboardPanEnabled;
    _updateBadge(keyboardPanResult.badge, _viewerState.keyboardPanEnabled, RECOMMENDED_DEFAULTS.keyboardPanEnabled);

    _applyLive();
  }

  // ---------- Private helpers (closures over _viewerState) ----------

  function _buildSubSectionHeader(titleText, onReset) {
    const hdr = document.createElement('div');
    hdr.className = 'settings-subsection-header';

    const h4 = document.createElement('h4');
    h4.className = 'settings-subsection-title';
    h4.textContent = titleText;
    hdr.appendChild(h4);

    const resetBtn = document.createElement('button');
    resetBtn.type = 'button';
    resetBtn.className = 'settings-reset-btn';
    resetBtn.textContent = 'Reset';
    resetBtn.addEventListener('click', onReset);
    hdr.appendChild(resetBtn);

    return hdr;
  }

  function _buildSliderRow(labelText, settingKey, min, max, step, currentSettingsSnapshot, helpText = '') {
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

  function _buildToggleRow(labelText, settingKey, currentSettingsSnapshot, helpText = '') {
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

    checkbox.addEventListener('change', (e) => {
      _viewerState[settingKey] = e.target.checked;
      _updateBadge(badge, e.target.checked, RECOMMENDED_DEFAULTS[settingKey]);
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
    'Persistent solve defaults live here. Advanced controls apply to the optimized solver path, while GMRES precision work stays separated until product defines it.'
  );

  const currentSimBasic = getCurrentSimBasicSettings();
  const solverHeader = _buildSubSectionHeader('Solve Defaults', () => {
    resetSimBasicSettings();
    const dm = document.getElementById('simbasic-deviceMode');
    if (dm) dm.value = SIM_BASIC_DEFAULTS.deviceMode;
    const mvm = document.getElementById('simbasic-meshValidationMode');
    if (mvm) mvm.value = SIM_BASIC_DEFAULTS.meshValidationMode;
    const fs = document.getElementById('simbasic-frequencySpacing');
    if (fs) fs.value = SIM_BASIC_DEFAULTS.frequencySpacing;
    const uo = document.getElementById('simbasic-useOptimized');
    if (uo) uo.checked = SIM_BASIC_DEFAULTS.useOptimized;
    const es = document.getElementById('simbasic-enableSymmetry');
    if (es) es.checked = SIM_BASIC_DEFAULTS.enableSymmetry;
    const vb = document.getElementById('simbasic-verbose');
    if (vb) vb.checked = SIM_BASIC_DEFAULTS.verbose;
    if (dmBadge) dmBadge.hidden = true;
    if (mvmBadge) mvmBadge.hidden = true;
    if (fsBadge) fsBadge.hidden = true;
    if (uoBadge) uoBadge.hidden = true;
    if (esBadge) esBadge.hidden = true;
    if (vbBadge) vbBadge.hidden = true;
  });
  sec.appendChild(solverHeader);

  const dmResult = _buildSimBasicSelectRow(
    'Compute Device',
    'simbasic-deviceMode',
    [
      { value: 'auto', label: 'Auto' },
      { value: 'opencl_gpu', label: 'OpenCL GPU' },
      { value: 'opencl_cpu', label: 'OpenCL CPU' },
    ],
    currentSimBasic.deviceMode,
    SIM_BASIC_DEFAULTS.deviceMode,
    SIMULATION_BASIC_HELP.deviceMode
  );
  sec.appendChild(dmResult.row);
  let dmBadge = dmResult.badge;

  const dmStatusSpan = document.createElement('span');
  dmStatusSpan.id = 'simbasic-deviceMode-status';
  dmStatusSpan.setAttribute('style', 'font-size:0.7rem;opacity:0.6;display:block;margin-top:2px;');
  dmResult.row.appendChild(dmStatusSpan);

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

  const uoResult = _buildSimBasicCheckboxRow(
    'Use Optimized Solver Path',
    'simbasic-useOptimized',
    currentSimBasic.useOptimized,
    SIM_BASIC_DEFAULTS.useOptimized,
    SIMULATION_BASIC_HELP.useOptimized
  );
  sec.appendChild(uoResult.row);
  let uoBadge = uoResult.badge;

  const esResult = _buildSimBasicCheckboxRow(
    'Allow Symmetry Reduction',
    'simbasic-enableSymmetry',
    currentSimBasic.enableSymmetry,
    SIM_BASIC_DEFAULTS.enableSymmetry,
    SIMULATION_BASIC_HELP.enableSymmetry
  );
  sec.appendChild(esResult.row);
  let esBadge = esResult.badge;

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
  const advancedIntro = document.createElement('p');
  advancedIntro.className = 'settings-section-help';
  advancedIntro.textContent = 'These settings are sent through the public solve contract today. GMRES method, restart, tolerance, max-iteration, and explicit strong-form policy remain planned-only.';
  sec.appendChild(advancedIntro);

  const advancedActiveHeader = _buildSubSectionHeader('Active Contract Overrides', () => {
    const resetSettings = resetSimAdvancedSettings();
    const ew = document.getElementById('simadvanced-enableWarmup');
    if (ew) ew.checked = resetSettings.enableWarmup;
    const bp = document.getElementById('simadvanced-bemPrecision');
    if (bp) bp.value = resetSettings.bemPrecision;
    const ubm = document.getElementById('simadvanced-useBurtonMiller');
    if (ubm) ubm.checked = resetSettings.useBurtonMiller;
    if (ewBadge) ewBadge.hidden = false;
    if (bpBadge) bpBadge.hidden = false;
    if (ubmBadge) ubmBadge.hidden = false;
  });
  sec.appendChild(advancedActiveHeader);

  const ewResult = _buildSimBasicCheckboxRow(
    ADVANCED_CONTROL_COPY.enable_warmup.label,
    'simadvanced-enableWarmup',
    currentSimAdvanced.enableWarmup,
    SIM_ADVANCED_DEFAULTS.enableWarmup,
    SIMULATION_ADVANCED_HELP.enableWarmup
  );
  sec.appendChild(ewResult.row);
  let ewBadge = ewResult.badge;

  const bpResult = _buildSimBasicSelectRow(
    ADVANCED_CONTROL_COPY.bem_precision.label,
    'simadvanced-bemPrecision',
    [
      { value: 'double', label: 'Double' },
      { value: 'single', label: 'Single' },
    ],
    currentSimAdvanced.bemPrecision,
    SIM_ADVANCED_DEFAULTS.bemPrecision,
    SIMULATION_ADVANCED_HELP.bemPrecision
  );
  sec.appendChild(bpResult.row);
  let bpBadge = bpResult.badge;

  const ubmResult = _buildSimBasicCheckboxRow(
    ADVANCED_CONTROL_COPY.use_burton_miller.label,
    'simadvanced-useBurtonMiller',
    currentSimAdvanced.useBurtonMiller,
    SIM_ADVANCED_DEFAULTS.useBurtonMiller,
    SIMULATION_ADVANCED_HELP.useBurtonMiller
  );
  sec.appendChild(ubmResult.row);
  let ubmBadge = ubmResult.badge;

  const status = document.createElement('p');
  status.id = 'simadvanced-capability-status';
  status.className = 'settings-placeholder-text';
  status.textContent = 'Checking backend capability...';
  sec.appendChild(status);

  const plannedHeader = _buildSubSectionHeader('Still Planned');
  sec.appendChild(plannedHeader);

  const advancedControls = document.createElement('div');
  advancedControls.id = 'simadvanced-planned-controls';
  sec.appendChild(advancedControls);
  _renderSimAdvancedControls(advancedControls);

  const cachedHealth = getCachedRuntimeHealth();
  if (cachedHealth) {
    _applySimBasicDeviceAvailability(cachedHealth);
    _applySimAdvancedCapabilityState(cachedHealth);
  }

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
    'Task Exports',
    'Job-list preferences, automatic result bundles, and optional mesh artifact downloads all live together here.'
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
      { value: 'label_asc', label: 'Label A-Z' }
    ])
  });

  _appendInlineRow(sec, {
    labelText: 'Minimum Rating Filter',
    labelFor: 'simmanage-min-rating',
    helpText: SIMULATION_MANAGEMENT_HELP.minRatingFilter,
    controlNode: _buildSelectElement('simmanage-min-rating', String(managementSettings.minRatingFilter), [
      { value: '0', label: 'All Ratings' },
      { value: '1', label: '1 star or higher' },
      { value: '2', label: '2 stars or higher' },
      { value: '3', label: '3 stars or higher' },
      { value: '4', label: '4 stars or higher' },
      { value: '5', label: '5 stars only' }
    ])
  });

  const exportHeader = _buildSubSectionHeader('Completed Task Bundles', () => {
    const resetSettings = resetSimulationManagementSettings();
    const autoExport = document.getElementById('simmanage-auto-export');
    if (autoExport) {
      autoExport.checked = resetSettings.autoExportOnComplete;
    }
    Array.from(sec.querySelectorAll('input[data-sim-management-format]')).forEach((input) => {
      const formatId = input.getAttribute('data-sim-management-format');
      input.checked = resetSettings.selectedFormats.includes(formatId);
    });
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

  _appendInlineRow(sec, {
    labelText: 'Auto-export completed task bundle',
    labelFor: 'simmanage-auto-export',
    helpText: SIMULATION_MANAGEMENT_HELP.autoExportOnComplete,
    controlHtml: `<input type="checkbox" id="simmanage-auto-export"${managementSettings.autoExportOnComplete ? ' checked' : ''}>`,
  });

  sec.appendChild(_buildSimulationExportFormatsRow(managementSettings));

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
  statusRow.appendChild(_buildSettingsLabelCopy(
    'Selected Folder',
    '',
    'Manual exports write to the selected folder root when available, while completed simulation bundles write into a job-specific subfolder.'
  ));
  const statusValue = document.createElement('div');
  statusValue.className = 'settings-control-value';
  const statusText = document.createElement('span');
  statusText.id = 'settings-workspace-folder-label';
  statusValue.appendChild(statusText);
  statusRow.appendChild(statusValue);
  sec.appendChild(statusRow);

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

  const refreshWorkspaceCopy = () => {
    const canPickFolder = supportsFolderSelection(globalThis?.window);
    const selectedLabel = getSelectedFolderLabel();
    statusText.textContent = selectedLabel;
    chooseBtn.textContent = selectedLabel === 'No folder selected' ? 'Choose Folder' : 'Change Folder';
    chooseBtn.disabled = !canPickFolder;
    chooseHelp.textContent = canPickFolder
      ? 'Choose a folder workspace here if you want manual exports and completed task bundles to land in a stable location instead of the save picker.'
      : 'Folder workspaces are unavailable in this browser/context. They require File System Access support on HTTPS or localhost. Manual exports and task bundles will continue to use the save picker or download fallback.';
    routingNote.textContent = canPickFolder
      ? 'Routing: manual exports write to the selected folder root when permission is available, and completed simulation bundles write into <workspace>/<jobId>/. Folder task manifests/index persist there for history, but the workspace is not a catch-all redirect for every generated artifact. If direct writes fail, the app clears the workspace and falls back to standard save/download behavior.'
      : 'Routing fallback: without folder workspace support, manual exports and completed simulation bundles use the browser save/download path instead of workspace writes.';
  };

  chooseBtn.addEventListener('click', async () => {
    if (!supportsFolderSelection(globalThis?.window)) {
      refreshWorkspaceCopy();
      return;
    }
    await requestFolderSelection(globalThis?.window);
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

function _buildSimBasicSelectRow(labelText, selectId, options, currentValue, defaultValue, helpText = '') {
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

function _buildSimBasicCheckboxRow(labelText, checkboxId, currentValue, defaultValue, helpText = '') {
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

function _buildSimAdvancedNumberRow(
  labelText,
  inputId,
  currentValue,
  defaultValue,
  { min = '', max = '', step = '0.0001' } = {},
  helpText = ''
) {
  const row = document.createElement('div');
  row.className = 'settings-control-row';
  row.appendChild(_buildSettingsLabelCopy(labelText, inputId, helpText));

  const valueWrapper = document.createElement('div');
  valueWrapper.className = 'settings-control-value';

  const input = document.createElement('input');
  input.type = 'number';
  input.id = inputId;
  input.value = String(currentValue);
  if (min) input.min = min;
  if (max) input.max = max;
  input.step = step;

  const badge = _makeDefaultBadge(currentValue, defaultValue);
  input.addEventListener('change', () => {
    const numeric = Number(input.value);
    if (!Number.isFinite(numeric) || numeric <= 0) {
      input.value = String(defaultValue);
      badge.hidden = false;
      return;
    }
    badge.hidden = numeric === defaultValue;
  });

  valueWrapper.appendChild(input);
  valueWrapper.appendChild(badge);
  row.appendChild(valueWrapper);

  return { row, badge, input };
}

function _buildSimulationExportFormatsRow(managementSettings) {
  const exportFormatsRow = document.createElement('div');
  exportFormatsRow.className = 'settings-control-row';
  exportFormatsRow.appendChild(_buildSettingsLabelCopy('Bundle Formats', '', SIMULATION_MANAGEMENT_HELP.selectedFormats));

  const exportFormatsValue = document.createElement('div');
  exportFormatsValue.className = 'settings-control-value';
  exportFormatsValue.setAttribute('style', 'display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));gap:8px 12px;align-items:start;');

  const exportFormatLabels = new Map([
    ['png', 'Chart Images (PNG)'],
    ['csv', 'Frequency Data CSV'],
    ['json', 'Full Results JSON'],
    ['txt', 'Summary Text Report'],
    ['polar_csv', 'Polar Directivity CSV'],
    ['impedance_csv', 'Impedance CSV'],
    ['vacs', 'ABEC Spectrum (VACS)'],
    ['stl', 'Waveguide STL'],
    ['fusion_csv', 'Fusion 360 CSV Curves']
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

function _renderSimAdvancedControls(
  container,
  plannedControls = Object.keys(ADVANCED_CONTROL_COPY).filter(
    (controlId) => !ACTIVE_ADVANCED_CONTROL_IDS.includes(controlId)
  )
) {
  if (!container) return;
  container.innerHTML = '';

  plannedControls.forEach((controlId) => {
    const copy = ADVANCED_CONTROL_COPY[controlId];
    if (!copy) return;

    const row = document.createElement('div');
    row.className = 'settings-action-row';

    const control = document.createElement('input');
    control.type = 'text';
    control.disabled = true;
    control.value = 'Backend capability required';
    row.appendChild(control);

    const label = document.createElement('p');
    label.className = 'settings-action-help';
    label.textContent = `${copy.label}: ${copy.help}`;
    row.appendChild(label);

    container.appendChild(row);
  });
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

function _getSelectOptions(select) {
  if (!select) return [];
  if (select.options && typeof select.options[Symbol.iterator] === 'function') {
    return Array.from(select.options);
  }
  return Array.isArray(select._children) ? select._children : [];
}

function _setOptionLabel(option, label) {
  if (!option) return;
  if ('textContent' in option) {
    option.textContent = label;
    return;
  }
  option.text = label;
}

function _applySimBasicDeviceAvailability(health) {
  const doc = _getDocument();
  if (!doc) return;

  const statusEl = doc.getElementById('simbasic-deviceMode-status');
  const select = doc.getElementById('simbasic-deviceMode');
  if (!statusEl || !select) return;

  const availability = describeSimBasicDeviceAvailability(health, select.value);
  for (const opt of _getSelectOptions(select)) {
    const isUnavailable = availability.unavailableModes.includes(opt.value);
    opt.disabled = isUnavailable;
    const baseLabel = String(opt.textContent || opt.text || '').replace(' (unavailable)', '');
    _setOptionLabel(opt, isUnavailable && opt.value !== 'auto' ? `${baseLabel} (unavailable)` : baseLabel);
  }
  statusEl.textContent = availability.statusText;
}

function _applySimAdvancedCapabilityState(health) {
  const doc = _getDocument();
  if (!doc) return;

  const statusEl = doc.getElementById('simadvanced-capability-status');
  const controlsEl = doc.getElementById('simadvanced-planned-controls');
  if (!statusEl) return;

  const runtime = summarizeRuntimeCapabilities(health);
  if (controlsEl) {
    _renderSimAdvancedControls(
      controlsEl,
      runtime.simulationAdvanced.plannedControls.length > 0
        ? runtime.simulationAdvanced.plannedControls
        : Object.keys(ADVANCED_CONTROL_COPY).filter(
          (controlId) => !ACTIVE_ADVANCED_CONTROL_IDS.includes(controlId)
        )
    );
  }
  statusEl.textContent = runtime.simulationAdvanced.available
    ? (
      runtime.simulationAdvanced.controls.length > 0
        ? `Backend exposes: ${runtime.simulationAdvanced.controls.join(', ')}. ${runtime.simulationAdvanced.reason}`
        : runtime.simulationAdvanced.reason
    )
    : runtime.simulationAdvanced.reason;
}

async function _refreshSimulationCapabilityState() {
  try {
    const health = await fetchRuntimeHealth();
    _applySimBasicDeviceAvailability(health);
    _applySimAdvancedCapabilityState(health);
  } catch {
    const doc = _getDocument();
    const statusEl = doc?.getElementById('simbasic-deviceMode-status');
    if (statusEl) {
      statusEl.textContent = '';
    }
    _applySimAdvancedCapabilityState(null);
  }
}

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

function _appendInlineRow(parent, { labelText, labelFor, controlHtml = '', controlNode = null, helpText = '' }) {
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
  copy.appendChild(label);

  const helpTrigger = createHelpTrigger(document, { labelText, helpText });
  if (helpTrigger) {
    copy.appendChild(helpTrigger);
  }

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
    target?.id === 'simmanage-auto-export'
    || target?.id === 'simmanage-default-sort'
    || target?.id === 'simmanage-min-rating'
    || target?.getAttribute?.('data-sim-management-format')
  );
}

function _readSimulationManagementSettings(root) {
  const current = getCurrentSimulationManagementSettings();
  const selectedFormats = Array.from(root.querySelectorAll('input[data-sim-management-format]'))
    .filter((input) => input.checked)
    .map((input) => input.getAttribute('data-sim-management-format'))
    .filter(Boolean);
  const minRating = Number(document.getElementById('simmanage-min-rating')?.value);

  return {
    ...current,
    autoExportOnComplete: document.getElementById('simmanage-auto-export')?.checked
      ?? current.autoExportOnComplete,
    selectedFormats,
    defaultSort: document.getElementById('simmanage-default-sort')?.value || current.defaultSort,
    minRatingFilter: Number.isFinite(minRating)
      ? Math.max(0, Math.min(5, minRating))
      : current.minRatingFilter
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
    if (shouldDispatch && typeof Event === 'function' && typeof element.dispatchEvent === 'function') {
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
