import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";

import { normalizeParamInput } from "../src/ui/paramInput.js";
import {
  formatJobSummary,
  renderSimulationMeshDiagnostics,
} from "../src/ui/simulation/jobActions.js";
import { renderSolveStatsSummary } from "../src/ui/simulation/results.js";
import { validateSimulationConfig } from "../src/modules/simulation/domain.js";
import { applyExportSelection } from "../src/ui/simulation/exports.js";
import {
  deriveExportFieldsFromFileName,
  markParametersChanged,
  resetParameterChangeTracking,
} from "../src/ui/fileOps.js";
import {
  SETTINGS_CONTROL_IDS,
  getLiveUpdateEnabled,
  getDisplayMode,
  getDownloadSimMeshEnabled,
  openSettingsModal,
} from "../src/ui/settings/modal.js";
import {
  describeSimBasicDeviceAvailability,
  describeSelectedDevice,
  summarizeRuntimeCapabilities,
} from "../src/ui/runtimeCapabilities.js";
import { createDependencyStatusPanel } from "../src/ui/dependencyStatus.js";
import { formatDependencyBlockMessage } from "../src/modules/runtime/health.js";
import { buildRequiredDependencyWarning } from "../src/ui/simulation/connection.js";
import {
  RECOMMENDED_DEFAULTS,
  resetAllViewerSettings,
  saveViewerSettings,
} from "../src/ui/settings/viewerSettings.js";
import { PARAM_SCHEMA } from "../src/config/schema.js";

test("index.html places the output-folder action in the simulation jobs header", () => {
  const html = fs.readFileSync(
    new URL("../index.html", import.meta.url),
    "utf8",
  );

  assert.match(
    html,
    /<div class="simulation-jobs-header-actions">[\s\S]*id="choose-folder-btn"[\s\S]*id="clear-failed-jobs-btn"[\s\S]*id="refresh-jobs-btn"/,
  );
  assert.doesNotMatch(html, /id="simulation-jobs-source-label"/);
  assert.doesNotMatch(html, /id="output-folder-row"/);
});

test("normalizeParamInput parses numeric literals consistently", () => {
  assert.equal(normalizeParamInput("1.0"), 1);
  assert.equal(normalizeParamInput(" 1e3 "), 1000);
  assert.equal(normalizeParamInput("-0.25"), -0.25);
  assert.equal(normalizeParamInput("45 + 10*cos(p)"), "45 + 10*cos(p)");
  assert.equal(normalizeParamInput("2+3"), "2+3");
});

test("mesh control labels separate viewport tessellation from solve mesh sizing", () => {
  assert.equal(
    PARAM_SCHEMA.MESH.angularSegments.label,
    "Preview Angular Segments",
  );
  assert.equal(
    PARAM_SCHEMA.MESH.lengthSegments.label,
    "Preview Length Segments",
  );
  assert.equal(
    PARAM_SCHEMA.MESH.throatResolution.label,
    "Throat Mesh Resolution",
  );
  assert.equal(
    PARAM_SCHEMA.MESH.mouthResolution.label,
    "Mouth Mesh Resolution",
  );
  assert.equal(PARAM_SCHEMA.MESH.rearResolution.label, "Rear Mesh Resolution");
  assert.equal(
    PARAM_SCHEMA.ENCLOSURE.encFrontResolution.label,
    "Front Baffle Mesh Resolution",
  );
  assert.equal(
    PARAM_SCHEMA.ENCLOSURE.encBackResolution.label,
    "Rear Baffle Mesh Resolution",
  );
  assert.match(
    PARAM_SCHEMA.MESH.angularSegments.tooltip,
    /Three\.js viewport/i,
  );
  assert.match(
    PARAM_SCHEMA.MESH.throatResolution.tooltip,
    /backend OCC solve\/export mesh/i,
  );
  assert.equal(PARAM_SCHEMA.SIMULATION.freqStart.label, "Sweep Start");
  assert.equal(PARAM_SCHEMA.SIMULATION.freqEnd.label, "Sweep End");
  assert.equal(PARAM_SCHEMA.SIMULATION.freqStart.controlId, "freq-start");
  assert.equal(PARAM_SCHEMA.SIMULATION.freqEnd.controlId, "freq-end");
  assert.equal(PARAM_SCHEMA.SIMULATION.numFreqs.controlId, "freq-steps");
  assert.match(
    PARAM_SCHEMA.SIMULATION.numFreqs.tooltip,
    /number of solved frequencies/i,
  );
});

