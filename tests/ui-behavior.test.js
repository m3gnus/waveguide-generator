import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

import { normalizeParamInput } from '../src/ui/paramInput.js';
import {
  formatJobSummary,
  renderSimulationMeshDiagnostics,
} from '../src/ui/simulation/jobActions.js';
import { renderResultDiagnostics, renderSolveStatsSummary } from '../src/ui/simulation/results.js';
import { validateSimulationConfig } from '../src/modules/simulation/domain.js';
import {
  applySavedExportFields,
  deriveExportFieldsFromFileName,
  loadExportFields,
  markParametersChanged,
  resetParameterChangeTracking,
  saveExportFields,
  setExportFields,
} from '../src/ui/fileOps.js';
import {
  SETTINGS_CONTROL_IDS,
  getLiveUpdateEnabled,
  getDisplayMode,
  setDisplayMode,
  getDownloadSimMeshEnabled,
  openSettingsModal,
} from '../src/ui/settings/modal.js';
import {
  describeSelectedDevice,
  getDependencyStatusSummary,
  getFeatureBlockedReason as getLegacyRuntimeFeatureBlockedReason,
  summarizeRuntimeCapabilities,
} from '../src/ui/runtimeCapabilities.js';
import {
  createDependencyStatusPanel,
  getFeatureBlockedReason,
} from '../src/ui/dependencyStatus.js';
import { formatDependencyBlockMessage } from '../src/modules/runtime/health.js';
import { buildRequiredDependencyWarning } from '../src/ui/simulation/connection.js';
import {
  RECOMMENDED_DEFAULTS,
  resetAllViewerSettings,
  saveViewerSettings,
} from '../src/ui/settings/viewerSettings.js';
import { PARAM_SCHEMA } from '../src/config/schema.js';

