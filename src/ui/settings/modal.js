/**
 * Settings modal — popup with grouped sections for viewer behavior,
 * simulation defaults, task exports, workspace routing, and system actions.
 *
 * Interaction style mirrors the View Results popup: backdrop click or ESC closes.
 */

import { trapFocus } from "../focusTrap.js";

import {
  RECOMMENDED_DEFAULTS,
  getCurrentViewerSettings,
  debouncedSaveViewerSettings,
  applyViewerSettingsToControls,
  setInvertWheelZoom,
  resetViewerSection,
  resetAllViewerSettings,
} from "./viewerSettings.js";

import {
  RECOMMENDED_DEFAULTS as SIM_BASIC_DEFAULTS,
  getCurrentSimBasicSettings,
  saveSimBasicSettings,
  resetSimBasicSettings,
} from "./simBasicSettings.js";
import {
  RECOMMENDED_DEFAULTS as SIM_ADVANCED_DEFAULTS,
  getCurrentSimAdvancedSettings,
  getUseBurtonMiller,
  getQuadratureRegular,
  getWorkgroupSizeMultiple,
  getAssemblyBackend,
  resetSimAdvancedSettings,
  saveSimAdvancedSettings,
} from "./simAdvancedSettings.js";
import {
  SIMULATION_EXPORT_FORMAT_IDS,
  getCurrentSimulationManagementSettings,
  resetSimulationManagementSettings,
  saveSimulationManagementSettings,
} from "./simulationManagementSettings.js";

import {
  getSelectedFolderLabel,
  requestBackendFolderSelection,
  subscribeFolderWorkspace,
  fetchWorkspacePath,
  openWorkspaceInFinder,
} from "../workspace/folderWorkspace.js";

// DOM IDs of controls that now live in Settings (used by events.js wiring)
export const SETTINGS_CONTROL_IDS = {
  liveUpdate: "live-update",

  downloadSimMesh: "download-sim-mesh",
  checkUpdates: "check-updates-btn",
};

// In-memory settings state so preferences survive modal close/reopen
const _state = {
  liveUpdate: true,
  displayMode: "clay",
  downloadSimMesh: false,
};
const SIMULATION_MANAGEMENT_HELP = Object.freeze({
  downloadMesh:
    "Automatically downloads the solver mesh file (.msh) when a job starts.",
  defaultSort: "Sets the default order used in the Simulation Jobs list.",
  minRatingFilter:
    "Hides completed jobs rated below this threshold in the Simulation Jobs list.",
  autoExportOnComplete:
    "Automatically exports results in the selected formats when a simulation completes.",
  selectedFormats:
    "Selects which file formats are included when exporting results.",
});
const VIEWER_HELP = Object.freeze({
  liveUpdate:
    "Applies geometry and viewport updates as soon as parameters change. Turn this off if you prefer to review changes manually before re-rendering.",
  rotateSpeed:
    "Controls how quickly the camera orbits the model while dragging.",
  zoomSpeed:
    "Controls how quickly scroll and pinch gestures move the camera toward the model.",
  panSpeed: "Controls how quickly the viewport shifts when you pan the camera.",
  dampingEnabled:
    "Keeps orbit movement eased instead of stopping abruptly after drag input ends.",
  dampingFactor:
    "Adjusts how quickly the eased orbit motion settles after input stops.",
  startupCameraMode:
    "Sets which camera projection opens by default the next time the app starts.",
  invertWheelZoom:
    "Reverses the mouse-wheel zoom direction for viewport navigation.",
  keyboardPanEnabled:
    "Enables arrow-key style camera panning shortcuts while the viewport is focused.",
});
const SIMULATION_BASIC_HELP = Object.freeze({
  meshValidationMode:
    "Controls what happens when the mesh may be too coarse for the requested frequency range. Warn (default) flags issues but lets the solve proceed. Strict aborts the solve on a mesh warning. Off skips validation entirely. Recommended default: Warn.",
  frequencySpacing:
    "Determines how the N frequency points are placed between the start and end frequency. Log spaces them evenly on a logarithmic scale (equal ratios between steps — perceptually uniform for audio). Linear spaces them evenly in Hz. Recommended default: Log.",
  verbose:
    "Emits per-frequency solver progress and diagnostic messages to the server log and job status stream. Useful for monitoring long sweeps or diagnosing convergence issues; adds minor overhead. Recommended default: Off.",
});
const SIMULATION_ADVANCED_HELP = Object.freeze({
  useBurtonMiller:
    "Eliminates fictitious interior resonances by adding hypersingular operator coupling to the BEM formulation. " +
    "OFF: ~1.8x faster (assembles 2 operators instead of 4 per frequency). " +
    "Risk: SPL deviations of up to 7 dB at frequencies where mesh interior resonances occur. " +
    "ON: Accurate and artifact-free, recommended for final/publication solves. " +
    "Default: Off (fast exploratory mode).",
  quadratureRegular:
    "Gauss quadrature points per element pair for regular integrals. Directly affects operator assembly time. " +
    "4: bempp default, highest accuracy. " +
    "3: ~1.25x faster, good balance for most work. " +
    "2: ~1.35x faster, noticeable accuracy loss at high frequencies. " +
    "Set to 4 for final solves. Default: 3 (fast mode).",
  workgroupSizeMultiple:
    "OpenCL work-group sizing factor for kernel launches. " +
    "1: 30\u201350% faster on CPU-only runtimes (pocl on Apple Silicon, Intel OpenCL on AMD CPUs). " +
    "2: bempp default, tuned for discrete GPUs. " +
    "No effect on accuracy. Always use 1 on CPU. Default: 1.",
  assemblyBackend:
    "Compute backend for operator assembly. " +
    "OpenCL: Parallel kernels via pocl (CPU) or GPU driver. Slightly faster with Burton-Miller on. " +
    "Numba: JIT-compiled via LLVM. Competitive with OpenCL on CPU, no driver dependencies. " +
    "Try both \u2014 performance varies by system. Default: OpenCL.",
});
const ADVANCED_CONTROL_COPY = Object.freeze({
  use_burton_miller: { label: "Burton-Miller Coupling" },
  quadrature_regular: { label: "Quadrature Order (Regular)" },
  workgroup_size_multiple: { label: "OpenCL Workgroup Size" },
  assembly_backend: { label: "Assembly Backend" },
});
const SETTINGS_SECTION_ITEMS = Object.freeze([
  { key: "viewer", label: "Viewer" },
  { key: "simulation", label: "Simulation" },
  { key: "task-exports", label: "Task Exports" },
  { key: "workspace", label: "Workspace" },
  { key: "system", label: "System" },
]);