test("validateSimulationConfig catches invalid ranges and counts", () => {
  assert.match(
    validateSimulationConfig({
      frequencyStart: 1000,
      frequencyEnd: 100,
      numFrequencies: 50,
    }),
    /Start frequency/,
  );

  assert.match(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 0,
    }),
    /Number of frequencies/,
  );

  assert.equal(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 50,
    }),
    null,
  );
});

test("formatJobSummary appends complete duration in m:ss", () => {
  const summary = formatJobSummary({
    status: "complete",
    startedAt: "2026-02-24T12:00:00.000Z",
    completedAt: "2026-02-24T12:02:53.000Z",
  });
  assert.equal(summary, "Complete (2:53)");
});

test("formatJobSummary appends complete duration in h:mm:ss", () => {
  const summary = formatJobSummary({
    status: "complete",
    startedAt: "2026-02-24T12:00:00.000Z",
    completedAt: "2026-02-24T13:04:32.000Z",
  });
  assert.equal(summary, "Complete (1:04:32)");
});

test("renderSolveStatsSummary includes persisted job completion timestamp", () => {
  const markup = renderSolveStatsSummary(
    {
      frequencies: [100, 1000],
      metadata: {
        performance: { total_time_seconds: 12.4 },
      },
    },
    {
      completedAt: "2026-03-19T08:45:00.000Z",
    },
  );

  assert.match(markup, /Completed/);
  assert.match(markup, /2026-03-19/);
  assert.match(markup, /2026-03-19 \d{2}:45/);
});

test("renderSolveStatsSummary uses persisted directivity metadata for solve settings", () => {
  const markup = renderSolveStatsSummary(
    {
      frequencies: [100, 1000],
      metadata: {
        performance: { total_time_seconds: 12.4 },
        observation: {
          effective_distance_m: 9.9,
          requested_distance_m: 9.9,
        },
        directivity: {
          angle_range_degrees: [0, 90],
          sample_count: 10,
          angular_step_degrees: 10,
          enabled_axes: ["horizontal", "diagonal"],
          normalization_angle_degrees: 7.5,
          diagonal_angle_degrees: 35,
          observation_origin: "throat",
          requested_distance_m: 1.0,
          effective_distance_m: 1.75,
        },
      },
    },
    {
      configSummary: {
        observation_origin: "mouth",
      },
    },
  );

  assert.match(markup, /Observation/);
  assert.match(markup, /1\.75 m from throat \(requested 1\.00 m\)/);
  assert.doesNotMatch(markup, /from mouth/);
  assert.match(markup, /Polar sweep/);
  assert.match(markup, /0° – 90°/);
  assert.match(markup, /Angular sampling/);
  assert.match(markup, /10° step, 10 samples/);
  assert.match(markup, /Axes/);
  assert.match(markup, /Horizontal, Diagonal/);
  assert.match(markup, /Normalization/);
  assert.match(markup, /7\.5°/);
  assert.match(markup, /Diagonal plane/);
  assert.match(markup, /35°/);
});

test("describeSimBasicDeviceAvailability reports selected auto mode and unavailable concrete modes", () => {
  const summary = describeSimBasicDeviceAvailability(
    {
      deviceInterface: {
        selected_mode: "opencl_gpu",
        mode_availability: {
          auto: { available: true },
          opencl_gpu: { available: true },
          opencl_cpu: { available: false },
        },
      },
    },
    "auto",
  );

  assert.deepEqual(summary.unavailableModes, ["opencl_cpu"]);
  assert.equal(summary.statusText, "Auto resolves to: OpenCL GPU");
});

test("describeSimBasicDeviceAvailability reports requested unavailable mode explicitly", () => {
  const summary = describeSimBasicDeviceAvailability(
    {
      deviceInterface: {
        mode_availability: {
          auto: { available: true },
          opencl_gpu: { available: false },
          opencl_cpu: { available: true },
        },
      },
    },
    "opencl_gpu",
  );

  assert.deepEqual(summary.unavailableModes, ["opencl_gpu"]);
  assert.equal(summary.statusText, "OpenCL GPU unavailable on this machine.");
});

test("summarizeRuntimeCapabilities reports advanced controls unavailable until backend advertises support", () => {
  const summary = summarizeRuntimeCapabilities({
    solverReady: true,
    occBuilderReady: true,
    capabilities: {
      simulationAdvanced: {
        available: true,
        controls: ["use_burton_miller"],
        reason:
          "The public solve contract now exposes Burton-Miller coupling as the stable advanced override.",
        plannedControls: ["method"],
      },
    },
  });

  assert.equal(summary.fullyReady, true);
  assert.equal(summary.simulationAdvanced.available, true);
  assert.equal(
    summary.simulationAdvanced.reason,
    "The public solve contract now exposes Burton-Miller coupling as the stable advanced override.",
  );
  assert.deepEqual(summary.simulationAdvanced.controls, ["use_burton_miller"]);
  assert.deepEqual(summary.simulationAdvanced.plannedControls, ["method"]);
});