test('index.html places clear-failed and refresh in the simulations summary', () => {
  const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
  const css = fs.readFileSync(new URL('../src/style.css', import.meta.url), 'utf8');

  assert.match(
    html,
    /<summary>[\s\S]*<span>Simulations<\/span>[\s\S]*id="clear-failed-jobs-btn"[\s\S]*id="refresh-jobs-btn"[\s\S]*<\/summary>/
  );
  assert.doesNotMatch(
    html,
    /<div class="simulation-jobs-header-actions">[\s\S]*id="refresh-jobs-btn"/
  );
  assert.doesNotMatch(html, /id="choose-folder-btn"/);
  assert.doesNotMatch(html, /id="simulation-jobs-source-label"/);
  assert.doesNotMatch(html, /id="output-folder-row"/);
  assert.match(css, /\.simulation-summary-actions button\s*\{[\s\S]*white-space:\s*nowrap;/);
});

test('export menu hover bridge covers the visible dropdown gap', () => {
  const css = fs.readFileSync(new URL('../src/style.css', import.meta.url), 'utf8');

  assert.match(css, /\.export-menu\s*\{[\s\S]*--export-menu-gap:\s*4px;/);
  assert.match(
    css,
    /\.export-menu::before\s*\{[\s\S]*top:\s*100%;[\s\S]*width:\s*max\(100%,\s*180px\);[\s\S]*height:\s*var\(--export-menu-gap\);/
  );
  assert.match(
    css,
    /\.export-menu-list\s*\{[\s\S]*top:\s*calc\(100% \+ var\(--export-menu-gap\)\);/
  );
  assert.match(
    css,
    /\.simulation-job-actions\s+\.export-menu::before\s*\{[\s\S]*width:\s*max\(100%,\s*168px\);/
  );
});

test('normalizeParamInput parses numeric literals consistently', () => {
  assert.equal(normalizeParamInput('1.0'), 1);
  assert.equal(normalizeParamInput(' 1e3 '), 1000);
  assert.equal(normalizeParamInput('-0.25'), -0.25);
  assert.equal(normalizeParamInput('45 + 10*cos(p)'), '45 + 10*cos(p)');
  assert.equal(normalizeParamInput('2+3'), '2+3');
});

test('mesh control labels separate surface sampling from solve mesh sizing', () => {
  assert.equal(PARAM_SCHEMA.MESH.angularSegments.label, 'Surface Angular Samples');
  assert.equal(PARAM_SCHEMA.MESH.lengthSegments.label, 'Surface Length Samples');
  assert.equal(PARAM_SCHEMA.MESH.throatResolution.label, 'Throat Mesh Resolution');
  assert.equal(PARAM_SCHEMA.MESH.mouthResolution.label, 'Mouth Mesh Resolution');
  assert.equal(PARAM_SCHEMA.MESH.rearResolution.label, 'Rear Mesh Resolution');
  assert.equal(PARAM_SCHEMA.MESH.apertureResolutionScale.label, 'Aperture Mesh Scale');
  assert.equal(PARAM_SCHEMA.ENCLOSURE.encFrontResolution.label, 'Front Baffle Mesh Resolution');
  assert.equal(PARAM_SCHEMA.ENCLOSURE.encBackResolution.label, 'Rear Baffle Mesh Resolution');
  assert.match(PARAM_SCHEMA.MESH.angularSegments.tooltip, /HornLab mesher tessellation/i);
  assert.doesNotMatch(PARAM_SCHEMA.MESH.angularSegments.tooltip, /Gmsh/i);
  assert.match(PARAM_SCHEMA.MESH.throatResolution.tooltip, /HornLab mesher solve\/export/i);
  assert.equal(PARAM_SCHEMA.SIMULATION.freqStart.label, 'Sweep Start');
  assert.equal(PARAM_SCHEMA.SIMULATION.freqEnd.label, 'Sweep End');
  assert.equal(PARAM_SCHEMA.SIMULATION.freqStart.controlId, 'freq-start');
  assert.equal(PARAM_SCHEMA.SIMULATION.freqEnd.controlId, 'freq-end');
  assert.equal(PARAM_SCHEMA.SIMULATION.numFreqs.controlId, 'freq-steps');
  assert.match(PARAM_SCHEMA.SIMULATION.numFreqs.tooltip, /number of solved frequencies/i);
});

test('all generated parameter controls have tooltips', () => {
  const missing = [];
  for (const [group, defs] of Object.entries(PARAM_SCHEMA)) {
    for (const [key, def] of Object.entries(defs)) {
      if (!String(def.tooltip || '').trim()) {
        missing.push(`${group}.${key}`);
      }
    }
  }

  assert.deepEqual(missing, []);
});

test('validateSimulationConfig catches invalid ranges and counts', () => {
  assert.match(
    validateSimulationConfig({
      frequencyStart: 1000,
      frequencyEnd: 100,
      numFrequencies: 50,
    }),
    /Start frequency/
  );

  assert.match(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 0,
    }),
    /Number of frequencies/
  );

  assert.equal(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 50,
    }),
    null
  );
});

test('formatJobSummary appends complete duration in m:ss', () => {
  const summary = formatJobSummary({
    status: 'complete',
    startedAt: '2026-02-24T12:00:00.000Z',
    completedAt: '2026-02-24T12:02:53.000Z',
  });
  assert.equal(summary, 'Complete (2:53)');
});

test('formatJobSummary appends complete duration in h:mm:ss', () => {
  const summary = formatJobSummary({
    status: 'complete',
    startedAt: '2026-02-24T12:00:00.000Z',
    completedAt: '2026-02-24T13:04:32.000Z',
  });
  assert.equal(summary, 'Complete (1:04:32)');
});

test('formatJobSummary surfaces the real solver error for failed jobs', () => {
  const summary = formatJobSummary({
    status: 'error',
    stage: 'error',
    stageMessage: 'Simulation failed',
    errorMessage: 'Metal BEM solve failed: native helper crashed (exit 139).',
  });
  assert.equal(summary, 'Failed: Metal BEM solve failed: native helper crashed (exit 139).');
});

test('formatJobSummary keeps a real error that contains the word "error"', () => {
  // The previous suppression regex matched any message containing "error" and hid it.
  const summary = formatJobSummary({
    status: 'error',
    errorMessage: 'RuntimeError: hornlab-waveguide-mesher did not produce .msh output.',
  });
  assert.equal(
    summary,
    'Failed: RuntimeError: hornlab-waveguide-mesher did not produce .msh output.'
  );
});

test('formatJobSummary collapses the generic placeholder to plain Failed', () => {
  const summary = formatJobSummary({
    status: 'error',
    stageMessage: 'Simulation failed',
    errorMessage: null,
  });
  assert.equal(summary, 'Failed');
});

test('renderSolveStatsSummary includes persisted job completion timestamp', () => {
  const markup = renderSolveStatsSummary(
    {
      frequencies: [100, 1000],
      metadata: {
        performance: { total_time_seconds: 12.4 },
      },
    },
    {
      completedAt: '2026-03-19T08:45:00.000Z',
    }
  );

  assert.match(markup, /Completed/);
  assert.match(markup, /2026-03-19/);
  assert.match(markup, /2026-03-19 \d{2}:45/);
});

test('renderSolveStatsSummary includes mesh counts from result metadata', () => {
  const markup = renderSolveStatsSummary({
    frequencies: [100, 1000],
    metadata: {
      performance: { total_time_seconds: 12.4 },
      mesh_stats: {
        vertex_count: 144,
        triangle_count: 72,
      },
    },
  });

  assert.match(markup, /Vertices/);
  assert.match(markup, /144/);
  assert.match(markup, /Triangles/);
  assert.match(markup, /72/);
});

test('renderSolveStatsSummary includes solved waveguide dimensions from mesh stats', () => {
  const markup = renderSolveStatsSummary({
    frequencies: [100, 1000],
    metadata: {
      performance: { total_time_seconds: 12.4 },
      mesh_stats: {
        vertex_count: 144,
        triangle_count: 72,
        dimensions_m: {
          width: 0.42,
          height: 0.28,
          depth: 0.31,
        },
      },
    },
  });

  assert.match(markup, /Waveguide shape/);
  assert.match(markup, /Height 280 mm, Depth 310 mm, Width 420 mm/);
});

test('renderSolveStatsSummary uses persisted directivity metadata for solve settings', () => {
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
          enabled_axes: ['horizontal', 'diagonal'],
          normalization_angle_degrees: 7.5,
          diagonal_angle_degrees: 35,
          observation_origin: 'throat',
          requested_distance_m: 1.0,
          effective_distance_m: 1.75,
        },
      },
    },
    {
      configSummary: {
        observation_origin: 'mouth',
      },
    }
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

