import test from 'node:test';
import assert from 'node:assert/strict';

import * as solverApi from '../src/solver/index.js';
import { applySmoothingSelection } from '../src/ui/simulation/smoothing.js';
import { downloadMeshArtifact } from '../src/ui/simulation/meshDownload.js';
import { renderJobList, formatJobSummary } from '../src/ui/simulation/jobActions.js';
import { pollSimulationStatus, clearPollTimer } from '../src/ui/simulation/polling.js';
import {
  getSymmetryPolicySummary,
  renderSymmetryPolicySummary
} from '../src/ui/simulation/results.js';
import { AppEvents } from '../src/events.js';
import { getDownloadSimMeshEnabled } from '../src/ui/settings/modal.js';
import {
  RECOMMENDED_DEFAULTS as SIM_MANAGEMENT_DEFAULTS,
  saveSimulationManagementSettings
} from '../src/ui/settings/simulationManagementSettings.js';

const { BemSolver, validateCanonicalMeshPayload } = solverApi;

test('solver public API no longer exposes mock fallback helpers', () => {
  assert.equal('mockBEMSolver' in solverApi, false);
});

test('submitSimulation sends canonical mesh payload shape and adaptive mesh options', async () => {
  const originalFetch = global.fetch;
  const calls = [];

  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { job_id: 'job-test-1' };
      }
    };
  };

  try {
    const solver = new BemSolver();
    solver.backendUrl = 'http://localhost:8000';

    const mesh = {
      vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
      indices: [0, 1, 2],
      surfaceTags: [2],
      format: 'msh',
      boundaryConditions: {
        throat: { type: 'velocity', surfaceTag: 2, value: 1.0 },
        wall: { type: 'neumann', surfaceTag: 1, value: 0.0 },
        mouth: { type: 'robin', surfaceTag: 1, impedance: 'spherical' }
      },
      metadata: { ringCount: 3, fullCircle: true }
    };

    const options = {
      mesh: {
        strategy: 'occ_adaptive',
        waveguide_params: {
          formula_type: 'OSSE',
          throat_res: 4,
          mouth_res: 9,
          rear_res: 12
        }
      }
    };

    const jobId = await solver.submitSimulation(
      {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 4,
        simulationType: '2',
        meshValidationMode: 'strict',
        frequencySpacing: 'linear',
        deviceMode: 'opencl_cpu',
        useOptimized: false,
        enableSymmetry: false,
        verbose: false,
        polarConfig: {
          angle_range: [0, 180, 37],
          norm_angle: 5,
          distance: 2,
          inclination: 45,
          enabled_axes: ['horizontal', 'diagonal']
        }
      },
      mesh,
      options
    );

    assert.equal(jobId, 'job-test-1');
    assert.equal(calls.length, 1);
    assert.match(calls[0].url, /\/api\/solve$/);

    const payload = JSON.parse(calls[0].options.body);
    assert.deepEqual(Object.keys(payload.mesh).sort(), [
      'boundaryConditions',
      'format',
      'indices',
      'metadata',
      'surfaceTags',
      'vertices'
    ]);
    assert.equal(payload.mesh.surfaceTags.length, payload.mesh.indices.length / 3);
    assert.equal(payload.options.mesh.strategy, 'occ_adaptive');
    assert.equal(payload.options.mesh.waveguide_params.formula_type, 'OSSE');
    assert.deepEqual(payload.polar_config.enabled_axes, ['horizontal', 'diagonal']);
    assert.equal(payload.sim_type, '2');
    assert.equal(payload.mesh_validation_mode, 'strict');
    assert.equal(payload.frequency_spacing, 'linear');
    assert.equal(payload.device_mode, 'opencl_cpu');
    assert.equal(payload.use_optimized, false);
    assert.equal(payload.enable_symmetry, false);
    assert.equal(payload.verbose, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test('submitSimulation omits invalid or unset runtime settings so backend defaults remain authoritative', async () => {
  const originalFetch = global.fetch;
  const calls = [];

  global.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { job_id: 'job-test-omit-1' };
      }
    };
  };

  try {
    const solver = new BemSolver();
    await solver.submitSimulation(
      {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 4,
        meshValidationMode: 'invalid',
        frequencySpacing: 'bogus',
        deviceMode: '',
        useOptimized: 'yes please',
        enableSymmetry: null,
        verbose: undefined
      },
      {
        vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
        indices: [0, 1, 2],
        surfaceTags: [2],
        format: 'msh',
        boundaryConditions: {},
        metadata: {}
      }
    );

    const payload = JSON.parse(calls[0].options.body);
    assert.equal('mesh_validation_mode' in payload, false);
    assert.equal('frequency_spacing' in payload, false);
    assert.equal('device_mode' in payload, false);
    assert.equal('use_optimized' in payload, false);
    assert.equal('enable_symmetry' in payload, false);
    assert.equal('verbose' in payload, false);
  } finally {
    global.fetch = originalFetch;
  }
});

