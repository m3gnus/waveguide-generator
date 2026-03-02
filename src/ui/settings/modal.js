/**
 * Settings modal — popup with sections for Viewer, Simulation Basic,
 * Simulation Advanced, and System.
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

/**
 * Open the settings modal. Creates it on-demand and appends to document.body.
 * Returns the backdrop element so callers can await removal if needed.
 */
export function openSettingsModal() {
  // Prevent duplicate modals
  const existing = document.getElementById('settings-modal-backdrop');
  if (existing) {
    existing.focus();
    return existing;
  }

  const backdrop = _buildModal();
  document.body.appendChild(backdrop);

  // Focus the dialog for keyboard access
  const dialog = backdrop.querySelector('[role="dialog"]');
  if (dialog) dialog.focus();

  return backdrop;
}

// ---------------------------------------------------------------------------
// Internal builders
// ---------------------------------------------------------------------------

function _buildModal() {
  const backdrop = document.createElement('div');
  backdrop.id = 'settings-modal-backdrop';
  backdrop.className = 'settings-modal-backdrop';

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

  const nav = _buildNav();
  const content = _buildContent();

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
  });

  // --- Close handlers ---
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener('keydown', onKeyDown);
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

function _buildNav() {
  const nav = document.createElement('nav');
  nav.className = 'settings-modal-nav';
  nav.setAttribute('aria-label', 'Settings sections');

  const items = [
    { key: 'viewer', label: 'Viewer' },
    { key: 'sim-basic', label: 'Simulation Basic' },
    { key: 'sim-advanced', label: 'Simulation Advanced' },
    { key: 'system', label: 'System' },
  ];

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

function _buildContent() {
  const content = document.createElement('div');
  content.className = 'settings-modal-content';

  content.appendChild(_buildViewerSection());
  content.appendChild(_buildSimBasicSection());
  content.appendChild(_buildSimAdvancedSection());
  content.appendChild(_buildSystemSection());

  return content;
}

// ---------------------------------------------------------------------------
// Section builders — controls are the actual interactive elements
// ---------------------------------------------------------------------------

function _buildViewerSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-viewer';
  sec.className = 'settings-section';
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(sec, 'Viewer', 'Viewport display and rendering preferences.');

  // Real-time Updates control
  _appendInlineRow(sec, {
    labelText: 'Real-time Updates',
    labelFor: 'live-update',
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
    controlHtml: `<select id="display-mode">${modeOptionsHtml}</select>`,
  });

  // --- Viewer sub-sections (Orbit Controls, Camera, Input) ---
  const currentSettings = getCurrentViewerSettings();

  // Mutable closure state — separate from the modal _state object
  let _viewerState = { ...currentSettings };

  // Live-apply helper: push _viewerState into OrbitControls and wheel zoom
  function _applyLive() {
    const controls = window.app && window.app.controls;
    applyViewerSettingsToControls(controls, _viewerState);
    const domEl = window.app && window.app.renderer && window.app.renderer.domElement;
    if (domEl) setInvertWheelZoom(domEl, _viewerState.invertWheelZoom);
  }

  // Helper: update badge visibility based on current vs recommended value
  function _updateBadge(badgeEl, currentValue, recommendedValue) {
    badgeEl.hidden = currentValue !== recommendedValue;
  }

  // ---------- SUB-SECTION: Orbit Controls ----------
  const orbitHeader = _buildSubSectionHeader('Orbit Controls', onResetOrbit);
  sec.appendChild(orbitHeader);

  const rotateResult = _buildSliderRow('Rotate Speed', 'rotateSpeed', 0.1, 5.0, 0.1, _viewerState);
  sec.appendChild(rotateResult.row);

  const zoomResult = _buildSliderRow('Zoom Speed', 'zoomSpeed', 0.1, 5.0, 0.1, _viewerState);
  sec.appendChild(zoomResult.row);

  const panResult = _buildSliderRow('Pan Speed', 'panSpeed', 0.1, 5.0, 0.1, _viewerState);
  sec.appendChild(panResult.row);

  // Damping enabled toggle
  const dampingEnabledResult = _buildToggleRow('Enable Damping', 'dampingEnabled', _viewerState);
  const dampingEnabledBadge = dampingEnabledResult.badge;
  const dampingToggle = dampingEnabledResult.checkbox;
  sec.appendChild(dampingEnabledResult.row);

  // Damping factor slider (hidden when damping disabled)
  const dampingFactorResult = _buildSliderRow('Damping Factor', 'dampingFactor', 0.01, 0.5, 0.01, _viewerState);
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

  const cameraLabel = document.createElement('label');
  cameraLabel.textContent = 'Startup Camera Mode';
  cameraRow.appendChild(cameraLabel);

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

  const invertWheelResult = _buildToggleRow('Invert Scroll Zoom', 'invertWheelZoom', _viewerState);
  sec.appendChild(invertWheelResult.row);

  const keyboardPanResult = _buildToggleRow('Keyboard Pan Shortcuts', 'keyboardPanEnabled', _viewerState);
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

  function _buildSliderRow(labelText, settingKey, min, max, step, currentSettingsSnapshot) {
    const row = document.createElement('div');
    row.className = 'settings-control-row';

    const inputId = `viewer-${settingKey}`;

    const label = document.createElement('label');
    label.setAttribute('for', inputId);
    label.textContent = labelText;
    row.appendChild(label);

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

  function _buildToggleRow(labelText, settingKey, currentSettingsSnapshot) {
    const row = document.createElement('div');
    row.className = 'settings-control-row';

    const inputId = `viewer-${settingKey}`;

    const label = document.createElement('label');
    label.setAttribute('for', inputId);
    label.textContent = labelText;
    row.appendChild(label);

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

function _buildSimBasicSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-sim-basic';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Simulation Basic',
    'BEM solver and meshing settings.'
  );

  // Keep existing "Download simulation mesh on start" checkbox
  _appendInlineRow(sec, {
    labelText: 'Download simulation mesh on start',
    labelFor: 'download-sim-mesh',
    controlHtml: `<input type="checkbox" id="download-sim-mesh"${_state.downloadSimMesh ? ' checked' : ''}>`,
  });

  // --- Solver Settings sub-section ---
  const currentSimBasic = getCurrentSimBasicSettings();

  // Sub-section header with Reset button
  const solverHeader = document.createElement('div');
  solverHeader.className = 'settings-subsection-header';

  const solverTitle = document.createElement('h4');
  solverTitle.className = 'settings-subsection-title';
  solverTitle.textContent = 'Solver Settings';
  solverHeader.appendChild(solverTitle);

  const resetBtn = document.createElement('button');
  resetBtn.type = 'button';
  resetBtn.className = 'settings-reset-btn';
  resetBtn.textContent = 'Reset';
  resetBtn.addEventListener('click', () => {
    resetSimBasicSettings();
    // Sync all Sim Basic DOM controls to RECOMMENDED_DEFAULTS
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
    // Update badge visibility
    if (dmBadge) dmBadge.hidden = true;
    if (mvmBadge) mvmBadge.hidden = true;
    if (fsBadge) fsBadge.hidden = true;
    if (uoBadge) uoBadge.hidden = true;
    if (esBadge) esBadge.hidden = true;
    if (vbBadge) vbBadge.hidden = true;
  });
  solverHeader.appendChild(resetBtn);
  sec.appendChild(solverHeader);

  // Helper: create a subtle "Default" badge
  function _makeDefaultBadge(currentValue, defaultValue) {
    const badge = document.createElement('span');
    badge.setAttribute('style', 'font-size:0.7rem;opacity:0.6;margin-left:6px;');
    badge.textContent = 'Default';
    badge.hidden = currentValue === defaultValue;
    return badge;
  }

  // Helper: build a select control row for Sim Basic
  function _buildSimBasicSelectRow(labelText, selectId, options, currentValue, defaultValue) {
    const row = document.createElement('div');
    row.className = 'settings-control-row';

    const label = document.createElement('label');
    label.setAttribute('for', selectId);
    label.textContent = labelText;
    row.appendChild(label);

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

  // Helper: build a checkbox control row for Sim Basic
  function _buildSimBasicCheckboxRow(labelText, checkboxId, currentValue, defaultValue) {
    const row = document.createElement('div');
    row.className = 'settings-control-row';

    const label = document.createElement('label');
    label.setAttribute('for', checkboxId);
    label.textContent = labelText;
    row.appendChild(label);

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

  // 1. Device Mode select
  const dmResult = _buildSimBasicSelectRow(
    'Device Mode',
    'simbasic-deviceMode',
    [
      { value: 'auto', label: 'Auto' },
      { value: 'opencl_gpu', label: 'OpenCL GPU' },
      { value: 'opencl_cpu', label: 'OpenCL CPU' },
    ],
    currentSimBasic.deviceMode,
    SIM_BASIC_DEFAULTS.deviceMode
  );
  sec.appendChild(dmResult.row);
  let dmBadge = dmResult.badge;

  // Inline device mode availability status span (starts empty, populated async)
  const dmStatusSpan = document.createElement('span');
  dmStatusSpan.id = 'simbasic-deviceMode-status';
  dmStatusSpan.setAttribute('style', 'font-size:0.7rem;opacity:0.6;display:block;margin-top:2px;');
  dmResult.row.appendChild(dmStatusSpan);

  // 2. Mesh Validation Mode select
  const mvmResult = _buildSimBasicSelectRow(
    'Mesh Validation',
    'simbasic-meshValidationMode',
    [
      { value: 'warn', label: 'Warn' },
      { value: 'strict', label: 'Strict' },
      { value: 'off', label: 'Off' },
    ],
    currentSimBasic.meshValidationMode,
    SIM_BASIC_DEFAULTS.meshValidationMode
  );
  sec.appendChild(mvmResult.row);
  let mvmBadge = mvmResult.badge;

  // 3. Frequency Spacing select
  const fsResult = _buildSimBasicSelectRow(
    'Frequency Spacing',
    'simbasic-frequencySpacing',
    [
      { value: 'log', label: 'Logarithmic' },
      { value: 'linear', label: 'Linear' },
    ],
    currentSimBasic.frequencySpacing,
    SIM_BASIC_DEFAULTS.frequencySpacing
  );
  sec.appendChild(fsResult.row);
  let fsBadge = fsResult.badge;

  // 4. Use Optimized checkbox
  const uoResult = _buildSimBasicCheckboxRow(
    'Use Optimized',
    'simbasic-useOptimized',
    currentSimBasic.useOptimized,
    SIM_BASIC_DEFAULTS.useOptimized
  );
  sec.appendChild(uoResult.row);
  let uoBadge = uoResult.badge;

  // 5. Enable Symmetry checkbox
  const esResult = _buildSimBasicCheckboxRow(
    'Enable Symmetry',
    'simbasic-enableSymmetry',
    currentSimBasic.enableSymmetry,
    SIM_BASIC_DEFAULTS.enableSymmetry
  );
  sec.appendChild(esResult.row);
  let esBadge = esResult.badge;

  // 6. Verbose Logging checkbox
  const vbResult = _buildSimBasicCheckboxRow(
    'Verbose Logging',
    'simbasic-verbose',
    currentSimBasic.verbose,
    SIM_BASIC_DEFAULTS.verbose
  );
  sec.appendChild(vbResult.row);
  let vbBadge = vbResult.badge;

  // Fire non-blocking device availability poll after section renders
  void _pollSimBasicDeviceAvailability();

  return sec;
}

function _buildSimAdvancedSection() {
  const sec = document.createElement('div');
  sec.id = 'settings-section-sim-advanced';
  sec.className = 'settings-section';
  sec.hidden = true;
  sec.setAttribute('role', 'tabpanel');

  _appendSectionHeading(
    sec,
    'Simulation Advanced',
    'Expert BEM solver tuning and mesh quality controls. Additional options available in future releases.'
  );

  const placeholder = document.createElement('p');
  placeholder.className = 'settings-placeholder-text';
  placeholder.textContent = 'Advanced solver controls will appear here in a future update.';
  sec.appendChild(placeholder);

  return sec;
}

function _buildSystemSection() {
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
  resetAllBtn.textContent = 'Reset All Settings to Defaults';
  resetAllRow.appendChild(resetAllBtn);

  const resetAllHelp = document.createElement('p');
  resetAllHelp.className = 'settings-action-help';
  resetAllHelp.textContent =
    'Restores all viewer controls to their recommended default values. Takes effect immediately.';
  resetAllRow.appendChild(resetAllHelp);

  resetAllBtn.addEventListener('click', () => {
    resetAllViewerSettings();
    applyViewerSettingsToControls(window.app?.controls, RECOMMENDED_DEFAULTS);
    const domEl = window.app?.renderer?.domElement;
    if (domEl) setInvertWheelZoom(domEl, RECOMMENDED_DEFAULTS.invertWheelZoom);
  });

  sec.appendChild(resetAllRow);

  return sec;
}

// ---------------------------------------------------------------------------
// Sim Basic device availability poll
// ---------------------------------------------------------------------------

/**
 * Non-blocking async health poll that marks unavailable device mode options
 * and populates the inline status element.
 *
 * Called fire-and-forget after _buildSimBasicSection renders.
 * Fails silently on any error — the UI remains fully functional with all
 * options enabled if the health check cannot be completed.
 */
async function _pollSimBasicDeviceAvailability() {
  const statusEl = document.getElementById('simbasic-deviceMode-status');
  const select = document.getElementById('simbasic-deviceMode');
  if (!statusEl || !select) return; // section not visible

  try {
    const res = await fetch('http://localhost:8000/health');
    if (!res.ok) throw new Error('health fetch failed');
    const health = await res.json();
    const di = health?.deviceInterface;

    if (!di || !di.mode_availability) {
      // Solver unavailable — mark concrete modes disabled
      for (const opt of select.options) {
        if (opt.value !== 'auto') {
          opt.disabled = true;
          opt.text = opt.text.replace(' (unavailable)', '') + ' (unavailable)';
        }
      }
      statusEl.textContent = 'Solver runtime unavailable. Auto mode only.';
      return;
    }

    // Mark per-mode availability
    let unavailableCount = 0;
    for (const opt of select.options) {
      const info = di.mode_availability[opt.value];
      if (info && !info.available) {
        opt.disabled = true;
        opt.text = opt.value === 'auto' ? opt.text : opt.text.replace(' (unavailable)', '') + ' (unavailable)';
        unavailableCount++;
      } else {
        opt.disabled = false;
        // Strip any previously-added suffix on refresh
        opt.text = opt.text.replace(' (unavailable)', '');
      }
    }

    // Populate inline status: show selected_mode for auto, or blank if all available
    if (di.selected_mode && di.selected_mode !== 'auto') {
      statusEl.textContent = `Auto resolves to: ${di.selected_mode}`;
    } else {
      statusEl.textContent = unavailableCount > 0 ? `${unavailableCount} mode(s) unavailable on this machine` : '';
    }

  } catch {
    // Health poll failed — fail silently, leave all options enabled
    statusEl.textContent = '';
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

function _appendInlineRow(parent, { labelText, labelFor, controlHtml }) {
  const row = document.createElement('div');
  row.className = 'settings-control-row';

  const label = document.createElement('label');
  label.setAttribute('for', labelFor);
  label.textContent = labelText;
  row.appendChild(label);

  const wrapper = document.createElement('div');
  wrapper.className = 'settings-control-value';
  wrapper.innerHTML = controlHtml;
  row.appendChild(wrapper);

  parent.appendChild(row);
}