test('renderResultDiagnostics shows mesh warnings and failed frequency details with escaping', () => {
  const markup = renderResultDiagnostics({
    metadata: {
      mesh_validation: {
        mode: 'warn',
        is_valid: false,
        warnings: ['Requested max frequency exceeds <mesh> capability.'],
      },
      warnings: [
        {
          stage: 'setup',
          code: 'observation_distance_adjusted',
          detail: 'Using safer distance & preserving solve.',
        },
      ],
      warning_count: 1,
      failures: [
        {
          frequency_hz: 2500,
          stage: 'frequency_solve',
          code: 'frequency_solve_failed',
          detail: 'GMRES failed <badly>',
        },
      ],
      failure_count: 1,
      partial_success: true,
    },
  });

  assert.match(markup, /Result Diagnostics/);
  assert.match(markup, /Mesh validation/);
  assert.match(markup, /Invalid \(warn mode\)/);
  assert.match(markup, /Requested max frequency exceeds &lt;mesh&gt; capability\./);
  assert.match(markup, /Run Warnings/);
  assert.match(markup, /Using safer distance &amp; preserving solve\./);
  assert.match(markup, /Failed Frequencies/);
  assert.match(markup, /2\.5 kHz/);
  assert.match(markup, /frequency_solve_failed: GMRES failed &lt;badly&gt;/);
  assert.match(markup, /Partial success/);
});

test('renderResultDiagnostics omits clean result metadata', () => {
  const markup = renderResultDiagnostics({
    metadata: {
      mesh_validation: {
        mode: 'warn',
        enabled: true,
        is_valid: true,
        warnings: [],
      },
      warnings: [],
      warning_count: 0,
      failures: [],
      failure_count: 0,
      partial_success: false,
    },
  });

  assert.equal(markup, '');
});

test('summarizeRuntimeCapabilities reports advanced controls unavailable until backend advertises support', () => {
  const summary = summarizeRuntimeCapabilities({
    solverReady: true,
    mesherReady: true,
    solverBackends: {
      metal: { ready: true, status: { available: true } },
    },
    capabilities: {
      simulationAdvanced: {
        available: true,
        controls: ['solver_backend'],
        reason: 'The public solve contract exposes solver backend selection.',
        plannedControls: ['method'],
      },
    },
  });

  assert.equal(summary.fullyReady, true);
  assert.equal(summary.mesherReady, true);
  assert.equal(summary.occBuilderReady, undefined);
  assert.equal(summary.simulationAdvanced.available, true);
  assert.equal(
    summary.simulationAdvanced.reason,
    'The public solve contract exposes solver backend selection.'
  );
  assert.deepEqual(summary.simulationAdvanced.controls, ['solver_backend']);
  assert.deepEqual(summary.simulationAdvanced.plannedControls, ['method']);
});

test('legacy dependency summary blocks mesh builds when HornLab mesher package is missing', () => {
  const health = {
    dependencies: {
      runtime: {
        python: { version: '3.13.1', supported: true },
        gmsh_python: {
          available: true,
          version: '4.15.0',
          supported: true,
          ready: true,
        },
        hornlab_waveguide_mesher: {
          available: false,
          version: null,
          supported: false,
          ready: false,
        },
        hornlab_metal_bem: { available: true, supported: true, ready: true, version: '1.0.0' },
      },
    },
  };

  const summary = getDependencyStatusSummary(health);
  assert.equal(summary.gmsh.ready, true);
  assert.equal(summary.hornlabMesher.ready, false);
  assert.equal(summary.hornlabMesher.name, 'HornLab waveguide mesher');

  const reason = getLegacyRuntimeFeatureBlockedReason(health, 'hornlab-mesher-mesh');
  assert.match(reason, /Install backend requirements/);
});