/**
 * Get the current live-update preference.
 * Returns the DOM value when modal is open, otherwise the stored value.
 */
export function getLiveUpdateEnabled() {
  const el = document.getElementById("live-update");
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
  const el = document.getElementById("download-sim-mesh");
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
  const existing = document.getElementById("settings-modal-backdrop");
  if (existing) {
    const dialog = existing.querySelector('[role="dialog"]');
    if (dialog) dialog.focus();
    return existing;
  }

  const { backdrop, cleanup } = _buildModal(viewerRuntime);
  document.body.appendChild(backdrop);

  const dialog = backdrop.querySelector('[role="dialog"]');
  const closeBtn = backdrop.querySelector(".settings-modal-close");
  const releaseFocus = trapFocus(dialog, { initialFocus: closeBtn });
  cleanup.push(releaseFocus);

  return backdrop;
}

// ---------------------------------------------------------------------------
// Internal builders
// ---------------------------------------------------------------------------

function _resolveViewerRuntime(runtime = {}) {
  return {
    getControls:
      typeof runtime?.getControls === "function"
        ? runtime.getControls
        : () => null,
    getDomElement:
      typeof runtime?.getDomElement === "function"
        ? runtime.getDomElement
        : () => null,
  };
}

function _buildModal(viewerRuntime) {
  const backdrop = document.createElement("div");
  backdrop.id = "settings-modal-backdrop";
  backdrop.className = "settings-modal-backdrop";
  const cleanupFns = [];

  const dialog = document.createElement("div");
  dialog.className = "settings-modal-dialog";
  dialog.setAttribute("role", "dialog");
  dialog.setAttribute("aria-modal", "true");
  dialog.setAttribute("aria-label", "Settings");
  dialog.setAttribute("tabindex", "-1");

  // Header
  const header = document.createElement("div");
  header.className = "settings-modal-header";

  const title = document.createElement("h2");
  title.className = "settings-modal-title";
  title.textContent = "Settings";
  header.appendChild(title);

  const closeBtn = document.createElement("button");
  closeBtn.type = "button";
  closeBtn.className = "settings-modal-close";
  closeBtn.textContent = "\u00d7";
  closeBtn.title = "Close (Escape)";
  closeBtn.setAttribute("aria-label", "Close settings");
  header.appendChild(closeBtn);

  dialog.appendChild(header);

  // Body: sidebar nav + content area
  const body = document.createElement("div");
  body.className = "settings-modal-body";

  const nav = _buildNav(SETTINGS_SECTION_ITEMS);
  const content = _buildContent(viewerRuntime, cleanupFns);

  body.appendChild(nav);
  body.appendChild(content);
  dialog.appendChild(body);
  backdrop.appendChild(dialog);

  // --- Persist state changes from within the modal ---
  backdrop.addEventListener("change", (event) => {
    const t = event.target;
    if (!t) return;
    if (t.id === "live-update") _state.liveUpdate = t.checked;
    if (t.id === "download-sim-mesh") _state.downloadSimMesh = t.checked;

    // Sim Basic settings: save on any simbasic-* control change
    if (t.id && t.id.startsWith("simbasic-")) {
      const settings = getCurrentSimBasicSettings();
      settings.meshValidationMode =
        document.getElementById("simbasic-meshValidationMode")?.value ??
        settings.meshValidationMode;
      settings.frequencySpacing =
        document.getElementById("simbasic-frequencySpacing")?.value ??
        settings.frequencySpacing;
      settings.verbose =
        document.getElementById("simbasic-verbose")?.checked ??
        settings.verbose;
      saveSimBasicSettings(settings);
    }

    if (t.id && t.id.startsWith("simadvanced-")) {
      const settings = getCurrentSimAdvancedSettings();
      settings.useBurtonMiller = getUseBurtonMiller();
      settings.quadratureRegular = getQuadratureRegular();
      settings.workgroupSizeMultiple = getWorkgroupSizeMultiple();
      settings.assemblyBackend = getAssemblyBackend();
      saveSimAdvancedSettings(settings);
    }

    if (_isSimulationManagementControl(t)) {
      const settings = _readSimulationManagementSettings(backdrop);
      saveSimulationManagementSettings(settings);
      _syncTaskListPreferenceControls(settings, {
        dispatchToolbarChange:
          t.id === "simmanage-default-sort" || t.id === "simmanage-min-rating",
      });
    }
  });

  // --- Close handlers ---
  let closed = false;
  const close = () => {
    if (closed) return;
    closed = true;
    window.removeEventListener("keydown", onKeyDown);
    cleanupFns.splice(0).forEach((cleanup) => {
      try {
        cleanup();
      } catch (error) {
        console.warn("settings modal cleanup failed:", error);
      }
    });
    backdrop.remove();
  };

  const onKeyDown = (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
    }
  };

  closeBtn.addEventListener("click", close);
  backdrop.addEventListener("click", (event) => {
    if (event.target === backdrop) close();
  });
  window.addEventListener("keydown", onKeyDown);

  // --- Section nav tab switching ---
  const sectionBtns = nav.querySelectorAll(".settings-nav-btn");
  const sections = content.querySelectorAll(".settings-section");

  sectionBtns.forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.section;
      sectionBtns.forEach((b) => {
        b.classList.toggle("active", b.dataset.section === target);
        b.setAttribute(
          "aria-selected",
          b.dataset.section === target ? "true" : "false",
        );
      });
      sections.forEach((sec) => {
        sec.hidden = sec.id !== `settings-section-${target}`;
      });
    });
  });

  return { backdrop, cleanup: cleanupFns };
}

function _buildNav(items = SETTINGS_SECTION_ITEMS) {
  const nav = document.createElement("nav");
  nav.className = "settings-modal-nav";
  nav.setAttribute("aria-label", "Settings sections");

  items.forEach((item, i) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "settings-nav-btn" + (i === 0 ? " active" : "");
    btn.dataset.section = item.key;
    btn.textContent = item.label;
    btn.setAttribute("role", "tab");
    btn.setAttribute("aria-selected", i === 0 ? "true" : "false");
    nav.appendChild(btn);
  });

  return nav;
}

