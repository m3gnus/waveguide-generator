import test from 'node:test';
import assert from 'node:assert/strict';

import { BemSolver, validateCanonicalMeshPayload } from '../src/solver/index.js';
import { applySmoothingSelection } from '../src/ui/simulation/smoothing.js';
import { filterValidPairs } from '../src/ui/simulation/charts.js';
import { downloadMeshArtifact } from '../src/ui/simulation/actions.js';

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

test('smoothing update re-renders existing results without submitting a new job', () => {
  let renderCalls = 0;
  let submitCalls = 0;

  const panel = {
    currentSmoothing: 'none',
    lastResults: { spl_on_axis: { frequencies: [100], spl: [90] } },
    displayResults: () => {
      renderCalls += 1;
    },
    solver: {
      submitSimulation: () => {
        submitCalls += 1;
      }
    }
  };

  applySmoothingSelection(panel, '1/6');

  assert.equal(panel.currentSmoothing, '1/6');
  assert.equal(renderCalls, 1);
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