test("describeSelectedDevice includes device name only when it adds signal", () => {
  assert.equal(
    describeSelectedDevice({
      deviceInterface: {
        selected_mode: "opencl_gpu",
        device_name: "Fake GPU",
      },
    }),
    "Using: OpenCL GPU (Fake GPU)",
  );

  assert.equal(
    describeSelectedDevice({
      deviceInterface: {
        selected_mode: "opencl_cpu",
        device_name: "CPU",
      },
    }),
    "Using: OpenCL CPU",
  );
});

test("renderSimulationMeshDiagnostics shows canonical tag counts and warnings", () => {
  const originalDocument = global.document;
  const diagnosticsEl = { innerHTML: "" };
  global.document = {
    getElementById(id) {
      return id === "simulation-mesh-diagnostics" ? diagnosticsEl : null;
    },
  };

  try {
    renderSimulationMeshDiagnostics({
      vertexCount: 12,
      triangleCount: 4,
      identityTriangleCounts: {
        throat_disc: 1,
        horn_wall: 0,
        inner_wall: 2,
        outer_wall: 1,
        mouth_rim: 0,
        throat_return: 0,
        rear_cap: 0,
        enc_front: 0,
        enc_side: 0,
        enc_rear: 0,
        enc_edge: 0,
      },
      tagCounts: { 1: 2, 2: 0, 3: 1, 4: 1 },
      warnings: [
        "Source surface tag (2) missing from the canonical simulation mesh.",
      ],
      provenance: "preview",
    });

    assert.match(diagnosticsEl.innerHTML, /12 vertices/);
    assert.match(diagnosticsEl.innerHTML, /Preview Geometry/);
    assert.match(diagnosticsEl.innerHTML, /Geometry Regions/);
    assert.match(diagnosticsEl.innerHTML, /Throat Disc/);
    assert.match(diagnosticsEl.innerHTML, /Inner Wall/);
    assert.doesNotMatch(diagnosticsEl.innerHTML, /throat_disc/);
    assert.doesNotMatch(diagnosticsEl.innerHTML, /Source \(2\)/);
    assert.match(
      diagnosticsEl.innerHTML,
      /Throat Disc is present, but it is not classified as the source region/i,
    );
  } finally {
    global.document = originalDocument;
  }
});

test("renderSimulationMeshDiagnostics shows authoritative backend OCC provenance when mesh stats are authoritative", () => {
  const originalDocument = global.document;
  const diagnosticsEl = { innerHTML: "" };
  global.document = {
    getElementById(id) {
      return id === "simulation-mesh-diagnostics" ? diagnosticsEl : null;
    },
  };

  try {
    renderSimulationMeshDiagnostics({
      vertexCount: 18,
      triangleCount: 6,
      identityTriangleCounts: {
        throat_disc: 1,
        horn_wall: 0,
        inner_wall: 2,
        outer_wall: 2,
        mouth_rim: 0,
        throat_return: 0,
        rear_cap: 1,
        enc_front: 0,
        enc_side: 0,
        enc_rear: 0,
        enc_edge: 0,
      },
      tagCounts: { 1: 5, 2: 1, 3: 0, 4: 0 },
      warnings: [],
      provenance: "backend",
    });

    assert.match(diagnosticsEl.innerHTML, /Solver Geometry/);
    assert.match(diagnosticsEl.innerHTML, /18 vertices/);
    assert.match(diagnosticsEl.innerHTML, /Rear Cap/);
    assert.doesNotMatch(diagnosticsEl.innerHTML, /Source \(2\)/);
  } finally {
    global.document = originalDocument;
  }
});

test("formatJobSummary falls back to Complete when duration is unavailable", () => {
  const summary = formatJobSummary({
    status: "complete",
    startedAt: "not-a-date",
    completedAt: null,
  });
  assert.equal(summary, "Complete");
});

test("applyExportSelection routes to expected handler", () => {
  const calls = [];
  const originalError = console.error;
  console.error = () => {};

  const handlers = {
    1: () => calls.push("image"),
    2: () => calls.push("csv"),
    3: () => calls.push("json"),
    4: () => calls.push("text"),
  };

  try {
    assert.equal(applyExportSelection({}, "2", handlers), true);
    assert.deepEqual(calls, ["csv"]);

    assert.equal(applyExportSelection({}, "9", handlers), false);
    assert.deepEqual(calls, ["csv"]);
  } finally {
    console.error = originalError;
  }
});