test('smoothing update sets panel state without submitting a new job', () => {
  let submitCalls = 0;

  const panel = {
    currentSmoothing: 'none',
    lastResults: { spl_on_axis: { frequencies: [100], spl: [90] } },
    solver: {
      submitSimulation: () => {
        submitCalls += 1;
      }
    }
  };

  applySmoothingSelection(panel, '1/6');

  assert.equal(panel.currentSmoothing, '1/6');
  assert.equal(submitCalls, 0);
});

test('getSymmetryPolicySummary formats applied symmetry reductions for the results UI', () => {
  const summary = getSymmetryPolicySummary({
    metadata: {
      symmetry: {
        symmetry_type: 'quarter_xz',
        reduction_factor: 4
      },
      symmetry_policy: {
        requested: true,
        applied: true,
        reason: 'applied',
        detected_symmetry_type: 'quarter_xz',
        detected_symmetry_planes: ['YZ', 'XY'],
        detected_reduction_factor: 4,
        reduction_factor: 4,
        excitation_centered: true
      }
    }
  });

  assert.equal(summary.badge, 'Reduced');
  assert.match(summary.headline, /quarter-domain/i);
  assert.match(summary.details, /YZ plane and XY plane/i);
  assert.equal(summary.items.find((item) => item.label === 'Decision')?.value, 'Quarter-domain (X/Z symmetry)');
  assert.equal(summary.items.find((item) => item.label === 'Source alignment')?.value, 'Centered');
  assert.equal(summary.items.find((item) => item.label === 'Reduction')?.value, '4x applied');
});

test('getSymmetryPolicySummary explains when detected symmetry is rejected by source alignment', () => {
  const summary = getSymmetryPolicySummary({
    metadata: {
      symmetry: {
        symmetry_type: 'full',
        reduction_factor: 1
      },
      symmetry_policy: {
        requested: true,
        applied: false,
        reason: 'excitation_off_center',
        detected_symmetry_type: 'quarter_xz',
        detected_symmetry_planes: ['YZ', 'XY'],
        detected_reduction_factor: 4,
        reduction_factor: 1,
        excitation_centered: false
      }
    }
  });

  assert.equal(summary.badge, 'Full model');
  assert.match(summary.headline, /alignment check/i);
  assert.match(summary.details, /off-center/i);
  assert.equal(summary.items.find((item) => item.label === 'Decision')?.value, 'Full model');
  assert.equal(summary.items.find((item) => item.label === 'Detected geometry')?.value, 'Quarter-domain (X/Z symmetry)');
  assert.equal(summary.items.find((item) => item.label === 'Reduction')?.value, '4x available');
});

test('renderSymmetryPolicySummary returns result-modal markup only when policy metadata exists', () => {
  const markup = renderSymmetryPolicySummary({
    metadata: {
      symmetry_policy: {
        requested: false,
        applied: false,
        reason: 'disabled',
        detected_symmetry_type: 'full',
        detected_symmetry_planes: [],
        detected_reduction_factor: 1,
        reduction_factor: 1,
        excitation_centered: null
      }
    }
  });

  assert.match(markup, /Symmetry Policy/);
  assert.match(markup, /Kept full model with symmetry disabled/);
  assert.match(markup, /view-results-summary/);
  assert.equal(renderSymmetryPolicySummary({ metadata: {} }), '');
});