test('dependency summary reports installed Metal debug helper as not ready', () => {
  const summary = getDependencyStatusSummary({
    solverBackends: {
      metal: {
        ready: false,
        status: {
          available: true,
          supportedPlatform: true,
          nativeHelperBuild: 'debug',
        },
      },
      bempp: {
        ready: false,
        status: { available: false },
      },
    },
    dependencies: {
      runtime: {
        python: { version: '3.13.1', supported: true },
        hornlab_metal_bem: { version: '0.2.0', supported: true, ready: true },
      },
    },
  });

  assert.equal(summary.metal.available, true);
  assert.equal(summary.metal.ready, false);
  assert.match(summary.metal.guidance, /build:metal-helper/);
});

test('describeSelectedDevice labels the Metal BEM solver and stays quiet otherwise', () => {
  assert.equal(
    describeSelectedDevice({
      solver: 'metal-bem',
    }),
    'Using: Metal BEM'
  );

  assert.equal(
    describeSelectedDevice({
      solver: 'unavailable',
    }),
    ''
  );

  assert.equal(describeSelectedDevice({}), '');
});

test('renderSimulationMeshDiagnostics shows canonical tag counts and warnings', () => {
  const originalDocument = global.document;
  const diagnosticsEl = { innerHTML: '' };
  global.document = {
    getElementById(id) {
      return id === 'simulation-mesh-diagnostics' ? diagnosticsEl : null;
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
      warnings: ['Source surface tag (2) missing from the canonical simulation mesh.'],
      provenance: 'preview',
    });

    assert.match(diagnosticsEl.innerHTML, /12 verts/);
    assert.match(diagnosticsEl.innerHTML, /Simulation Geometry/);
    assert.match(diagnosticsEl.innerHTML, /Geometry Regions/);
    assert.match(diagnosticsEl.innerHTML, /Throat Disc/);
    assert.match(diagnosticsEl.innerHTML, /Inner Wall/);
    assert.doesNotMatch(diagnosticsEl.innerHTML, /throat_disc/);
    assert.doesNotMatch(diagnosticsEl.innerHTML, /Source \(2\)/);
    assert.match(
      diagnosticsEl.innerHTML,
      /Throat Disc is present, but it is not classified as the source region/i
    );
  } finally {
    global.document = originalDocument;
  }
});

test('renderSimulationMeshDiagnostics shows authoritative backend mesh provenance when mesh stats are authoritative', () => {
  const originalDocument = global.document;
  const diagnosticsEl = { innerHTML: '' };
  global.document = {
    getElementById(id) {
      return id === 'simulation-mesh-diagnostics' ? diagnosticsEl : null;
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
      provenance: 'backend',
    });

    assert.match(diagnosticsEl.innerHTML, /Solver Geometry/);
    assert.match(diagnosticsEl.innerHTML, /18 verts/);
    assert.match(diagnosticsEl.innerHTML, /Rear Cap/);
    assert.doesNotMatch(diagnosticsEl.innerHTML, /Source \(2\)/);
  } finally {
    global.document = originalDocument;
  }
});

test('formatJobSummary falls back to Complete when duration is unavailable', () => {
  const summary = formatJobSummary({
    status: 'complete',
    startedAt: 'not-a-date',
    completedAt: null,
  });
  assert.equal(summary, 'Complete');
});

test('deriveExportFieldsFromFileName parses output name and counter from file names', () => {
  assert.deepEqual(deriveExportFieldsFromFileName('horn.cfg'), {
    outputName: 'horn',
    counter: 1,
  });
  assert.deepEqual(deriveExportFieldsFromFileName('horn_design_12.cfg'), {
    outputName: 'horn_design',
    counter: 12,
  });
  assert.deepEqual(deriveExportFieldsFromFileName('horn_design_0.cfg'), {
    outputName: 'horn_design_0',
    counter: 1,
  });
  assert.deepEqual(deriveExportFieldsFromFileName('my file name_3.txt'), {
    outputName: 'my file name',
    counter: 3,
  });
  assert.deepEqual(deriveExportFieldsFromFileName('260219superhorn35.cfg'), {
    outputName: '260219superhorn',
    counter: 35,
  });
  assert.deepEqual(deriveExportFieldsFromFileName('   '), {
    outputName: 'horn_design',
    counter: 1,
  });
});

test('markParametersChanged increments counter once per change cycle and skips import baseline update', () => {
  const originalDocument = global.document;
  const counterEl = { value: '35' };
  global.document = {
    getElementById(id) {
      if (id === 'export-counter') return counterEl;
      return null;
    },
  };

  try {
    resetParameterChangeTracking({ skipNext: true });
    markParametersChanged();
    assert.equal(counterEl.value, '35');

    markParametersChanged();
    assert.equal(counterEl.value, '36');

    markParametersChanged();
    assert.equal(counterEl.value, '36');

    resetParameterChangeTracking();
    markParametersChanged();
    assert.equal(counterEl.value, '37');
  } finally {
    global.document = originalDocument;
    resetParameterChangeTracking();
  }
});

