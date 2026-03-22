import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  prepareGeometryParams,
  buildCanonicalMeshPayload,
  buildPreparedCanonicalMeshPayload
} from '../src/geometry/index.js';
import { SimulationModule } from '../src/modules/simulation/index.js';
import { DesignModule } from '../src/modules/design/index.js';
import { GlobalState } from '../src/state.js';
import {
  validateSimulationConfig,
  prepareCanonicalSimulationMesh,
  prepareOccAdaptiveSolveRequest,
  summarizeCanonicalSimulationMesh,
  summarizePersistedSimulationMeshStats
} from '../src/modules/simulation/domain.js';
import {
  readSimulationState,
  updateSimulationStateParams,
  loadSimulationStateSnapshot,
  applySimulationJobScriptState
} from '../src/modules/simulation/state.js';
import {
  buildQueuedSimulationJob,
  buildCancellationRequestedSimulationJob,
  buildCancelledSimulationJob,
  resolveClearedFailedJobIds
} from '../src/modules/simulation/jobs.js';
import {
  readSimulationWorkspaceJobs,
  syncSimulationWorkspaceIndex,
  syncSimulationWorkspaceJobManifest
} from '../src/ui/simulation/workspaceTasks.js';
function makeRawParams(overrides = {}) {
  return {
    ...getDefaults('OSSE'),
    type: 'OSSE',
    L: '120',
    a: '45',
    a0: '15.5',
    r0: '12.7',
    angularSegments: 24,
    lengthSegments: 10,
    ...overrides
  };
}

test('SimulationModule task matches canonical mesh payload contract', () => {
  const rawParams = makeRawParams({ encDepth: 180, quadrants: '1' });
  const simulationInput = SimulationModule.import(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const simulationTask = SimulationModule.task(simulationInput, {
    includeEnclosure: true,
    adaptivePhi: false
  });

  const expectedPrepared = prepareGeometryParams(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const expected = buildCanonicalMeshPayload(expectedPrepared, {
    includeEnclosure: true,
    adaptivePhi: false
  });

  assert.equal(simulationInput.module, 'simulation');
  assert.equal(simulationInput.stage, 'import');
  assert.equal(simulationTask.stage, 'task');
  assert.deepEqual(SimulationModule.output.mesh(simulationTask), expected);
});

test('SimulationModule occ adaptive output builds solver submit options', () => {
  const preparedParams = prepareGeometryParams(
    makeRawParams({ encDepth: 220, wallThickness: 6 }),
    {
      type: 'OSSE',
      applyVerticalOffset: true
    }
  );
  const simulationInput = SimulationModule.importPrepared(preparedParams);
  const adaptive = SimulationModule.output.occAdaptive(simulationInput, {
    mshVersion: '2.2',
    simType: 2
  });

  assert.equal(adaptive.waveguidePayload.formula_type, 'OSSE');
  assert.equal(adaptive.waveguidePayload.sim_type, 2);
  assert.equal(adaptive.submitOptions.mesh.strategy, 'occ_adaptive');
  assert.equal(
    adaptive.submitOptions.mesh.waveguide_params,
    adaptive.waveguidePayload
  );
});

test('SimulationModule.importDesign consumes DesignModule task output directly', () => {
  const rawParams = makeRawParams({ encDepth: 120, wallThickness: 4 });
  const designTask = DesignModule.task(
    DesignModule.import(rawParams, {
      type: 'OSSE',
      applyVerticalOffset: true
    })
  );
  const simulationInput = SimulationModule.importDesign(designTask);
  const expectedPrepared = prepareGeometryParams(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });

  assert.equal(
    JSON.stringify(simulationInput.params),
    JSON.stringify(expectedPrepared)
  );
});

test('simulation use case validates frequency configuration', () => {
  assert.match(
    validateSimulationConfig({
      frequencyStart: 1000,
      frequencyEnd: 100,
      numFrequencies: 10
    }),
    /Start frequency/
  );

  assert.equal(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 10
    }),
    null
  );
});