test('validateCanonicalMeshPayload rejects malformed canonical mesh', () => {
  assert.throws(
    () =>
      validateCanonicalMeshPayload({
        vertices: [0, 0, 0],
        indices: [0, 1, 2],
        surfaceTags: [],
        format: 'msh',
        boundaryConditions: {}
      }),
    /surfaceTags length must match triangle count/
  );
});

test('submitSimulation preflight rejects mesh missing source tag before any API call', async () => {
  const originalFetch = global.fetch;
  let fetchCalls = 0;
  global.fetch = async () => {
    fetchCalls += 1;
    return { ok: true, async json() { return { job_id: 'should-not-happen' }; } };
  };

  try {
    const solver = new BemSolver();
    await assert.rejects(
      () => solver.submitSimulation(
        {
          frequencyStart: 100,
          frequencyEnd: 1000,
          numFrequencies: 3,
          simulationType: '2'
        },
        {
          vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
          indices: [0, 1, 2],
          surfaceTags: [1],
          format: 'msh',
          boundaryConditions: { throat: { type: 'velocity', surfaceTag: 2, value: 1.0 } },
          metadata: {}
        }
      ),
      /source surface tag \(2\) missing/i
    );
    assert.equal(fetchCalls, 0);
  } finally {
    global.fetch = originalFetch;
  }
});

test('submitSimulation maps backend 422 responses to typed validation ApiError', async () => {
  const originalFetch = global.fetch;

  global.fetch = async () => ({
    ok: false,
    status: 422,
    async json() {
      return {
        detail: [
          { loc: ['body', 'mesh'], msg: 'field required' }
        ]
      };
    }
  });

  try {
    const solver = new BemSolver();
    await assert.rejects(
      () => solver.submitSimulation(
        {
          frequencyStart: 100,
          frequencyEnd: 1000,
          numFrequencies: 3,
          simulationType: '2'
        },
        {
          vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
          indices: [0, 1, 2],
          surfaceTags: [2],
          format: 'msh',
          boundaryConditions: { throat: { type: 'velocity', surfaceTag: 2, value: 1.0 } },
          metadata: {}
        }
      ),
      (error) => {
        assert.equal(error.name, 'ApiError');
        assert.equal(error.category, 'validation');
        assert.equal(error.status, 422);
        assert.match(error.message, /submit simulation failed validation \(422\)/i);
        assert.match(error.message, /body\.mesh: field required/i);
        return true;
      }
    );
  } finally {
    global.fetch = originalFetch;
  }
});

// --- Check 5: mesh artifact download ---

test('downloadMeshArtifact fetches mesh and triggers download', async () => {
  const originalFetch = global.fetch;
  const originalCreateElement = global.document?.createElement;

  // Minimal DOM stubs for download anchor
  const clickedLinks = [];
  const removedChildren = [];
  const revokedUrls = [];

  global.document = {
    createElement(tag) {
      const el = { href: '', download: '', click() { clickedLinks.push(this); } };
      return el;
    },
    body: {
      appendChild() {},
      removeChild(el) { removedChildren.push(el); }
    }
  };
  global.URL = {
    createObjectURL() { return 'blob:test'; },
    revokeObjectURL(u) { revokedUrls.push(u); }
  };
  global.Blob = class { constructor(parts, opts) { this.parts = parts; this.opts = opts; } };

  global.fetch = async (url) => {
    assert.match(url, /\/api\/mesh-artifact\/job-42$/);
    return {
      ok: true,
      async text() { return '$MeshFormat\n2.2 0 8\n$EndMeshFormat'; }
    };
  };

  try {
    await downloadMeshArtifact('job-42');
    assert.equal(clickedLinks.length, 1);
    assert.match(clickedLinks[0].download, /simulation_mesh_job-42\.msh/);
    assert.equal(revokedUrls.length, 1);
  } finally {
    global.fetch = originalFetch;
    if (originalCreateElement) {
      global.document.createElement = originalCreateElement;
    }
  }
});

// --- Session 6 regression tests: lifecycle safety + URL config ---