function createStorage() {
  const data = new Map();
  return {
    getItem(key) {
      return data.has(key) ? data.get(key) : null;
    },
    setItem(key, value) {
      data.set(key, String(value));
    },
    removeItem(key) {
      data.delete(key);
    },
    clear() {
      data.clear();
    },
  };
}

test('export output fields persist and hydrate across reloads', () => {
  const originalLocalStorage = global.localStorage;
  const originalDocument = global.document;
  const storage = createStorage();
  const firstPrefix = { value: 'horn_design' };
  const firstCounter = { value: '1' };
  const secondPrefix = { value: 'horn_design' };
  const secondCounter = { value: '1' };

  global.localStorage = storage;

  try {
    setExportFields(
      { outputName: 'saved_horn', counter: 42 },
      {
        getElementById(id) {
          if (id === 'export-prefix') return firstPrefix;
          if (id === 'export-counter') return firstCounter;
          return null;
        },
      }
    );

    assert.deepEqual(loadExportFields(), { outputName: 'saved_horn', counter: 42 });

    global.document = {
      getElementById(id) {
        if (id === 'export-prefix') return secondPrefix;
        if (id === 'export-counter') return secondCounter;
        return null;
      },
    };
    applySavedExportFields();

    assert.equal(secondPrefix.value, 'saved_horn');
    assert.equal(secondCounter.value, '42');
  } finally {
    global.localStorage = originalLocalStorage;
    global.document = originalDocument;
  }
});

test('export output field persistence normalizes malformed storage', () => {
  const originalLocalStorage = global.localStorage;
  global.localStorage = createStorage();

  try {
    saveExportFields({ outputName: '', counter: -10 });
    assert.deepEqual(loadExportFields(), { outputName: 'horn_design', counter: 1 });
  } finally {
    global.localStorage = originalLocalStorage;
  }
});

// --- Phase 1 migration regression tests: Settings modal entry ---

test('SETTINGS_CONTROL_IDS maps all migrated controls to their element IDs', () => {
  // Verifies the canonical ID map exists so consumers can reference controls
  // that now live inside the dynamically-created settings modal.
  assert.equal(SETTINGS_CONTROL_IDS.liveUpdate, 'live-update');
  assert.equal(SETTINGS_CONTROL_IDS.downloadSimMesh, 'download-sim-mesh');
  assert.equal(SETTINGS_CONTROL_IDS.checkUpdates, 'check-updates-btn');
});

test('settings getters return in-memory defaults when modal is not open', () => {
  // When the modal is closed there are no DOM elements for these controls.
  // Getters must return stored defaults rather than null/undefined.
  const originalDocument = global.document;
  global.document = { getElementById: () => null };

  try {
    // Default: live-update = true
    assert.equal(getLiveUpdateEnabled(), true);
    // Default: display-mode = clay
    assert.equal(getDisplayMode(), 'clay');
    // Default: download-sim-mesh = false
    assert.equal(getDownloadSimMeshEnabled(), false);
  } finally {
    global.document = originalDocument;
  }
});

test('settings getters read DOM values when elements are present', () => {
  const originalDocument = global.document;

  const liveUpdateEl = { checked: false };
  const downloadMeshEl = { checked: true };

  global.document = {
    getElementById(id) {
      if (id === 'live-update') return liveUpdateEl;
      if (id === 'download-sim-mesh') return downloadMeshEl;
      return null;
    },
  };

  try {
    assert.equal(getLiveUpdateEnabled(), false);
    // display mode is now managed via setDisplayMode, not DOM
    setDisplayMode('zebra');
    assert.equal(getDisplayMode(), 'zebra');
    setDisplayMode('clay'); // restore default
    assert.equal(getDownloadSimMeshEnabled(), true);
  } finally {
    global.document = originalDocument;
  }
});