function _buildContent(viewerRuntime, cleanupFns = []) {
  const content = document.createElement("div");
  content.className = "settings-modal-content";

  content.appendChild(_buildViewerSection(viewerRuntime));
  content.appendChild(_buildSimulationSection());
  content.appendChild(_buildTaskExportsSection());
  content.appendChild(_buildWorkspaceSection(cleanupFns));
  content.appendChild(_buildSystemSection(viewerRuntime));

  return content;
}

// ---------------------------------------------------------------------------
// Section builders — controls are the actual interactive elements
// ---------------------------------------------------------------------------

function _buildViewerSection(viewerRuntime) {
  const sec = document.createElement("div");
  sec.id = "settings-section-viewer";
  sec.className = "settings-section";
  sec.setAttribute("role", "tabpanel");

  _appendSectionHeading(
    sec,
    "Viewer",
    "Viewport display and rendering preferences.",
  );

  // Real-time Updates control
  _appendInlineRow(sec, {
    labelText: "Real-time Updates",
    labelFor: "live-update",
    helpText: VIEWER_HELP.liveUpdate,
    controlHtml: `<input type="checkbox" id="live-update"${_state.liveUpdate ? " checked" : ""}>`,
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
  const orbitHeader = _buildSubSectionHeader("Orbit Controls", onResetOrbit);
  sec.appendChild(orbitHeader);

  const rotateResult = _buildSliderRow(
    "Rotate Speed",
    "rotateSpeed",
    0.1,
    5.0,
    0.1,
    _viewerState,
    VIEWER_HELP.rotateSpeed,
  );
  sec.appendChild(rotateResult.row);

  const zoomResult = _buildSliderRow(
    "Zoom Speed",
    "zoomSpeed",
    0.1,
    5.0,
    0.1,
    _viewerState,
    VIEWER_HELP.zoomSpeed,
  );
  sec.appendChild(zoomResult.row);

  const panResult = _buildSliderRow(
    "Pan Speed",
    "panSpeed",
    0.1,
    5.0,
    0.1,
    _viewerState,
    VIEWER_HELP.panSpeed,
  );
  sec.appendChild(panResult.row);

  // Damping enabled toggle
  const dampingEnabledResult = _buildToggleRow(
    "Enable Damping",
    "dampingEnabled",
    _viewerState,
    VIEWER_HELP.dampingEnabled,
  );
  const dampingEnabledBadge = dampingEnabledResult.badge;
  const dampingToggle = dampingEnabledResult.checkbox;
  sec.appendChild(dampingEnabledResult.row);

  // Damping factor slider (hidden when damping disabled)
  const dampingFactorResult = _buildSliderRow(
    "Damping Factor",
    "dampingFactor",
    0.01,
    0.5,
    0.01,
    _viewerState,
    VIEWER_HELP.dampingFactor,
  );
  dampingFactorResult.row.hidden = !_viewerState.dampingEnabled;
  sec.appendChild(dampingFactorResult.row);

  // Override the default dampingEnabled change handler (need to show/hide dampingFactor row)
  // Remove default handler by replacing the element (simpler: add the special handler here)
  dampingToggle.addEventListener("change", (e) => {
    _viewerState.dampingEnabled = e.target.checked;
    dampingFactorResult.row.hidden = !e.target.checked;
    _updateBadge(
      dampingEnabledBadge,
      e.target.checked,
      RECOMMENDED_DEFAULTS.dampingEnabled,
    );
    debouncedSaveViewerSettings(_viewerState);
    _applyLive();
  });

  // ---------- SUB-SECTION: Camera ----------
  const cameraHeader = _buildSubSectionHeader("Camera", onResetCamera);
  sec.appendChild(cameraHeader);

  // Startup Camera Mode radio row
  const cameraRow = document.createElement("div");
  cameraRow.className = "settings-control-row";

  cameraRow.appendChild(
    _buildSettingsLabelCopy(
      "Startup Camera Mode",
      "",
      VIEWER_HELP.startupCameraMode,
    ),
  );

  const cameraValueWrapper = document.createElement("div");
  cameraValueWrapper.className = "settings-control-value";

  const cameraBadge = document.createElement("span");
  cameraBadge.className = "settings-recommended-badge";
  cameraBadge.textContent = "Recommended";
  cameraBadge.hidden =
    _viewerState.startupCameraMode !== RECOMMENDED_DEFAULTS.startupCameraMode;

  const radioGroup = document.createElement("div");
  radioGroup.setAttribute("style", "display:flex;gap:12px;align-items:center;");

  const radioOptions = [
    { value: "perspective", label: "Perspective" },
    { value: "orthographic", label: "Orthographic" },
  ];

  let perspectiveRadio;
  let orthographicRadio;
  radioOptions.forEach((opt) => {
    const radioId = `viewer-startupCameraMode-${opt.value}`;
    const radioLabel = document.createElement("label");
    radioLabel.setAttribute(
      "style",
      "display:flex;align-items:center;gap:4px;cursor:pointer;",
    );

    const radio = document.createElement("input");
    radio.type = "radio";
    radio.id = radioId;
    radio.name = "viewer-startupCameraMode";
    radio.value = opt.value;
    radio.checked = _viewerState.startupCameraMode === opt.value;
    radio.setAttribute("style", "margin:0;");

    if (opt.value === "perspective") perspectiveRadio = radio;
    if (opt.value === "orthographic") orthographicRadio = radio;

    radio.addEventListener("change", (e) => {
      if (e.target.checked) {
        _viewerState.startupCameraMode = opt.value;
        _updateBadge(
          cameraBadge,
          opt.value,
          RECOMMENDED_DEFAULTS.startupCameraMode,
        );
        debouncedSaveViewerSettings(_viewerState);
        // NOTE: does NOT call _applyLive() — startup camera mode takes effect on next launch
      }
    });

    const radioText = document.createElement("span");
    radioText.textContent = opt.label;
    radioText.setAttribute("style", "font-size:0.85rem;");

    radioLabel.appendChild(radio);
    radioLabel.appendChild(radioText);
    radioGroup.appendChild(radioLabel);
  });

  cameraValueWrapper.appendChild(radioGroup);
  cameraValueWrapper.appendChild(cameraBadge);
  cameraRow.appendChild(cameraValueWrapper);
  sec.appendChild(cameraRow);

  const cameraHelp = document.createElement("p");
  cameraHelp.className = "settings-section-help";
  cameraHelp.setAttribute("style", "margin-top:4px;font-style:italic;");
  cameraHelp.textContent = "Takes effect on next launch.";
  sec.appendChild(cameraHelp);

  // ---------- SUB-SECTION: Input ----------
  const inputHeader = _buildSubSectionHeader("Input", onResetInput);
  sec.appendChild(inputHeader);

  const invertWheelResult = _buildToggleRow(
    "Invert Scroll Zoom",
    "invertWheelZoom",
    _viewerState,
    VIEWER_HELP.invertWheelZoom,
  );
  sec.appendChild(invertWheelResult.row);

  const keyboardPanResult = _buildToggleRow(
    "Keyboard Pan Shortcuts",
    "keyboardPanEnabled",
    _viewerState,
    VIEWER_HELP.keyboardPanEnabled,
  );
  sec.appendChild(keyboardPanResult.row);

  // ---------- Per-section reset handlers ----------

  function onResetOrbit() {
    const newSettings = resetViewerSection("orbit");
    _viewerState = { ..._viewerState, ...newSettings };

    // Update orbit slider DOMs
    _syncSliderRow(
      rotateResult,
      _viewerState.rotateSpeed,
      RECOMMENDED_DEFAULTS.rotateSpeed,
    );
    _syncSliderRow(
      zoomResult,
      _viewerState.zoomSpeed,
      RECOMMENDED_DEFAULTS.zoomSpeed,
    );
    _syncSliderRow(
      panResult,
      _viewerState.panSpeed,
      RECOMMENDED_DEFAULTS.panSpeed,
    );

    // Update dampingEnabled toggle
    dampingToggle.checked = _viewerState.dampingEnabled;
    _updateBadge(
      dampingEnabledBadge,
      _viewerState.dampingEnabled,
      RECOMMENDED_DEFAULTS.dampingEnabled,
    );
    dampingFactorResult.row.hidden = !_viewerState.dampingEnabled;

    // Update dampingFactor slider
    _syncSliderRow(
      dampingFactorResult,
      _viewerState.dampingFactor,
      RECOMMENDED_DEFAULTS.dampingFactor,
    );

    _applyLive();
  }

  function onResetCamera() {
    const newSettings = resetViewerSection("camera");
    _viewerState = { ..._viewerState, ...newSettings };

    // Update radio buttons
    if (perspectiveRadio)
      perspectiveRadio.checked =
        _viewerState.startupCameraMode === "perspective";
    if (orthographicRadio)
      orthographicRadio.checked =
        _viewerState.startupCameraMode === "orthographic";
    _updateBadge(
      cameraBadge,
      _viewerState.startupCameraMode,
      RECOMMENDED_DEFAULTS.startupCameraMode,
    );
    // NOTE: does NOT call _applyLive() — startup camera mode takes effect on next launch
  }

  function onResetInput() {
    const newSettings = resetViewerSection("input");
    _viewerState = { ..._viewerState, ...newSettings };

    // Update invertWheelZoom toggle
    invertWheelResult.checkbox.checked = _viewerState.invertWheelZoom;
    _updateBadge(
      invertWheelResult.badge,
      _viewerState.invertWheelZoom,
      RECOMMENDED_DEFAULTS.invertWheelZoom,
    );

    // Update keyboardPanEnabled toggle
    keyboardPanResult.checkbox.checked = _viewerState.keyboardPanEnabled;
    _updateBadge(
      keyboardPanResult.badge,
      _viewerState.keyboardPanEnabled,
      RECOMMENDED_DEFAULTS.keyboardPanEnabled,
    );

    _applyLive();
  }

  // ---------- Private helpers (closures over _viewerState) ----------

  function _buildSubSectionHeader(titleText, onReset) {
    const hdr = document.createElement("div");
    hdr.className = "settings-subsection-header";

    const h4 = document.createElement("h4");
    h4.className = "settings-subsection-title";
    h4.textContent = titleText;
    hdr.appendChild(h4);

    const resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.className = "settings-reset-btn";
    resetBtn.textContent = "Reset";
    resetBtn.addEventListener("click", onReset);
    hdr.appendChild(resetBtn);

    return hdr;
  }

  function _buildSliderRow(
    labelText,
    settingKey,
    min,
    max,
    step,
    currentSettingsSnapshot,
    helpText = "",
  ) {
    const row = document.createElement("div");
    row.className = "settings-control-row";

    const inputId = `viewer-${settingKey}`;

    row.appendChild(_buildSettingsLabelCopy(labelText, inputId, helpText));

    const valueWrapper = document.createElement("div");
    valueWrapper.className = "settings-control-value";

    const sliderGroup = document.createElement("div");
    sliderGroup.className = "settings-slider-group";

    const slider = document.createElement("input");
    slider.type = "range";
    slider.id = inputId;
    slider.min = String(min);
    slider.max = String(max);
    slider.step = String(step);
    slider.value = String(currentSettingsSnapshot[settingKey]);

    const readout = document.createElement("span");
    readout.className = "settings-slider-readout";
    readout.textContent = _formatSliderValue(
      currentSettingsSnapshot[settingKey],
      step,
    );

    const badge = document.createElement("span");
    badge.className = "settings-recommended-badge";
    badge.textContent = "Recommended";
    badge.hidden =
      currentSettingsSnapshot[settingKey] !== RECOMMENDED_DEFAULTS[settingKey];

    slider.addEventListener("input", () => {
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
    helpText = "",
  ) {
    const row = document.createElement("div");
    row.className = "settings-control-row";

    const inputId = `viewer-${settingKey}`;

    row.appendChild(_buildSettingsLabelCopy(labelText, inputId, helpText));

    const valueWrapper = document.createElement("div");
    valueWrapper.className = "settings-control-value";

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.id = inputId;
    checkbox.checked = currentSettingsSnapshot[settingKey];

    const badge = document.createElement("span");
    badge.className = "settings-recommended-badge";
    badge.textContent = "Recommended";
    badge.hidden =
      currentSettingsSnapshot[settingKey] !== RECOMMENDED_DEFAULTS[settingKey];

    checkbox.addEventListener("change", (e) => {
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
  const sec = document.createElement("div");
  sec.id = "settings-section-simulation";
  sec.className = "settings-section";
  sec.hidden = true;
  sec.setAttribute("role", "tabpanel");

  _appendSectionHeading(
    sec,
    "Simulation",
    "Persistent solve defaults live here. Advanced controls expose only stable public runtime overrides.",
  );

  const currentSimBasic = getCurrentSimBasicSettings();
  const solverHeader = _buildSubSectionHeader("Solve Defaults", () => {
    resetSimBasicSettings();
    const mvm = document.getElementById("simbasic-meshValidationMode");
    if (mvm) mvm.value = SIM_BASIC_DEFAULTS.meshValidationMode;
    const fs = document.getElementById("simbasic-frequencySpacing");
    if (fs) fs.value = SIM_BASIC_DEFAULTS.frequencySpacing;
    const vb = document.getElementById("simbasic-verbose");
    if (vb) vb.checked = SIM_BASIC_DEFAULTS.verbose;
    if (mvmBadge) mvmBadge.hidden = true;
    if (fsBadge) fsBadge.hidden = true;
    if (vbBadge) vbBadge.hidden = true;
  });
  sec.appendChild(solverHeader);

  const mvmResult = _buildSimBasicSelectRow(
    "Mesh Validation Policy",
    "simbasic-meshValidationMode",
    [
      { value: "warn", label: "Warn" },
      { value: "strict", label: "Strict" },
      { value: "off", label: "Off" },
    ],
    currentSimBasic.meshValidationMode,
    SIM_BASIC_DEFAULTS.meshValidationMode,
    SIMULATION_BASIC_HELP.meshValidationMode,
  );
  sec.appendChild(mvmResult.row);
  let mvmBadge = mvmResult.badge;

  const fsResult = _buildSimBasicSelectRow(
    "Sweep Spacing",
    "simbasic-frequencySpacing",
    [
      { value: "log", label: "Logarithmic" },
      { value: "linear", label: "Linear" },
    ],
    currentSimBasic.frequencySpacing,
    SIM_BASIC_DEFAULTS.frequencySpacing,
    SIMULATION_BASIC_HELP.frequencySpacing,
  );
  sec.appendChild(fsResult.row);
  let fsBadge = fsResult.badge;

  const vbResult = _buildSimBasicCheckboxRow(
    "Verbose Backend Logging",
    "simbasic-verbose",
    currentSimBasic.verbose,
    SIM_BASIC_DEFAULTS.verbose,
    SIMULATION_BASIC_HELP.verbose,
  );
  sec.appendChild(vbResult.row);
  let vbBadge = vbResult.badge;

  const advancedHeader = _buildSubSectionHeader("Advanced Solver Controls");
  sec.appendChild(advancedHeader);

  const currentSimAdvanced = getCurrentSimAdvancedSettings();
  const advancedIntro = document.createElement("div");
  advancedIntro.className = "settings-section-help";
  advancedIntro.innerHTML =
    "These settings control BEM operator assembly speed and accuracy. " +
    "Defaults are tuned for fast exploratory sweeps (~1.8x faster). " +
    "For final accurate solves, enable Burton-Miller." +
    "<br><br>" +
    "<strong>Benchmarked speedups</strong> (Apple M1 Max, 7732-element mesh, 20 freq):<br>" +
    "\u2022 <strong>Workgroup Size 1</strong> \u2014 ~30\u201350% faster on CPU OpenCL. No accuracy loss. Always recommended.<br>" +
    "\u2022 <strong>Burton-Miller off</strong> \u2014 ~1.8x faster (2 operators instead of 4). " +
    "May produce SPL deviations of up to 7 dB at certain frequencies where mesh interior resonances occur. " +
    "Good for quick exploratory sweeps, not recommended for final results.<br>" +
    "\u2022 <strong>Quadrature 3</strong> \u2014 ~1.25x faster. Amplifies Burton-Miller artifacts significantly " +
    "(up to 28 dB deviation). Only use with Burton-Miller on.<br>" +
    "\u2022 <strong>Numba backend</strong> \u2014 Competitive with OpenCL on CPU. Good alternative if OpenCL/pocl is unavailable.";
  sec.appendChild(advancedIntro);

  // ── Preset buttons ──
  const PRESETS = {
    fast: { useBurtonMiller: false, quadratureRegular: 4, workgroupSizeMultiple: 1, assemblyBackend: "opencl" },
    accurate: { useBurtonMiller: true, quadratureRegular: 4, workgroupSizeMultiple: 1, assemblyBackend: "opencl" },
  };

  function _applyPreset(preset) {
    saveSimAdvancedSettings(preset);
    const ubm = document.getElementById("simadvanced-useBurtonMiller");
    if (ubm) ubm.checked = preset.useBurtonMiller;
    const qr = document.getElementById("simadvanced-quadratureRegular");
    if (qr) qr.value = preset.quadratureRegular;
    const wg = document.getElementById("simadvanced-workgroupSizeMultiple");
    if (wg) wg.value = preset.workgroupSizeMultiple;
    const ab = document.getElementById("simadvanced-assemblyBackend");
    if (ab) ab.value = preset.assemblyBackend;
    // Update badges
    if (typeof ubmBadge !== "undefined" && ubmBadge) ubmBadge.hidden = preset.useBurtonMiller === SIM_ADVANCED_DEFAULTS.useBurtonMiller;
    if (typeof qrBadge !== "undefined" && qrBadge) qrBadge.hidden = preset.quadratureRegular === SIM_ADVANCED_DEFAULTS.quadratureRegular;
    if (typeof wgBadge !== "undefined" && wgBadge) wgBadge.hidden = preset.workgroupSizeMultiple === SIM_ADVANCED_DEFAULTS.workgroupSizeMultiple;
    if (typeof abBadge !== "undefined" && abBadge) abBadge.hidden = preset.assemblyBackend === SIM_ADVANCED_DEFAULTS.assemblyBackend;
  }

  const presetRow = document.createElement("div");
  presetRow.style.cssText = "display:flex;gap:8px;margin:8px 0 4px;";

  const fastBtn = document.createElement("button");
  fastBtn.type = "button";
  fastBtn.className = "settings-reset-btn";
  fastBtn.style.cssText = "flex:1;padding:6px 12px;font-size:13px;font-weight:600;";
  fastBtn.textContent = "Fast";
  fastBtn.title = "BM off, quad 4, wg 1 — ~1.5x faster, max 5 dB deviation";
  fastBtn.addEventListener("click", () => _applyPreset(PRESETS.fast));

  const accurateBtn = document.createElement("button");
  accurateBtn.type = "button";
  accurateBtn.className = "settings-reset-btn";
  accurateBtn.style.cssText = "flex:1;padding:6px 12px;font-size:13px;font-weight:600;";
  accurateBtn.textContent = "Accurate";
  accurateBtn.title = "BM on, quad 4, wg 1 — artifact-free, recommended for final solves";
  accurateBtn.addEventListener("click", () => _applyPreset(PRESETS.accurate));

  presetRow.appendChild(fastBtn);
  presetRow.appendChild(accurateBtn);
  sec.appendChild(presetRow);

  const advancedActiveHeader = _buildSubSectionHeader(
    "Active Contract Overrides",
    () => {
      _applyPreset({ ...SIM_ADVANCED_DEFAULTS });
    },
  );
  sec.appendChild(advancedActiveHeader);

  const ubmResult = _buildSimBasicCheckboxRow(
    ADVANCED_CONTROL_COPY.use_burton_miller.label,
    "simadvanced-useBurtonMiller",
    currentSimAdvanced.useBurtonMiller,
    SIM_ADVANCED_DEFAULTS.useBurtonMiller,
    SIMULATION_ADVANCED_HELP.useBurtonMiller,
  );
  sec.appendChild(ubmResult.row);
  let ubmBadge = ubmResult.badge;

  const qrResult = _buildSimAdvancedNumberRow(
    ADVANCED_CONTROL_COPY.quadrature_regular.label,
    "simadvanced-quadratureRegular",
    currentSimAdvanced.quadratureRegular,
    SIM_ADVANCED_DEFAULTS.quadratureRegular,
    { min: "1", max: "10", step: "1" },
    SIMULATION_ADVANCED_HELP.quadratureRegular,
  );
  sec.appendChild(qrResult.row);
  let qrBadge = qrResult.badge;

  const wgResult = _buildSimAdvancedNumberRow(
    ADVANCED_CONTROL_COPY.workgroup_size_multiple.label,
    "simadvanced-workgroupSizeMultiple",
    currentSimAdvanced.workgroupSizeMultiple,
    SIM_ADVANCED_DEFAULTS.workgroupSizeMultiple,
    { min: "1", max: "8", step: "1" },
    SIMULATION_ADVANCED_HELP.workgroupSizeMultiple,
  );
  sec.appendChild(wgResult.row);
  let wgBadge = wgResult.badge;

  const abResult = _buildSimBasicSelectRow(
    ADVANCED_CONTROL_COPY.assembly_backend.label,
    "simadvanced-assemblyBackend",
    [
      { value: "opencl", label: "OpenCL (pocl/CPU)" },
      { value: "numba", label: "Numba (JIT/CPU)" },
    ],
    currentSimAdvanced.assemblyBackend,
    SIM_ADVANCED_DEFAULTS.assemblyBackend,
    SIMULATION_ADVANCED_HELP.assemblyBackend,
  );
  sec.appendChild(abResult.row);
  let abBadge = abResult.badge;

  return sec;
}

function _buildTaskExportsSection() {
  const sec = document.createElement("div");
  sec.id = "settings-section-task-exports";
  sec.className = "settings-section";
  sec.hidden = true;
  sec.setAttribute("role", "tabpanel");

  _appendSectionHeading(
    sec,
    "Task Exports",
    "Job-list preferences, automatic result bundles, and optional mesh artifact downloads all live together here.",
  );

  const managementSettings = getCurrentSimulationManagementSettings();

  const taskListHeader = _buildSubSectionHeader("Simulation Jobs Toolbar");
  sec.appendChild(taskListHeader);

  _appendInlineRow(sec, {
    labelText: "Default Task Sort",
    labelFor: "simmanage-default-sort",
    helpText: SIMULATION_MANAGEMENT_HELP.defaultSort,
    controlNode: _buildSelectElement(
      "simmanage-default-sort",
      managementSettings.defaultSort,
      [
        { value: "completed_desc", label: "Newest First" },
        { value: "rating_desc", label: "Highest Rated" },
        { value: "label_asc", label: "Label A-Z" },
      ],
    ),
  });

  _appendInlineRow(sec, {
    labelText: "Minimum Rating Filter",
    labelFor: "simmanage-min-rating",
    helpText: SIMULATION_MANAGEMENT_HELP.minRatingFilter,
    controlNode: _buildSelectElement(
      "simmanage-min-rating",
      String(managementSettings.minRatingFilter),
      [
        { value: "0", label: "All Ratings" },
        { value: "1", label: "1 star or higher" },
        { value: "2", label: "2 stars or higher" },
        { value: "3", label: "3 stars or higher" },
        { value: "4", label: "4 stars or higher" },
        { value: "5", label: "5 stars only" },
      ],
    ),
  });

  const exportHeader = _buildSubSectionHeader("Completed Task Bundles", () => {
    const resetSettings = resetSimulationManagementSettings();
    const defaultSort = document.getElementById("simmanage-default-sort");
    if (defaultSort) {
      defaultSort.value = resetSettings.defaultSort;
    }
    const minRating = document.getElementById("simmanage-min-rating");
    if (minRating) {
      minRating.value = String(resetSettings.minRatingFilter);
    }
    _syncTaskListPreferenceControls(resetSettings);
  });
  sec.appendChild(exportHeader);

  _appendInlineRow(sec, {
    labelText: "Auto-download solve mesh (.msh)",
    labelFor: "download-sim-mesh",
    helpText: SIMULATION_MANAGEMENT_HELP.downloadMesh,
    controlHtml: `<input type="checkbox" id="download-sim-mesh"${_state.downloadSimMesh ? " checked" : ""}>`,
  });

  return sec;
}

function _buildWorkspaceSection(cleanupFns = []) {
  const sec = document.createElement("div");
  sec.id = "settings-section-workspace";
  sec.className = "settings-section";
  sec.hidden = true;
  sec.setAttribute("role", "tabpanel");

  _appendSectionHeading(
    sec,
    "Workspace",
    "Manage the folder workspace used for manual exports and completed simulation-task bundles.",
  );

  const statusRow = document.createElement("div");
  statusRow.className = "settings-control-row";
  statusRow.appendChild(
    _buildSettingsLabelCopy(
      "Selected Folder",
      "",
      "The backend workspace folder used for all exports.",
    ),
  );
  const statusValue = document.createElement("div");
  statusValue.className = "settings-control-value";
  const statusText = document.createElement("span");
  statusText.id = "settings-workspace-folder-label";
  statusValue.appendChild(statusText);
  statusRow.appendChild(statusValue);
  sec.appendChild(statusRow);

  // Path display row
  const pathRow = document.createElement("div");
  pathRow.className = "settings-control-row";
  const pathLabel = document.createElement("div");
  pathLabel.className = "settings-control-label";
  pathLabel.textContent = "Output Folder Path";
  pathRow.appendChild(pathLabel);
  const pathValueBox = document.createElement("pre");
  pathValueBox.className = "ui-command-box settings-workspace-path-box";
  pathValueBox.textContent = "Loading…";
  const pathValueWrap = document.createElement("div");
  pathValueWrap.className = "settings-control-value";
  pathValueWrap.appendChild(pathValueBox);
  pathRow.appendChild(pathValueWrap);
  sec.appendChild(pathRow);

  // "Open in Finder" button row
  const finderRow = document.createElement("div");
  finderRow.className = "settings-action-row";
  const finderBtn = document.createElement("button");
  finderBtn.type = "button";
  finderBtn.className = "secondary";
  finderBtn.textContent = "Open in Finder";
  const finderHelp = document.createElement("p");
  finderHelp.className = "settings-action-help";
  finderHelp.textContent =
    "Opens the output folder in the OS file manager (Finder / Explorer).";
  finderRow.appendChild(finderBtn);
  finderRow.appendChild(finderHelp);
  sec.appendChild(finderRow);

  const chooseRow = document.createElement("div");
  chooseRow.className = "settings-action-row";

  const chooseBtn = document.createElement("button");
  chooseBtn.type = "button";
  chooseBtn.id = "settings-choose-folder-btn";
  chooseBtn.className = "secondary";
  chooseRow.appendChild(chooseBtn);

  const chooseHelp = document.createElement("p");
  chooseHelp.id = "settings-workspace-support";
  chooseHelp.className = "settings-action-help";
  chooseRow.appendChild(chooseHelp);
  sec.appendChild(chooseRow);

  const routingNote = document.createElement("p");
  routingNote.id = "settings-workspace-routing";
  routingNote.className = "settings-section-help";
  sec.appendChild(routingNote);

  finderBtn.addEventListener("click", async () => {
    finderBtn.disabled = true;
    const ok = await openWorkspaceInFinder();
    finderBtn.disabled = false;
    if (!ok) {
      finderHelp.textContent =
        "Could not open folder — is the backend running?";
    }
  });

  const refreshWorkspaceCopy = () => {
    const selectedLabel = getSelectedFolderLabel();
    statusText.textContent = selectedLabel;
    chooseBtn.textContent =
      selectedLabel === "No folder selected"
        ? "Choose Folder"
        : "Change Folder";
    chooseBtn.disabled = false;

    chooseHelp.textContent =
      "Opens a native folder picker via the backend server.";
    routingNote.textContent =
      "Exports are saved to the folder shown above. Use Choose Folder to change it.";

    fetchWorkspacePath().then((path) => {
      pathValueBox.textContent =
        path || "Backend unavailable — path unknown.";
    });
  };

  chooseBtn.addEventListener("click", async () => {
    chooseBtn.disabled = true;
    chooseHelp.textContent = "Waiting for folder selection…";
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
  const hdr = document.createElement("div");
  hdr.className = "settings-subsection-header";

  const h4 = document.createElement("h4");
  h4.className = "settings-subsection-title";
  h4.textContent = titleText;
  hdr.appendChild(h4);

  if (typeof onReset === "function") {
    const resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.className = "settings-reset-btn";
    resetBtn.textContent = "Reset";
    resetBtn.addEventListener("click", onReset);
    hdr.appendChild(resetBtn);
  }

  return hdr;
}

function _makeDefaultBadge(currentValue, defaultValue) {
  const badge = document.createElement("span");
  badge.setAttribute("style", "font-size:0.7rem;opacity:0.6;margin-left:6px;");
  badge.textContent = "Default";
  badge.hidden = currentValue === defaultValue;
  return badge;
}

function _buildSimBasicSelectRow(
  labelText,
  selectId,
  options,
  currentValue,
  defaultValue,
  helpText = "",
) {
  const row = document.createElement("div");
  row.className = "settings-control-row";
  row.appendChild(_buildSettingsLabelCopy(labelText, selectId, helpText));

  const valueWrapper = document.createElement("div");
  valueWrapper.className = "settings-control-value";

  const select = document.createElement("select");
  select.id = selectId;

  for (const { value, label: optLabel } of options) {
    const opt = document.createElement("option");
    opt.value = value;
    opt.textContent = optLabel;
    if (value === currentValue) opt.selected = true;
    select.appendChild(opt);
  }

  const badge = _makeDefaultBadge(currentValue, defaultValue);

  select.addEventListener("change", () => {
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
  helpText = "",
) {
  const row = document.createElement("div");
  row.className = "settings-control-row";
  row.appendChild(_buildSettingsLabelCopy(labelText, checkboxId, helpText));

  const valueWrapper = document.createElement("div");
  valueWrapper.className = "settings-control-value";

  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.id = checkboxId;
  checkbox.checked = currentValue;

  const badge = _makeDefaultBadge(currentValue, defaultValue);

  checkbox.addEventListener("change", () => {
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
  { min = "", max = "", step = "0.0001" } = {},
  helpText = "",
) {
  const row = document.createElement("div");
  row.className = "settings-control-row";
  row.appendChild(_buildSettingsLabelCopy(labelText, inputId, helpText));

  const valueWrapper = document.createElement("div");
  valueWrapper.className = "settings-control-value";

  const input = document.createElement("input");
  input.type = "number";
  input.id = inputId;
  input.value = String(currentValue);
  if (min) input.min = min;
  if (max) input.max = max;
  input.step = step;

  const badge = _makeDefaultBadge(currentValue, defaultValue);
  input.addEventListener("change", () => {
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
  const exportFormatsRow = document.createElement("div");
  exportFormatsRow.className = "settings-control-row";
  exportFormatsRow.appendChild(
    _buildSettingsLabelCopy(
      "Bundle Formats",
      "",
      SIMULATION_MANAGEMENT_HELP.selectedFormats,
    ),
  );

  const exportFormatsValue = document.createElement("div");
  exportFormatsValue.className = "settings-control-value";
  exportFormatsValue.setAttribute(
    "style",
    "display:grid;grid-template-columns:repeat(auto-fit, minmax(180px, 1fr));gap:8px 12px;align-items:start;",
  );

  const exportFormatLabels = new Map([
    ["png", "Chart Images (PNG)"],
    ["csv", "Frequency Data CSV"],
    ["json", "Full Results JSON"],
    ["txt", "Summary Text Report"],
    ["polar_csv", "Polar Directivity CSV"],
    ["impedance_csv", "Impedance CSV"],
    ["vacs", "ABEC Spectrum (VACS)"],
    ["stl", "Waveguide STL"],
    ["fusion_csv", "Fusion 360 CSV Curves"],
  ]);

  SIMULATION_EXPORT_FORMAT_IDS.forEach((formatId) => {
    const option = document.createElement("label");
    option.setAttribute("style", "display:flex;align-items:center;gap:8px;");
    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.id = `simmanage-format-${formatId}`;
    checkbox.setAttribute("data-sim-management-format", formatId);
    checkbox.checked = managementSettings.selectedFormats.includes(formatId);
    option.appendChild(checkbox);
    const text = document.createElement("span");
    text.textContent = exportFormatLabels.get(formatId) || formatId;
    option.appendChild(text);
    exportFormatsValue.appendChild(option);
  });

  exportFormatsRow.appendChild(exportFormatsValue);
  return exportFormatsRow;
}

function _buildSystemSection(viewerRuntime) {
  const sec = document.createElement("div");
  sec.id = "settings-section-system";
  sec.className = "settings-section";
  sec.hidden = true;
  sec.setAttribute("role", "tabpanel");

  _appendSectionHeading(
    sec,
    "System",
    "Application updates and system information.",
  );

  const updateRow = document.createElement("div");
  updateRow.className = "settings-action-row";

  const updateBtn = document.createElement("button");
  updateBtn.type = "button";
  updateBtn.id = "check-updates-btn";
  updateBtn.className = "secondary";
  updateBtn.textContent = "Check for App Updates";
  updateRow.appendChild(updateBtn);

  const updateHelp = document.createElement("p");
  updateHelp.className = "settings-action-help";
  updateHelp.textContent =
    "Queries the backend for the latest commit on the default remote branch and reports whether the local copy is behind, ahead, or up to date.";
  updateRow.appendChild(updateHelp);

  sec.appendChild(updateRow);

  // Reset All Settings action row
  const resetAllRow = document.createElement("div");
  resetAllRow.className = "settings-action-row";

  const resetAllBtn = document.createElement("button");
  resetAllBtn.type = "button";
  resetAllBtn.id = "reset-all-settings-btn";
  resetAllBtn.className = "secondary";
  resetAllBtn.textContent = "Reset Viewer Settings to Defaults";
  resetAllRow.appendChild(resetAllBtn);

  const resetAllHelp = document.createElement("p");
  resetAllHelp.className = "settings-action-help";
  resetAllHelp.textContent =
    "Restores viewer controls to their recommended default values. Simulation, export, and workspace preferences stay unchanged.";
  resetAllRow.appendChild(resetAllHelp);

  resetAllBtn.addEventListener("click", () => {
    resetAllViewerSettings();
    applyViewerSettingsToControls(
      viewerRuntime.getControls(),
      RECOMMENDED_DEFAULTS,
    );
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
  const h = document.createElement("h3");
  h.className = "settings-section-title";
  h.textContent = title;
  parent.appendChild(h);

  if (helpText) {
    const p = document.createElement("p");
    p.className = "settings-section-help";
    p.textContent = helpText;
    parent.appendChild(p);
  }
}

function _appendInlineRow(
  parent,
  { labelText, labelFor, controlHtml = "", controlNode = null, helpText = "" },
) {
  const row = document.createElement("div");
  row.className = "settings-control-row";
  row.appendChild(_buildSettingsLabelCopy(labelText, labelFor, helpText));

  const wrapper = document.createElement("div");
  wrapper.className = "settings-control-value";
  if (controlNode) {
    wrapper.appendChild(controlNode);
  } else {
    wrapper.innerHTML = controlHtml;
  }
  row.appendChild(wrapper);

  parent.appendChild(row);
}

function _buildSettingsLabelCopy(labelText, labelFor, helpText = "") {
  const copy = document.createElement("div");
  copy.className = "settings-control-copy";

  const label = document.createElement("label");
  if (labelFor) {
    label.setAttribute("for", labelFor);
  }
  label.textContent = labelText;
  if (helpText) {
    label.setAttribute("data-help-text", helpText);
  }
  copy.appendChild(label);

  return copy;
}

function _buildSelectElement(id, currentValue, options) {
  const select = document.createElement("select");
  select.id = id;

  options.forEach(({ value, label }) => {
    const option = document.createElement("option");
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
    target?.id === "simmanage-auto-export" ||
    target?.id === "simmanage-default-sort" ||
    target?.id === "simmanage-min-rating" ||
    target?.getAttribute?.("data-sim-management-format"),
  );
}

function _readSimulationManagementSettings(root) {
  const current = getCurrentSimulationManagementSettings();
  const selectedFormats = Array.from(
    root.querySelectorAll("input[data-sim-management-format]"),
  )
    .filter((input) => input.checked)
    .map((input) => input.getAttribute("data-sim-management-format"))
    .filter(Boolean);
  const minRating = Number(
    document.getElementById("simmanage-min-rating")?.value,
  );

  return {
    ...current,
    autoExportOnComplete:
      document.getElementById("simmanage-auto-export")?.checked ??
      current.autoExportOnComplete,
    selectedFormats,
    defaultSort:
      document.getElementById("simmanage-default-sort")?.value ||
      current.defaultSort,
    minRatingFilter: Number.isFinite(minRating)
      ? Math.max(0, Math.min(5, minRating))
      : current.minRatingFilter,
  };
}

function _syncTaskListPreferenceControls(
  settings,
  { dispatchToolbarChange = false } = {},
) {
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
      typeof Event === "function" &&
      typeof element.dispatchEvent === "function"
    ) {
      element.dispatchEvent(new Event("change", { bubbles: true }));
    }
  };

  if (desiredSort) {
    syncValue("simmanage-default-sort", desiredSort, false);
    syncValue("simulation-jobs-sort", desiredSort, dispatchToolbarChange);
  }
  syncValue("simmanage-min-rating", desiredMinRating, false);
  syncValue(
    "simulation-jobs-min-rating",
    desiredMinRating,
    dispatchToolbarChange,
  );
}