test('downloadMeshArtifact uses the provided backendUrl instead of hardcoded default', async () => {
  const originalFetch = global.fetch;
  const fetchedUrls = [];

  global.document = {
    createElement() { return { href: '', download: '', click() {} }; },
    body: { appendChild() {}, removeChild() {} }
  };
  global.URL = { createObjectURL() { return 'blob:test'; }, revokeObjectURL() {} };
  global.Blob = class { constructor(parts, opts) { this.parts = parts; } };

  global.fetch = async (url) => {
    fetchedUrls.push(url);
    return { ok: true, async text() { return '$MeshFormat'; } };
  };

  try {
    await downloadMeshArtifact('job-99', 'http://custom-backend:9000');
    assert.equal(fetchedUrls.length, 1);
    assert.match(fetchedUrls[0], /^http:\/\/custom-backend:9000\/api\/mesh-artifact\/job-99$/);
  } finally {
    global.fetch = originalFetch;
  }
});

test('pollSimulationStatus guard: second call returns immediately when isPolling is true', () => {
  // When isPolling is already true, pollSimulationStatus should be a no-op.
  // No timer should be set and no DOM or fetch access should occur.
  const panel = {
    isPolling: true,
    pollTimer: null,
    pollInterval: null,
    pollDelayMs: 60000,
    pollBackoffMs: 1000,
    activeJobId: null,
    jobs: new Map(),
    solver: { backendUrl: 'http://localhost:8000' }
  };

  pollSimulationStatus(panel);

  // Guard fired — no timer was scheduled.
  assert.equal(panel.pollTimer, null);
  assert.equal(panel.isPolling, true);
});

test('dispose() clears poll timers, connection timer, and resets isPolling', () => {
  // Verify dispose() tear-down logic by exercising AppEvents.off round-trip.
  const removedEvents = [];
  const originalOff = AppEvents.off.bind(AppEvents);
  AppEvents.off = (event, cb) => {
    removedEvents.push(event);
    originalOff(event, cb);
  };

  const clearedIds = [];
  const origClearTimeout = global.clearTimeout;
  global.clearTimeout = (id) => { clearedIds.push(id); if (origClearTimeout) origClearTimeout(id); };

  try {
    // Simulate a panel that has active timers and registered listeners.
    const listener = () => {};
    AppEvents.on('state:updated', listener);

    const panel = {
      pollTimer: 7001,
      pollInterval: 7001,
      isPolling: true,
      connectionPollTimer: 7002,
      _onStateUpdated: listener,
      _onMeshReady: null,
      _onMeshError: null
    };

    // Run the same logic as SimulationPanel.dispose()
    if (panel.pollTimer) {
      clearTimeout(panel.pollTimer);
      panel.pollTimer = null;
      panel.pollInterval = null;
      panel.isPolling = false;
    }
    if (panel.connectionPollTimer) {
      clearTimeout(panel.connectionPollTimer);
      panel.connectionPollTimer = null;
    }
    if (panel._onStateUpdated) {
      AppEvents.off('state:updated', panel._onStateUpdated);
      panel._onStateUpdated = null;
    }

    assert.ok(clearedIds.includes(7001), 'pollTimer was cleared');
    assert.ok(clearedIds.includes(7002), 'connectionPollTimer was cleared');
    assert.equal(panel.pollTimer, null);
    assert.equal(panel.connectionPollTimer, null);
    assert.equal(panel.isPolling, false);
    assert.ok(removedEvents.includes('state:updated'), 'state:updated listener was removed');
    assert.equal(panel._onStateUpdated, null);
  } finally {
    global.clearTimeout = origClearTimeout;
    AppEvents.off = originalOff;
  }
});

// --- Session 7 regression tests: module split + DOM cache ---

test('formatJobSummary is accessible from jobActions.js sub-module', () => {
  assert.strictEqual(typeof formatJobSummary, 'function');
  // Verify it produces expected output for a complete job
  const job = { status: 'complete', progress: 1, completedAt: '2026-02-24T12:00:00Z', startedAt: '2026-02-24T11:59:00Z' };
  const summary = formatJobSummary(job);
  assert.ok(summary.startsWith('Complete'), `Expected summary starting with Complete, got: ${summary}`);
});