test('simulation domain prepares canonical mesh from an explicit state snapshot', () => {
  const state = {
    type: 'OSSE',
    params: makeRawParams({ encDepth: 180, quadrants: '1' })
  };
  const payload = prepareCanonicalSimulationMesh(state);
  const expectedPrepared = prepareGeometryParams(state.params, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const expected = buildCanonicalMeshPayload(expectedPrepared, {
    includeEnclosure: true,
    adaptivePhi: false
  });

  assert.deepEqual(payload, expected);
});

test('simulation domain applies scale exactly once when building canonical mesh from raw state', () => {
  const state = {
    type: 'OSSE',
    params: makeRawParams({
      scale: 0.5,
      L: '100',
      r0: '10',
      encDepth: 0,
      wallThickness: 0
    })
  };

  const payload = prepareCanonicalSimulationMesh(state);
  const expectedPrepared = prepareGeometryParams(state.params, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const expected = buildPreparedCanonicalMeshPayload(expectedPrepared, {
    includeEnclosure: false,
    adaptivePhi: false
  });

  assert.equal(expectedPrepared.L, 50);
  assert.deepEqual(payload, expected);
});

test('simulation domain prepares OCC adaptive solve requests from an explicit state snapshot', () => {
  const state = {
    type: 'OSSE',
    params: makeRawParams({ encDepth: 220, wallThickness: 6 })
  };
  const request = prepareOccAdaptiveSolveRequest(state, {
    mshVersion: '2.2',
    simType: 2
  });

  assert.equal(request.waveguidePayload.formula_type, 'OSSE');
  assert.equal(request.waveguidePayload.sim_type, 2);
  assert.equal(request.submitOptions.mesh.strategy, 'occ_adaptive');
  assert.deepEqual(request.stateSnapshot, state);
  assert.notEqual(request.stateSnapshot, state);
});

test('simulation state facade reads and updates GlobalState through module boundary', () => {
  const originalGet = GlobalState.get;
  const originalUpdate = GlobalState.update;
  const originalLoadState = GlobalState.loadState;

  let currentState = {
    type: 'OSSE',
    params: { freqStart: 120, a: 30, _blocks: { horizontal: {} } }
  };
  const loadCalls = [];

  GlobalState.get = () => currentState;
  GlobalState.update = (nextParams) => {
    currentState = {
      ...currentState,
      params: {
        ...currentState.params,
        ...nextParams
      }
    };
  };
  GlobalState.loadState = (nextState, source) => {
    loadCalls.push({ nextState, source });
    currentState = nextState;
  };

  try {
    assert.equal(readSimulationState().params.freqStart, 120);

    updateSimulationStateParams({ freqStart: 240 });
    assert.equal(readSimulationState().params.freqStart, 240);

    const loaded = loadSimulationStateSnapshot(
      { type: 'OSSE', params: { freqStart: 360, _blocks: { diagonal: {} } } },
      'test-snapshot-load'
    );
    assert.equal(loaded.params.freqStart, 360);
    assert.equal(loadCalls.length, 1);
    assert.equal(loadCalls[0].source, 'test-snapshot-load');
  } finally {
    GlobalState.get = originalGet;
    GlobalState.update = originalUpdate;
    GlobalState.loadState = originalLoadState;
  }
});

test('simulation job script state application prefers snapshots and falls back to params', () => {
  const originalGet = GlobalState.get;
  const originalUpdate = GlobalState.update;
  const originalLoadState = GlobalState.loadState;

  let currentState = { type: 'OSSE', params: { L: 120 } };
  let updateCalls = 0;
  let loadCalls = 0;

  GlobalState.get = () => currentState;
  GlobalState.update = (nextParams) => {
    updateCalls += 1;
    currentState = {
      ...currentState,
      params: {
        ...currentState.params,
        ...nextParams
      }
    };
  };
  GlobalState.loadState = (nextState) => {
    loadCalls += 1;
    currentState = nextState;
  };

  try {
    const snapshotResult = applySimulationJobScriptState({
      stateSnapshot: { type: 'OSSE', params: { L: 200, _blocks: { horizontal: {} } } },
      params: { L: 300 }
    });
    assert.equal(snapshotResult.mode, 'snapshot');
    assert.equal(snapshotResult.params.L, 200);
    assert.equal(loadCalls, 1);
    assert.equal(updateCalls, 0);

    const paramsResult = applySimulationJobScriptState({ params: { a: 42 } });
    assert.equal(paramsResult.mode, 'params');
    assert.equal(paramsResult.params.a, 42);
    assert.equal(updateCalls, 1);

    const noneResult = applySimulationJobScriptState({});
    assert.equal(noneResult.mode, 'none');
    assert.equal(noneResult.params, null);
  } finally {
    GlobalState.get = originalGet;
    GlobalState.update = originalUpdate;
    GlobalState.loadState = originalLoadState;
  }
});

test('simulation use case builds queued job metadata and script snapshot', () => {
  const job = buildQueuedSimulationJob({
    jobId: 'job-abc',
    startedIso: '2026-03-11T10:00:00.000Z',
    outputName: 'simulation',
    counter: 7,
    config: {
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 5,
      meshValidationMode: 'strict',
      frequencySpacing: 'log',
      verbose: true,
      advancedSettings: {
        useBurtonMiller: false
      },
      polarConfig: {
        angle_range: [0, 90, 15],
        norm_angle: 0,
        distance: 2,
        inclination: 45,
        enabled_axes: ['horizontal']
      }
    },
    waveguidePayload: { formula_type: 'OSSE' },
    preparedParams: { L: 120, a: 45 },
    stateSnapshot: { type: 'OSSE', params: { L: 120 } }
  });

  assert.equal(job.id, 'job-abc');
  assert.equal(job.status, 'queued');
  assert.equal(job.label, 'simulation_7');
  assert.equal(job.configSummary.formula_type, 'OSSE');
  assert.deepEqual(job.configSummary.frequency_range, [100, 1000]);
  assert.equal('enable_symmetry' in job.configSummary, false);
  assert.deepEqual(job.script.params, { L: 120, a: 45 });
  assert.equal(job.script.meshValidationMode, 'strict');
  assert.equal(job.script.frequencySpacing, 'log');
  assert.equal('enableSymmetry' in job.script, false);
  assert.equal(job.script.verbose, true);
  assert.deepEqual(job.script.advancedSettings, {
    useBurtonMiller: false
  });
});

test('simulation use case summarizes canonical tag counts and warnings', () => {
  const summary = summarizeCanonicalSimulationMesh({
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices: [0, 1, 2, 0, 2, 1],
    surfaceTags: [1, 4],
    groups: {
      throat_disc: { start: 0, end: 1 },
      inner_wall: { start: 1, end: 2 }
    }
  });

  assert.equal(summary.vertexCount, 3);
  assert.equal(summary.triangleCount, 2);
  assert.deepEqual(summary.tagCounts, { 1: 1, 2: 0, 3: 0, 4: 1 });
  assert.equal(summary.identityTriangleCounts.throat_disc, 1);
  assert.equal(summary.identityTriangleCounts.inner_wall, 1);
  assert.equal(summary.identityTriangleCounts.enc_side, 0);
  assert.equal(summary.ok, false);
  assert.match(summary.warnings[0], /source surface tag/i);
});

test('simulation use case flags tag-count mismatches in canonical summaries', () => {
  const summary = summarizeCanonicalSimulationMesh({
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices: [0, 1, 2, 0, 2, 1],
    surfaceTags: [2]
  });

  assert.equal(summary.ok, false);
  assert.match(summary.warnings[0], /surface tag count/i);
});

test('simulation use case normalizes persisted backend mesh diagnostics', () => {
  const summary = summarizePersistedSimulationMeshStats({
    vertex_count: 12,
    triangle_count: 4,
    tag_counts: { 1: 3, 2: 1, 4: 0 },
    identity_triangle_counts: {
      inner_wall: 2,
      throat_disc: 1,
      rear_cap: 1
    }
  });

  assert.equal(summary.vertexCount, 12);
  assert.equal(summary.triangleCount, 4);
  assert.deepEqual(summary.tagCounts, { 1: 3, 2: 1, 3: 0, 4: 0 });
  assert.equal(summary.identityTriangleCounts.inner_wall, 2);
  assert.equal(summary.identityTriangleCounts.throat_disc, 1);
  assert.equal(summary.identityTriangleCounts.enc_side, 0);
  assert.equal(summary.provenance, 'backend');
  assert.equal(summary.ok, true);
});

test('simulation workspace service writes job manifest via backend', async () => {
  const originalFetch = global.fetch;
  const fetchCalls = [];

  global.fetch = async (url, options = {}) => {
    fetchCalls.push({ url, options });
    return {
      ok: true,
      async json() {
        return { status: 'success' };
      }
    };
  };

  try {
    const manifest = await syncSimulationWorkspaceJobManifest({
      id: 'job-folder-1',
      label: 'horn_1',
      status: 'queued',
      createdAt: '2026-03-11T12:00:00.000Z'
    });

    assert.equal(manifest.id, 'job-folder-1');

    // Manifest write goes through backend via writeWorkspaceFile
    assert.ok(fetchCalls.length >= 1);
    assert.equal(fetchCalls[0].url, 'http://localhost:8000/api/export-file');

    // readSimulationWorkspaceJobs always returns empty in backend-only mode
    const restored = await readSimulationWorkspaceJobs();
    assert.equal(restored.available, false);
    assert.equal(restored.items.length, 0);
  } finally {
    global.fetch = originalFetch;
  }
});

test('simulation workspace service index sync is unavailable in backend-only mode', async () => {
  const result = await syncSimulationWorkspaceIndex([
    {
      id: 'job-folder-2',
      status: 'complete',
      exportedFiles: ['result.csv'],
      scriptSnapshot: { outputName: 'horn' }
    }
  ]);

  // syncSimulationWorkspaceIndex always returns not-synced in backend-only mode
  assert.equal(result.synced, false);
  assert.equal(result.available, false);
  assert.deepEqual(result.items, []);

  // readSimulationWorkspaceJobs always returns empty
  const restored = await readSimulationWorkspaceJobs();
  assert.equal(restored.available, false);
  assert.equal(restored.items.length, 0);
});

test('simulation use case builds cancelled job state and resolves failed cleanup IDs', () => {
  const pending = buildCancellationRequestedSimulationJob(
    { id: 'job-1', status: 'running', stage: 'bem_solve' },
    { message: 'Cancellation requested by user' }
  );
  assert.equal(pending.status, 'running');
  assert.equal(pending.stage, 'cancelling');
  assert.equal(pending.stageMessage, 'Cancellation requested by user');
  assert.equal(pending.cancellationRequested, true);

  const cancelled = buildCancelledSimulationJob(
    { id: 'job-1', status: 'running', stage: 'bem_solve' },
    { completedAt: '2026-03-11T10:01:00.000Z' }
  );
  assert.equal(cancelled.status, 'cancelled');
  assert.equal(cancelled.stageMessage, 'Simulation cancelled by user');
  assert.equal(cancelled.completedAt, '2026-03-11T10:01:00.000Z');
  assert.equal(cancelled.cancellationRequested, false);
  assert.equal(buildCancellationRequestedSimulationJob(null), null);
  assert.equal(buildCancelledSimulationJob(null), null);

  assert.deepEqual(
    resolveClearedFailedJobIds(['a', 'b'], { deleted_ids: ['b'] }),
    ['b']
  );
  assert.deepEqual(
    resolveClearedFailedJobIds(['a', 'b'], { deleted_count: 2 }),
    ['a', 'b']
  );
  assert.deepEqual(
    resolveClearedFailedJobIds(['a', 'b'], { deleted_count: 0 }),
    []
  );
});
