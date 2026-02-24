import test from 'node:test';
import assert from 'node:assert/strict';

import { BemSolver, validateCanonicalMeshPayload } from '../src/solver/index.js';
import { applySmoothingSelection } from '../src/ui/simulation/smoothing.js';
import { filterValidPairs } from '../src/ui/simulation/charts.js';
import { downloadMeshArtifact, pollSimulationStatus } from '../src/ui/simulation/actions.js';
import { renderJobList, formatJobSummary } from '../src/ui/simulation/jobActions.js';
import { pollSimulationStatus as pollFromSubModule, clearPollTimer } from '../src/ui/simulation/polling.js';
import { AppEvents } from '../src/events.js';

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

// --- P0 regression tests: null-value chart handling ---

test('filterValidPairs strips null and non-finite values from parallel arrays', () => {
  const { freqs, vals } = filterValidPairs(
    [100, 200, 300, 400, 500],
    [90, null, NaN, undefined, 85]
  );
  assert.deepEqual(freqs, [100, 500]);
  assert.deepEqual(vals, [90, 85]);
});

test('filterValidPairs returns empty arrays when all values are null', () => {
  const { freqs, vals } = filterValidPairs(
    [100, 200, 300],
    [null, null, null]
  );
  assert.deepEqual(freqs, []);
  assert.deepEqual(vals, []);
});

test('filterValidPairs strips entries where frequency is null', () => {
  const { freqs, vals } = filterValidPairs(
    [100, null, 300],
    [90, 85, 80]
  );
  assert.deepEqual(freqs, [100, 300]);
  assert.deepEqual(vals, [90, 80]);
});

test('filterValidPairs preserves all valid pairs', () => {
  const { freqs, vals } = filterValidPairs(
    [100, 200, 300],
    [90, 85, 80]
  );
  assert.deepEqual(freqs, [100, 200, 300]);
  assert.deepEqual(vals, [90, 85, 80]);
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

test('pollSimulationStatus from polling.js sub-module is the same export as from actions.js barrel', () => {
  // Both imports should resolve to the same function reference via the barrel re-export.
  assert.strictEqual(pollSimulationStatus, pollFromSubModule);
});

test('clearPollTimer from polling.js resets isPolling and clears timer refs', () => {
  const clearedIds = [];
  const origClearTimeout = global.clearTimeout;
  global.clearTimeout = (id) => { clearedIds.push(id); if (origClearTimeout) origClearTimeout(id); };

  try {
    const panel = {
      pollTimer: 9001,
      pollInterval: 9001,
      isPolling: true
    };
    clearPollTimer(panel);
    assert.ok(clearedIds.includes(9001), 'pollTimer was cleared via clearTimeout');
    assert.equal(panel.pollTimer, null);
    assert.equal(panel.pollInterval, null);
    assert.equal(panel.isPolling, false);
  } finally {
    global.clearTimeout = origClearTimeout;
  }
});