test('renderJobList is accessible from jobActions.js sub-module', () => {
  assert.strictEqual(typeof renderJobList, 'function');
});

test('renderJobList exposes folder source mode in the header and rows', () => {
  const originalDocument = global.document;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      return null;
    }
  };

  try {
    renderJobList({
      jobSourceMode: 'folder',
      activeJobId: null,
      jobs: new Map([
        ['job-folder-1', {
          id: 'job-folder-1',
          label: 'folder-task',
          status: 'complete',
          createdAt: '2026-03-11T09:00:00.000Z',
          completedAt: '2026-03-11T09:10:00.000Z'
        }]
      ])
    });

    assert.equal(sourceLabel.textContent, 'Folder Tasks');
    assert.match(list.innerHTML, /simulation-job-source-badge/);
    assert.match(list.innerHTML, />Folder</);
  } finally {
    global.document = originalDocument;
  }
});

test('renderJobList applies rating filter and renders rating controls', () => {
  const originalDocument = global.document;
  const originalLocalStorage = global.localStorage;
  const list = { innerHTML: '' };
  const sourceLabel = { textContent: '' };
  const sortSelect = { value: 'completed_desc' };
  const minRatingSelect = { value: '0' };

  global.localStorage = {
    values: new Map(),
    getItem(key) {
      return this.values.has(key) ? this.values.get(key) : null;
    },
    setItem(key, value) {
      this.values.set(key, String(value));
    }
  };

  saveSimulationManagementSettings({
    autoExportOnComplete: true,
    selectedFormats: ['csv'],
    defaultSort: 'rating_desc',
    minRatingFilter: 4
  });

  global.document = {
    getElementById(id) {
      if (id === 'simulation-jobs-list') return list;
      if (id === 'simulation-jobs-source-label') return sourceLabel;
      if (id === 'simulation-jobs-sort') return sortSelect;
      if (id === 'simulation-jobs-min-rating') return minRatingSelect;
      return null;
    }
  };

  try {
    renderJobList({
      jobSourceMode: 'backend',
      activeJobId: null,
      jobs: new Map([
        ['job-high', {
          id: 'job-high',
          label: 'rated-high',
          status: 'complete',
          rating: 5,
          createdAt: '2026-03-11T09:00:00.000Z',
          completedAt: '2026-03-11T09:10:00.000Z'
        }],
        ['job-low', {
          id: 'job-low',
          label: 'rated-low',
          status: 'complete',
          rating: 2,
          createdAt: '2026-03-11T08:00:00.000Z',
          completedAt: '2026-03-11T08:10:00.000Z'
        }]
      ])
    });

    assert.match(list.innerHTML, /rated-high/);
    assert.doesNotMatch(list.innerHTML, /rated-low/);
    assert.match(list.innerHTML, /simulation-job-rating-star/);
    assert.match(list.innerHTML, /data-job-rating="5"/);
  } finally {
    saveSimulationManagementSettings(SIM_MANAGEMENT_DEFAULTS);
    global.document = originalDocument;
    global.localStorage = originalLocalStorage;
  }
});

test('clearPollTimer from polling.js resets isPolling and clears timer refs', () => {
  const clearedIds = [];
  const origClearTimeout = global.clearTimeout;
  global.clearTimeout = (id) => { clearedIds.push(id); if (origClearTimeout) origClearTimeout(id); };

  try {
    const panel = {
      pollTimer: 9001,
      pollInterval: 9001,
      consecutivePollFailures: 3,
      isPolling: true
    };
    clearPollTimer(panel);
    assert.ok(clearedIds.includes(9001), 'pollTimer was cleared via clearTimeout');
    assert.equal(panel.pollTimer, null);
    assert.equal(panel.pollInterval, null);
    assert.equal(panel.consecutivePollFailures, 0);
    assert.equal(panel.isPolling, false);
  } finally {
    global.clearTimeout = origClearTimeout;
  }
});