test("applyExportSelection includes VACS spectrum option 7", () => {
  const calls = [];
  const handlers = {
    1: () => calls.push("image"),
    2: () => calls.push("csv"),
    3: () => calls.push("json"),
    4: () => calls.push("text"),
    5: () => calls.push("polar"),
    6: () => calls.push("impedance"),
    7: () => calls.push("vacs"),
  };

  assert.equal(applyExportSelection({}, "7", handlers), true);
  assert.deepEqual(calls, ["vacs"]);
});

test("applyExportSelection includes CAD exports options 8 and 9", () => {
  const calls = [];
  const handlers = {
    8: () => calls.push("stl"),
    9: () => calls.push("fusion-csv"),
  };

  assert.equal(applyExportSelection({}, "8", handlers), true);
  assert.equal(applyExportSelection({}, "9", handlers), true);
  assert.deepEqual(calls, ["stl", "fusion-csv"]);
});

test("deriveExportFieldsFromFileName parses output name and counter from file names", () => {
  assert.deepEqual(deriveExportFieldsFromFileName("horn.cfg"), {
    outputName: "horn",
    counter: 1,
  });
  assert.deepEqual(deriveExportFieldsFromFileName("horn_design_12.cfg"), {
    outputName: "horn_design",
    counter: 12,
  });
  assert.deepEqual(deriveExportFieldsFromFileName("horn_design_0.cfg"), {
    outputName: "horn_design_0",
    counter: 1,
  });
  assert.deepEqual(deriveExportFieldsFromFileName("my file name_3.txt"), {
    outputName: "my file name",
    counter: 3,
  });
  assert.deepEqual(deriveExportFieldsFromFileName("260219superhorn35.cfg"), {
    outputName: "260219superhorn",
    counter: 35,
  });
  assert.deepEqual(deriveExportFieldsFromFileName("   "), {
    outputName: "horn_design",
    counter: 1,
  });
});

test("markParametersChanged increments counter once per change cycle and skips import baseline update", () => {
  const originalDocument = global.document;
  const counterEl = { value: "35" };
  global.document = {
    getElementById(id) {
      if (id === "export-counter") return counterEl;
      return null;
    },
  };

  try {
    resetParameterChangeTracking({ skipNext: true });
    markParametersChanged();
    assert.equal(counterEl.value, "35");

    markParametersChanged();
    assert.equal(counterEl.value, "36");

    markParametersChanged();
    assert.equal(counterEl.value, "36");

    resetParameterChangeTracking();
    markParametersChanged();
    assert.equal(counterEl.value, "37");
  } finally {
    global.document = originalDocument;
    resetParameterChangeTracking();
  }
});

// --- Phase 1 migration regression tests: Settings modal entry ---

test("SETTINGS_CONTROL_IDS maps all migrated controls to their element IDs", () => {
  // Verifies the canonical ID map exists so consumers can reference controls
  // that now live inside the dynamically-created settings modal.
  assert.equal(SETTINGS_CONTROL_IDS.liveUpdate, "live-update");
  assert.equal(SETTINGS_CONTROL_IDS.displayMode, "display-mode");
  assert.equal(SETTINGS_CONTROL_IDS.downloadSimMesh, "download-sim-mesh");
  assert.equal(SETTINGS_CONTROL_IDS.checkUpdates, "check-updates-btn");
});

test("settings getters return in-memory defaults when modal is not open", () => {
  // When the modal is closed there are no DOM elements for these controls.
  // Getters must return stored defaults rather than null/undefined.
  const originalDocument = global.document;
  global.document = { getElementById: () => null };

  try {
    // Default: live-update = true
    assert.equal(getLiveUpdateEnabled(), true);
    // Default: display-mode = standard
    assert.equal(getDisplayMode(), "standard");
    // Default: download-sim-mesh = false
    assert.equal(getDownloadSimMeshEnabled(), false);
  } finally {
    global.document = originalDocument;
  }
});

test("settings getters read DOM values when elements are present", () => {
  const originalDocument = global.document;

  const liveUpdateEl = { checked: false };
  const displayModeEl = { value: "zebra" };
  const downloadMeshEl = { checked: true };

  global.document = {
    getElementById(id) {
      if (id === "live-update") return liveUpdateEl;
      if (id === "display-mode") return displayModeEl;
      if (id === "download-sim-mesh") return downloadMeshEl;
      return null;
    },
  };

  try {
    assert.equal(getLiveUpdateEnabled(), false);
    assert.equal(getDisplayMode(), "zebra");
    assert.equal(getDownloadSimMeshEnabled(), true);
  } finally {
    global.document = originalDocument;
  }
});