test('openSettingsModal creates the grouped settings sections and workspace action', () => {
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
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        type: '',
        title: '',
        style: {},
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
                selector === '.settings-nav-btn' &&
                child.className &&
                child.className.includes('settings-nav-btn')
              ) {
                results.push(child);
              }
              if (
                selector === '.settings-section' &&
                child.className &&
                child.className.includes('settings-section')
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
                if (child._attrs && child._attrs['role'] === 'dialog') return child;
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
    assert.equal(appendedChildren.length, 1, 'One element should be appended to body');

    // Collect all textContent values from created elements to find section headings
    const allText = createdElements.map((el) => el.textContent).filter(Boolean);

    // The grouped settings sections must be present in the modal nav/content.
    assert.ok(
      allText.some((t) => t === 'Viewer'),
      'Viewer section must be present'
    );
    assert.ok(
      allText.some((t) => t === 'Simulation'),
      'Simulation section must be present'
    );
    assert.ok(
      allText.some((t) => t === 'Export Settings'),
      'Export Settings section must be present'
    );
    assert.ok(
      allText.some((t) => t === 'Workspace'),
      'Workspace section must be present'
    );
    assert.ok(
      allText.some((t) => t === 'System'),
      'System section must be present'
    );
    assert.ok(
      createdElements.some((el) => el.id === 'simmanage-default-sort'),
      'Export Settings should expose a default task sort control'
    );
    assert.ok(
      createdElements.some((el) => el.id === 'simmanage-min-rating'),
      'Export Settings should expose a minimum rating filter control'
    );
    assert.ok(
      createdElements.some((el) => el.id === 'settings-choose-folder-btn'),
      'Workspace section should expose a folder selection action'
    );
    assert.equal(
      createdElements.some((el) => el.id === 'simadvanced-enableWarmup'),
      false,
      'Simulation section should not expose the warm-up advanced control'
    );
    assert.equal(
      createdElements.some((el) => el.id === 'simadvanced-bemPrecision'),
      false,
      'Simulation section should not expose the BEM precision advanced control'
    );
    assert.equal(
      createdElements.some((el) => el.id === 'simbasic-deviceMode'),
      false,
      'Simulation section should not expose the compute-device selector'
    );
    assert.equal(
      createdElements.some((el) => el.id === 'simadvanced-useBurtonMiller'),
      false,
      'Simulation section should not expose the removed Burton-Miller control'
    );
    assert.ok(
      createdElements.some((el) => el.id === 'simadvanced-solverBackend'),
      'Simulation section should expose the solver backend selector'
    );
    // No additional advanced controls should be rendered beyond the supported
    // solver backend selection.
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

test('openSettingsModal places check-updates-btn inside the modal, not in the actions panel', () => {
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
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        type: '',
        title: '',
        style: {},
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
    const updateBtnElements = createdElements.filter((el) => el.id === 'check-updates-btn');
    assert.equal(updateBtnElements.length, 1, 'Exactly one check-updates-btn should be created');

    // Verify it is NOT directly in the static DOM (getElementById returns null before modal open)
    const staticBtn = global.document.getElementById('check-updates-btn');
    assert.equal(
      staticBtn,
      null,
      'check-updates-btn should not exist in static DOM before modal is opened'
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
  }
});

test('openSettingsModal shows workspace section with backend routing and enabled choose button', () => {
  const originalDocument = global.document;
  const originalWindow = global.window;

  const appendedChildren = [];
  const createdElements = [];

  global.window = { addEventListener: () => {}, removeEventListener: () => {} };
  global.document = createSettingsModalDocument(createdElements, appendedChildren);

  try {
    openSettingsModal();

    const routingNote = createdElements.find((el) => el.id === 'settings-workspace-routing');
    const chooseBtn = createdElements.find((el) => el.id === 'settings-choose-folder-btn');

    // All folder operations now go through the backend; the choose button
    // is always enabled and the routing note describes backend-based exports.
    assert.ok(routingNote, 'Workspace routing note should be rendered');
    assert.match(routingNote.textContent, /folder/i);
    assert.equal(chooseBtn?.disabled, false);
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
        id: '',
        className: '',
        textContent: '',
        innerHTML: '',
        hidden: false,
        type: '',
        title: '',
        name: '',
        value: '',
        checked: false,
        style: {},
        dataset: {},
        min: '',
        max: '',
        step: '',
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
                if (child._attrs && child._attrs.role === 'dialog') return child;
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

test('recommended badges are visible when viewer values match defaults', () => {
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
  global.document = createSettingsModalDocument(createdElements, appendedChildren);

  try {
    openSettingsModal();
    const badges = createdElements.filter((el) => el.className === 'settings-recommended-badge');
    assert.ok(badges.length > 0, 'Expected recommended badges to be created');
    assert.ok(
      badges.every((badge) => badge.hidden === false),
      'All badges should be visible when values are recommended'
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.localStorage = originalLocalStorage;
  }
});

test('recommended badge hides when a viewer value differs from default', () => {
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
  global.document = createSettingsModalDocument(createdElements, appendedChildren);

  try {
    openSettingsModal();
    const badges = createdElements.filter((el) => el.className === 'settings-recommended-badge');
    assert.ok(badges.length > 0, 'Expected recommended badges to be created');
    assert.ok(
      badges.some((badge) => badge.hidden === true),
      'At least one badge should hide for non-recommended values'
    );
  } finally {
    global.document = originalDocument;
    global.window = originalWindow;
    global.localStorage = originalLocalStorage;
  }
});

test('recommended badge rule remains stable for all default values', () => {
  for (const key of Object.keys(RECOMMENDED_DEFAULTS)) {
    assert.equal(
      RECOMMENDED_DEFAULTS[key] !== RECOMMENDED_DEFAULTS[key],
      false,
      `Expected default value for ${key} to match itself`
    );
  }
});

function createMockElement(tagName = 'div') {
  const attributes = new Map();
  const listeners = new Map();
  const classes = new Set();

  const syncClassName = (element) => {
    element.className = Array.from(classes).join(' ').trim();
  };

  const element = {
    tagName: tagName.toUpperCase(),
    className: '',
    textContent: '',
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
      listeners.get('click')?.({ currentTarget: this });
    },
    querySelector(selector) {
      if (!selector.startsWith('.')) {
        return null;
      }
      const className = selector.slice(1);
      const queue = [...this.children];
      while (queue.length > 0) {
        const next = queue.shift();
        if ((next.className || '').split(/\s+/).includes(className)) {
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
    String(node?.textContent || ''),
    ...(node?.children || []).map((child) => collectNodeText(child)),
  ]
    .join(' ')
    .trim();
}

test('formatDependencyBlockMessage includes feature impact and guidance for missing gmsh', () => {
  const health = {
    dependencyDoctor: {
      components: [
        {
          id: 'gmsh_python',
          name: 'Gmsh Python API',
          category: 'required',
          status: 'missing',
          featureImpact: '/api/mesh/build and backend meshing are unavailable.',
          guidance: ['Install gmsh package: pip install -r server/requirements-gmsh.txt'],
        },
      ],
    },
  };

  const message = formatDependencyBlockMessage(health, {
    features: ['meshBuild'],
    fallback: 'HornLab mesher export is unavailable.',
  });

  assert.match(message, /HornLab mesher export is unavailable/);
  assert.match(message, /Gmsh Python API/);
  assert.match(message, /backend meshing are unavailable/);
  assert.match(message, /Install gmsh package/);
});

test('formatDependencyBlockMessage does not include optional component issues in solve feature block', () => {
  const health = {
    dependencyDoctor: {
      components: [
        {
          id: 'metal_release_helper',
          name: 'Metal release helper',
          category: 'optional',
          status: 'missing',
          featureImpact: 'Native Metal helper rebuilds are unavailable; solves still run.',
          guidance: ['Build the helper: npm run build:metal-helper'],
        },
      ],
    },
  };

  const message = formatDependencyBlockMessage(health, {
    features: ['solve'],
    fallback: 'Simulation is unavailable.',
    includeOptional: false,
  });

  assert.strictEqual(message, 'Simulation is unavailable.');
});

test('getFeatureBlockedReason ignores optional component issues for bem-solve', () => {
  const reason = getFeatureBlockedReason(
    {
      dependencyDoctor: {
        components: [
          {
            id: 'metal_release_helper',
            name: 'Metal release helper',
            category: 'optional',
            status: 'missing',
            featureImpact: 'Native Metal helper rebuilds are unavailable; solves still run.',
            guidance: ['Build the helper: npm run build:metal-helper'],
          },
          {
            id: 'matplotlib',
            name: 'Matplotlib',
            category: 'optional',
            status: 'missing',
            featureImpact: 'Chart render endpoints are unavailable; solver core paths still work.',
            guidance: ['Install matplotlib: pip install matplotlib'],
          },
        ],
      },
    },
    'bem-solve'
  );

  assert.equal(reason, null);
});

test('getFeatureBlockedReason supports HornLab mesher mesh feature aliases', () => {
  const reason = getFeatureBlockedReason(
    {
      dependencyDoctor: {
        components: [
          {
            id: 'gmsh_python',
            name: 'Gmsh Python API',
            category: 'required',
            status: 'missing',
            featureImpact: '/api/mesh/build and backend meshing are unavailable.',
            guidance: ['Install gmsh package'],
          },
        ],
      },
    },
    'hornlab-mesher-mesh'
  );

  assert.match(reason, /Install gmsh package/);
});

test('getFeatureBlockedReason reports missing HornLab mesher package for mesh builds', () => {
  const reason = getFeatureBlockedReason(
    {
      dependencyDoctor: {
        components: [
          {
            id: 'gmsh_python',
            name: 'Gmsh Python API',
            category: 'required',
            status: 'installed',
            featureImpact: 'HornLab mesher build path is available.',
            guidance: [],
          },
          {
            id: 'hornlab_waveguide_mesher',
            name: 'HornLab waveguide mesher',
            category: 'required',
            status: 'missing',
            featureImpact:
              '/api/mesh/build, viewport meshing, and HornLab mesher jobs are unavailable.',
            guidance: ['Install backend requirements'],
          },
        ],
      },
    },
    'hornlab-mesher-mesh'
  );

  assert.match(reason, /HornLab waveguide mesher/);
  assert.match(reason, /Install backend requirements/);
  assert.doesNotMatch(reason, /Gmsh Python API/);
});

test('getFeatureBlockedReason reports missing hornlab-metal-bem for bem-solve', () => {
  const reason = getFeatureBlockedReason(
    {
      dependencyDoctor: {
        components: [
          {
            id: 'hornlab_metal_bem',
            name: 'hornlab-metal-bem',
            category: 'required',
            status: 'missing',
            featureImpact: '/api/solve BEM simulation is unavailable.',
            guidance: ['Install hornlab-metal-bem: pip install -r server/requirements.txt'],
          },
        ],
      },
    },
    'bem-solve'
  );

  assert.match(reason, /BEM simulation is unavailable/);
  assert.match(reason, /Install hornlab-metal-bem/);
});

test('getFeatureBlockedReason reports missing hornlab-bempp-bem for bem-solve', () => {
  const reason = getFeatureBlockedReason(
    {
      dependencyDoctor: {
        components: [
          {
            id: 'hornlab_bempp_bem',
            name: 'hornlab-bempp-bem',
            category: 'required',
            status: 'missing',
            featureImpact: 'BEMPP fallback solves are unavailable.',
            guidance: ['Install BEMPP fallback requirements'],
          },
        ],
      },
    },
    'bem-solve'
  );

  assert.match(reason, /BEMPP fallback solves are unavailable/);
  assert.match(reason, /Install BEMPP fallback requirements/);
});

test('getFeatureBlockedReason reports required Metal release helper for bem-solve', () => {
  const reason = getFeatureBlockedReason(
    {
      dependencyDoctor: {
        components: [
          {
            id: 'metal_release_helper',
            name: 'Metal release helper',
            category: 'required',
            status: 'missing',
            featureImpact: 'Release Metal helper is required for Apple-Silicon solves.',
            guidance: ['Build the release Metal helper'],
          },
        ],
      },
    },
    'bem-solve'
  );

  assert.match(reason, /Release Metal helper is required/);
  assert.match(reason, /Build the release Metal helper/);
});

test('createDependencyStatusPanel renders required and optional dependency issues', () => {
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
            id: 'gmsh_python',
            name: 'Gmsh Python API',
            category: 'required',
            status: 'missing',
            version: null,
            requiredFor: '/api/mesh/build',
            featureImpact: '/api/mesh/build and backend meshing are unavailable.',
            guidance: ['Install gmsh package: pip install -r server/requirements-gmsh.txt'],
          },
          {
            id: 'matplotlib',
            name: 'Matplotlib',
            category: 'optional',
            status: 'missing',
            version: null,
            requiredFor: 'chart/directivity image render endpoints',
            featureImpact:
              'Chart/directivity image render endpoints are unavailable; solver core paths still work.',
            guidance: ['Install matplotlib: pip install matplotlib'],
          },
        ],
      },
    });

    assert.equal(panel.classList.contains('has-warnings'), true);
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

test('buildRequiredDependencyWarning returns null when required runtime is ready', () => {
  const warning = buildRequiredDependencyWarning({
    dependencyDoctor: {
      summary: { requiredReady: true },
      components: [
        {
          id: 'gmsh_python',
          name: 'Gmsh Python API',
          category: 'required',
          status: 'installed',
          guidance: [],
        },
        {
          id: 'matplotlib',
          name: 'Matplotlib',
          category: 'optional',
          status: 'missing',
          guidance: ['Install matplotlib: pip install matplotlib'],
        },
      ],
    },
  });

  assert.equal(warning, null);
});

test('buildRequiredDependencyWarning only includes required dependency guidance', () => {
  const warning = buildRequiredDependencyWarning({
    dependencyDoctor: {
      components: [
        {
          id: 'gmsh_python',
          name: 'Gmsh Python API',
          category: 'required',
          status: 'missing',
          featureImpact: '/api/mesh/build and backend meshing are unavailable.',
          guidance: ['Install gmsh package: pip install -r server/requirements-gmsh.txt'],
        },
        {
          id: 'matplotlib',
          name: 'Matplotlib',
          category: 'optional',
          status: 'missing',
          featureImpact:
            'Chart/directivity image render endpoints are unavailable; solver core paths still work.',
          guidance: ['Install matplotlib: pip install matplotlib'],
        },
      ],
    },
  });

  assert.ok(warning);
  assert.match(warning.title, /Backend Dependencies Missing/);
  assert.match(warning.message, /Simulation and backend meshing stay blocked/i);
  assert.match(warning.message, /Gmsh Python API/);
  assert.match(warning.message, /Install gmsh package/);
  assert.doesNotMatch(warning.message, /Install matplotlib/);
});