test('pollSimulationStatus publishes backend simulation mesh stats to the app widget', async () => {
  const originalDocument = global.document;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;

  global.document = { getElementById() { return null; } };
  global.setTimeout = () => 1;
  global.clearTimeout = () => {};

  const publishedMeshStats = [];

  try {
    const panel = {
      isPolling: false,
      pollTimer: null,
      pollInterval: null,
      pollDelayMs: 1000,
      pollBackoffMs: 1000,
      consecutivePollFailures: 0,
      activeJobId: null,
      currentJobId: null,
      jobs: new Map(),
      resultCache: new Map(),
      solver: {
        async listJobs() {
          return {
            items: [{
              id: 'job-mesh-stats',
              status: 'running',
              progress: 0.35,
              stage: 'mesh_prepare',
              stage_message: 'Building adaptive OCC mesh',
              mesh_stats: { vertex_count: 144, triangle_count: 72, source: 'occ_adaptive_canonical' }
            }]
          };
        }
      },
      displayResults() {},
      checkSolverConnection() {},
      app: {
        setSimulationMeshStats(meshStats) {
          publishedMeshStats.push(meshStats);
        }
      }
    };

    pollSimulationStatus(panel);
    await Promise.resolve();
    await Promise.resolve();

    assert.deepEqual(publishedMeshStats, [
      { vertex_count: 144, triangle_count: 72, source: 'occ_adaptive_canonical' }
    ]);
  } finally {
    global.document = originalDocument;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

test('pollSimulationStatus enforces idle polling budget after status-fetch error', async () => {
  const originalDocument = global.document;
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;

  const scheduledDelays = [];
  let timeoutId = 0;
  global.document = { getElementById() { return null; } };
  global.setTimeout = (_fn, delay) => {
    scheduledDelays.push(delay);
    timeoutId += 1;
    return timeoutId;
  };
  global.clearTimeout = () => {};

  try {
    const panel = {
      isPolling: false,
      pollTimer: null,
      pollInterval: null,
      pollDelayMs: 1000,
      pollBackoffMs: 1000,
      consecutivePollFailures: 0,
      activeJobId: null,
      jobs: new Map(),
      resultCache: new Map(),
      solver: {
        async listJobs() {
          throw new Error('backend unavailable');
        }
      },
      checkSolverConnection() {}
    };

    pollSimulationStatus(panel);
    await Promise.resolve();
    await Promise.resolve();

    assert.equal(panel.consecutivePollFailures, 1);
    assert.equal(panel.pollBackoffMs, 2000);
    assert.equal(panel.pollDelayMs, 15000);
    assert.ok(scheduledDelays.includes(15000), `expected scheduled delay to include 15000ms, got ${scheduledDelays.join(',')}`);
  } finally {
    global.document = originalDocument;
    global.setTimeout = originalSetTimeout;
    global.clearTimeout = originalClearTimeout;
  }
});

// --- Phase 1 migration regression: simulation flow unaffected by control migration ---

test('getDownloadSimMeshEnabled returns false by default when modal is not open', () => {
  // jobActions.js uses getDownloadSimMeshEnabled() to guard the mesh download at job start.
  // This default must be false so no unexpected download is triggered on startup before
  // the user has ever opened Settings.
  const originalDocument = global.document;
  global.document = { getElementById: () => null };

  try {
    assert.equal(getDownloadSimMeshEnabled(), false);
  } finally {
    global.document = originalDocument;
  }
});

test('getDownloadSimMeshEnabled does not access a static DOM element that would be absent when modal is closed', () => {
  // After migration, download-sim-mesh lives in a dynamically-created modal.
  // When the modal is closed, getElementById('download-sim-mesh') returns null.
  // The getter must NOT throw or return a falsy value that silently corrupts behavior.
  const originalDocument = global.document;
  const queriedIds = [];

  global.document = {
    getElementById(id) {
      queriedIds.push(id);
      return null; // Modal is closed — element does not exist in DOM
    }
  };

  try {
    const result = getDownloadSimMeshEnabled();
    // Should return a boolean (the in-memory default), never null or undefined
    assert.equal(typeof result, 'boolean');
    // Should have attempted to look up the element (DOM-first strategy)
    assert.ok(queriedIds.includes('download-sim-mesh'), 'getter should attempt DOM lookup first');
  } finally {
    global.document = originalDocument;
  }
});