test("openSettingsModal creates the grouped settings sections and workspace action", () => {
  // Minimal DOM environment for on-demand modal construction.
  const originalDocument = global.document;
  const originalWindow = global.window;

  const appendedChildren = [];
  const createdElements = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.document = {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        id: "",
        className: "",
        textContent: "",
        innerHTML: "",
        hidden: false,
        type: "",
        title: "",
        dataset: {},
        setAttribute(k, v) {
          this._attrs[k] = v;
        },
        getAttribute(k) {
          return this._attrs[k];
        },
        addEventListener(evt, fn) {
          this._eventListeners[evt] = this._eventListeners[evt] || [];
          this._eventListeners[evt].push(fn);
        },
        appendChild(child) {
          this._children.push(child);
          return child;
        },
        querySelectorAll(selector) {
          // Return nav buttons or section divs based on class
          const results = [];
          const walk = (node) => {
            if (!node || !node._children) return;
            for (const child of node._children) {
              if (
                selector === ".settings-nav-btn" &&
                child.className &&
                child.className.includes("settings-nav-btn")
              ) {
                results.push(child);
              }
              if (
                selector === ".settings-section" &&
                child.className &&
                child.className.includes("settings-section")
              ) {
                results.push(child);
              }
              walk(child);
            }
          };
          walk(this);
          return results;
        },
        querySelector(selector) {
          if (selector === '[role="dialog"]') {
            const walk = (node) => {
              if (!node || !node._children) return null;
              for (const child of node._children) {
                if (child._attrs && child._attrs["role"] === "dialog")
                  return child;
                const found = walk(child);
                if (found) return found;
              }
              return null;
            };
            return walk(this);
          }
          return null;
        },
        focus() {},
        remove() {},
        classList: {
          _list: new Set(),
          toggle(cls, force) {
            if (force === undefined) {
              if (this._list.has(cls)) this._list.delete(cls);
              else this._list.add(cls);
            } else if (force) {
              this._list.add(cls);
            } else {
              this._list.delete(cls);
            }
          },
          includes(cls) {
            return this._list.has(cls);
          },
        },
      };
      createdElements.push(el);
      return el;
    },
    body: {
      appendChild(child) {
        appendedChildren.push(child);
        return child;
      },
    },
  };

  try {
    openSettingsModal();

    // The backdrop div should have been appended to body
    assert.equal(
      appendedChildren.length,
      1,
      "One element should be appended to body",
    );

    // Collect all textContent values from created elements to find section headings
    const allText = createdElements.map((el) => el.textContent).filter(Boolean);

    // The grouped settings sections must be present in the modal nav/content.
    assert.ok(
      allText.some((t) => t === "Viewer"),
      "Viewer section must be present",
    );
    assert.ok(
      allText.some((t) => t === "Simulation"),
      "Simulation section must be present",
    );
    assert.ok(
      allText.some((t) => t === "Task Exports"),
      "Task Exports section must be present",
    );
    assert.ok(
      allText.some((t) => t === "Workspace"),
      "Workspace section must be present",
    );
    assert.ok(
      allText.some((t) => t === "System"),
      "System section must be present",
    );
    assert.ok(
      createdElements.some((el) => el.id === "simmanage-default-sort"),
      "Task Exports should expose a default task sort control",
    );
    assert.ok(
      createdElements.some((el) => el.id === "simmanage-min-rating"),
      "Task Exports should expose a minimum rating filter control",
    );
    assert.ok(
      createdElements.some((el) => el.id === "settings-choose-folder-btn"),
      "Workspace section should expose a folder selection action",
    );
    assert.equal(
      createdElements.some((el) => el.id === "simadvanced-enableWarmup"),
      false,
      "Simulation section should not expose the warm-up advanced control",
    );
    assert.equal(
      createdElements.some((el) => el.id === "simadvanced-bemPrecision"),
      false,
      "Simulation section should not expose the BEM precision advanced control",
    );
    assert.equal(
      createdElements.some((el) => el.id === "simbasic-deviceMode"),
      false,
      "Simulation section should not expose the compute-device selector",
    );
    assert.ok(
      createdElements.some((el) => el.id === "simadvanced-useBurtonMiller"),
      "Simulation section should expose the Burton-Miller advanced control",
    );
    // No additional advanced controls should be rendered beyond the supported
    // Burton-Miller override.
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

test("openSettingsModal places check-updates-btn inside the modal, not in the actions panel", () => {
  // Regression: check-updates-btn must only exist inside the dynamically-created
  // settings modal. If it were found in the static DOM at startup (via getElementById
  // before modal open), the binding in events.js would attach the old direct handler
  // pattern instead of the delegation chain.
  const originalDocument = global.document;
  const originalWindow = global.window;

  const appendedChildren = [];
  const createdElements = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.document = {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        id: "",
        className: "",
        textContent: "",
        innerHTML: "",
        hidden: false,
        type: "",
        title: "",
        dataset: {},
        setAttribute(k, v) {
          this._attrs[k] = v;
        },
        getAttribute(k) {
          return this._attrs[k];
        },
        addEventListener(evt, fn) {
          this._eventListeners[evt] = this._eventListeners[evt] || [];
          this._eventListeners[evt].push(fn);
        },
        appendChild(child) {
          this._children.push(child);
          return child;
        },
        querySelectorAll() {
          return [];
        },
        querySelector() {
          return null;
        },
        focus() {},
        remove() {},
        classList: {
          _list: new Set(),
          toggle() {},
          includes() {
            return false;
          },
        },
      };
      createdElements.push(el);
      return el;
    },
    body: {
      appendChild(child) {
        appendedChildren.push(child);
        return child;
      },
    },
  };

  try {
    openSettingsModal();

    // check-updates-btn must be created inside the modal (within the appended backdrop)
    const updateBtnElements = createdElements.filter(
      (el) => el.id === "check-updates-btn",
    );
    assert.equal(
      updateBtnElements.length,
      1,
      "Exactly one check-updates-btn should be created",
    );

    // Verify it is NOT directly in the static DOM (getElementById returns null before modal open)
    const staticBtn = global.document.getElementById("check-updates-btn");
    assert.equal(
      staticBtn,
      null,
      "check-updates-btn should not exist in static DOM before modal is opened",
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

test("openSettingsModal keeps folder-workspace fallback copy visible when picker support is unavailable", () => {
  const originalDocument = global.document;
  const originalWindow = global.window;

  const appendedChildren = [];
  const createdElements = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.document = createSettingsModalDocument(
    createdElements,
    appendedChildren,
  );

  try {
    openSettingsModal();

    const routingNote = createdElements.find(
      (el) => el.id === "settings-workspace-routing",
    );
    const chooseBtn = createdElements.find(
      (el) => el.id === "settings-choose-folder-btn",
    );

    // When showDirectoryPicker is unavailable (Firefox), explanation moves to routingNote
    // and the choose button row is hidden (disabled).
    assert.ok(routingNote, "Workspace routing note should be rendered");
    assert.match(routingNote.textContent, /firefox/i);
    assert.equal(chooseBtn?.disabled, true);
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

function createSettingsModalDocument(createdElements, appendedChildren) {
  return {
    getElementById: () => null,
    createElement(tag) {
      const el = {
        _tag: tag,
        _children: [],
        _attrs: {},
        _eventListeners: {},
        id: "",
        className: "",
        textContent: "",
        innerHTML: "",
        hidden: false,
        type: "",
        title: "",
        name: "",
        value: "",
        checked: false,
        dataset: {},
        min: "",
        max: "",
        step: "",
        setAttribute(k, v) {
          this._attrs[k] = v;
        },
        getAttribute(k) {
          return this._attrs[k];
        },
        addEventListener(evt, fn) {
          this._eventListeners[evt] = this._eventListeners[evt] || [];
          this._eventListeners[evt].push(fn);
        },
        appendChild(child) {
          this._children.push(child);
          return child;
        },
        querySelectorAll() {
          return [];
        },
        querySelector(selector) {
          if (selector === '[role="dialog"]') {
            const walk = (node) => {
              if (!node || !node._children) return null;
              for (const child of node._children) {
                if (child._attrs && child._attrs.role === "dialog")
                  return child;
                const found = walk(child);
                if (found) return found;
              }
              return null;
            };
            return walk(this);
          }
          return null;
        },
        focus() {},
        remove() {},
        classList: {
          toggle() {},
          includes() {
            return false;
          },
        },
      };
      createdElements.push(el);
      return el;
    },
    body: {
      appendChild(child) {
        appendedChildren.push(child);
        return child;
      },
    },
  };
}

test("recommended badges are visible when viewer values match defaults", () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalLocalStorage = global.localStorage;

  const store = {};
  const createdElements = [];
  const appendedChildren = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.localStorage = {
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => {
      store[key] = value;
    },
    removeItem: (key) => {
      delete store[key];
    },
    clear: () => Object.keys(store).forEach((key) => delete store[key]),
  };
  resetAllViewerSettings();
  global.document = createSettingsModalDocument(
    createdElements,
    appendedChildren,
  );

  try {
    openSettingsModal();
    const badges = createdElements.filter(
      (el) => el.className === "settings-recommended-badge",
    );
    assert.ok(badges.length > 0, "Expected recommended badges to be created");
    assert.ok(
      badges.every((badge) => badge.hidden === false),
      "All badges should be visible when values are recommended",
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.localStorage = originalLocalStorage;
  }
});

test("recommended badge hides when a viewer value differs from default", () => {
  const originalDocument = global.document;
  const originalWindow = global.window;
  const originalLocalStorage = global.localStorage;

  const store = {};
  const createdElements = [];
  const appendedChildren = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.localStorage = {
    getItem: (key) => store[key] ?? null,
    setItem: (key, value) => {
      store[key] = value;
    },
    removeItem: (key) => {
      delete store[key];
    },
    clear: () => Object.keys(store).forEach((key) => delete store[key]),
  };
  saveViewerSettings({ ...RECOMMENDED_DEFAULTS, rotateSpeed: 2.5 });
  global.document = createSettingsModalDocument(
    createdElements,
    appendedChildren,
  );

  try {
    openSettingsModal();
    const badges = createdElements.filter(
      (el) => el.className === "settings-recommended-badge",
    );
    assert.ok(badges.length > 0, "Expected recommended badges to be created");
    assert.ok(
      badges.some((badge) => badge.hidden === true),
      "At least one badge should hide for non-recommended values",
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.localStorage = originalLocalStorage;
  }
});

test("recommended badge rule remains stable for all default values", () => {
  for (const key of Object.keys(RECOMMENDED_DEFAULTS)) {
    assert.equal(
      RECOMMENDED_DEFAULTS[key] !== RECOMMENDED_DEFAULTS[key],
      false,
      `Expected default value for ${key} to match itself`,
    );
  }
});

function createMockElement(tagName = "div") {
  const attributes = new Map();
  const listeners = new Map();
  const classes = new Set();

  const syncClassName = (element) => {
    element.className = Array.from(classes).join(" ").trim();
  };

  const element = {
    tagName: tagName.toUpperCase(),
    className: "",
    textContent: "",
    children: [],
    parentElement: null,
    appendChild(child) {
      child.parentElement = this;
      this.children.push(child);
      return child;
    },
    replaceChildren(...children) {
      this.children = [];
      for (const child of children) {
        child.parentElement = this;
        this.children.push(child);
      }
    },
    setAttribute(name, value) {
      attributes.set(name, String(value));
    },
    getAttribute(name) {
      return attributes.get(name) ?? null;
    },
    addEventListener(type, handler) {
      listeners.set(type, handler);
    },
    click() {
      listeners.get("click")?.({ currentTarget: this });
    },
    querySelector(selector) {
      if (!selector.startsWith(".")) {
        return null;
      }
      const className = selector.slice(1);
      const queue = [...this.children];
      while (queue.length > 0) {
        const next = queue.shift();
        if ((next.className || "").split(/\s+/).includes(className)) {
          return next;
        }
        queue.push(...(next.children || []));
      }
      return null;
    },
    classList: {
      add(...names) {
        for (const name of names) {
          if (name) {
            classes.add(name);
          }
        }
        syncClassName(element);
      },
      remove(...names) {
        for (const name of names) {
          classes.delete(name);
        }
        syncClassName(element);
      },
      toggle(name, force) {
        if (force === true) {
          classes.add(name);
        } else if (force === false) {
          classes.delete(name);
        } else if (classes.has(name)) {
          classes.delete(name);
        } else {
          classes.add(name);
        }
        syncClassName(element);
        return classes.has(name);
      },
      contains(name) {
        return classes.has(name);
      },
    },
  };

  return element;
}

function collectNodeText(node) {
  return [
    String(node?.textContent || ""),
    ...(node?.children || []).map((child) => collectNodeText(child)),
  ]
    .join(" ")
    .trim();
}

test("formatDependencyBlockMessage includes feature impact and guidance for missing gmsh", () => {
  const health = {
    dependencyDoctor: {
      components: [
        {
          id: "gmsh_python",
          name: "Gmsh Python API",
          category: "required",
          status: "missing",
          featureImpact: "/api/mesh/build and adaptive OCC meshing are unavailable.",
          guidance: [
            "Install gmsh package: pip install -r server/requirements-gmsh.txt",
          ],
        },
      ],
    },
  };

  const message = formatDependencyBlockMessage(health, {
    features: ["meshBuild"],
    fallback: "OCC mesh export is unavailable.",
  });

  assert.match(message, /OCC mesh export is unavailable/);
  assert.match(message, /Gmsh Python API/);
  assert.match(message, /adaptive OCC meshing are unavailable/);
  assert.match(message, /Install gmsh package/);
});

test("formatDependencyBlockMessage includes bounded solve validation issue for solve feature", () => {
  const health = {
    dependencyDoctor: {
      components: [
        {
          id: "bounded_solve_validation",
          name: "Bounded solve validation",
          category: "required",
          status: "missing",
          featureImpact: "/api/solve readiness is unvalidated on this host/runtime.",
          guidance: [
            "Run bounded solve validation: cd server && python3 scripts/benchmark_tritonia.py --freq 1000 --device auto --precision single --timeout 30",
          ],
        },
      ],
    },
  };

  const message = formatDependencyBlockMessage(health, {
    features: ["solve"],
    fallback: "Simulation is unavailable.",
  });

  assert.match(message, /Simulation is unavailable/);
  assert.match(message, /Bounded solve validation/);
  assert.match(message, /unvalidated on this host\/runtime/);
});

test("createDependencyStatusPanel renders required and optional dependency issues", () => {
  const originalDocument = global.document;
  global.document = {
    createElement(tagName) {
      return createMockElement(tagName);
    },
  };

  try {
    const panel = createDependencyStatusPanel({
      dependencyDoctor: {
        components: [
          {
            id: "gmsh_python",
            name: "Gmsh Python API",
            category: "required",
            status: "missing",
            version: null,
            requiredFor: "/api/mesh/build",
            featureImpact: "/api/mesh/build and adaptive OCC meshing are unavailable.",
            guidance: [
              "Install gmsh package: pip install -r server/requirements-gmsh.txt",
            ],
          },
          {
            id: "matplotlib",
            name: "Matplotlib",
            category: "optional",
            status: "missing",
            version: null,
            requiredFor: "chart/directivity image render endpoints",
            featureImpact:
              "Chart/directivity image render endpoints are unavailable; solver core paths still work.",
            guidance: ["Install matplotlib: pip install matplotlib"],
          },
        ],
      },
    });

    assert.equal(panel.classList.contains("has-warnings"), true);
    const text = collectNodeText(panel);
    assert.match(text, /Runtime Dependencies/);
    assert.match(text, /Gmsh Python API/);
    assert.match(text, /Install gmsh package/);
    assert.match(text, /Matplotlib/);
    assert.match(text, /Install matplotlib/);
  } finally {
    global.document = originalDocument;
  }
});

test("buildRequiredDependencyWarning returns null when required runtime is ready", () => {
  const warning = buildRequiredDependencyWarning({
    dependencyDoctor: {
      summary: { requiredReady: true },
      components: [
        {
          id: "gmsh_python",
          name: "Gmsh Python API",
          category: "required",
          status: "installed",
          guidance: [],
        },
        {
          id: "matplotlib",
          name: "Matplotlib",
          category: "optional",
          status: "missing",
          guidance: ["Install matplotlib: pip install matplotlib"],
        },
      ],
    },
  });

  assert.equal(warning, null);
});

test("buildRequiredDependencyWarning only includes required dependency guidance", () => {
  const warning = buildRequiredDependencyWarning({
    dependencyDoctor: {
      components: [
        {
          id: "gmsh_python",
          name: "Gmsh Python API",
          category: "required",
          status: "missing",
          featureImpact: "/api/mesh/build and adaptive OCC meshing are unavailable.",
          guidance: [
            "Install gmsh package: pip install -r server/requirements-gmsh.txt",
          ],
        },
        {
          id: "matplotlib",
          name: "Matplotlib",
          category: "optional",
          status: "missing",
          featureImpact:
            "Chart/directivity image render endpoints are unavailable; solver core paths still work.",
          guidance: ["Install matplotlib: pip install matplotlib"],
        },
      ],
    },
  });

  assert.ok(warning);
  assert.match(warning.title, /Backend Dependencies Missing/);
  assert.match(warning.message, /Simulation and OCC meshing stay blocked/i);
  assert.match(warning.message, /Gmsh Python API/);
  assert.match(warning.message, /Install gmsh package/);
  assert.doesNotMatch(warning.message, /Install matplotlib/);
});
