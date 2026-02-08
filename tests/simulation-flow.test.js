import test from 'node:test';
import assert from 'node:assert/strict';

import { BemSolver, validateCanonicalMeshPayload } from '../src/solver/index.js';
import { applySmoothingSelection } from '../src/ui/simulation/smoothing.js';

test('submitSimulation sends canonical mesh payload shape', async () => {
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

    const jobId = await solver.submitSimulation(
      {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 4,
        simulationType: '2'
      },
      mesh
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
