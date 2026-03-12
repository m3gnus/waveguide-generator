import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildCanonicalMeshPayload } from '../src/geometry/index.js';
import { SimulationModule } from '../src/modules/simulation/index.js';
import { DesignModule } from '../src/modules/design/index.js';
import { GlobalState } from '../src/state.js';
import {
  validateSimulationConfig,
  prepareCanonicalSimulationMesh,
  prepareOccAdaptiveSolveRequest,
  summarizeCanonicalSimulationMesh
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
} from '../src/modules/simulation/workspaceTasks.js';
import {
  resetSelectedFolder,
  setSelectedFolderHandle
} from '../src/ui/workspace/folderWorkspace.js';

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

function createMemoryDirectory(name = 'root') {
  const files = new Map();
  const directories = new Map();

  return {
    kind: 'directory',
    name,
    async getDirectoryHandle(dirName, options = {}) {
      if (!directories.has(dirName)) {
        if (!options.create) {
          const error = new Error('not found');
          error.name = 'NotFoundError';
          throw error;
        }
        directories.set(dirName, createMemoryDirectory(dirName));
      }
      return directories.get(dirName);
    },
    async getFileHandle(fileName, options = {}) {
      if (!files.has(fileName)) {
        if (!options.create) {
          const error = new Error('not found');
          error.name = 'NotFoundError';
          throw error;
        }
        files.set(fileName, '');
      }
      return {
        async getFile() {
          const textValue = files.get(fileName) ?? '';
          return { async text() { return textValue; } };
        },
        async createWritable() {
          return {
            async write(content) {
              files.set(fileName, String(content));
            },
            async close() {}
          };
        }
      };
    },
    files,
    directories,
    async *entries() {
      for (const [dirName, dirHandle] of directories.entries()) {
        yield [dirName, dirHandle];
      }
      for (const [fileName] of files.entries()) {
        yield [fileName, { kind: 'file', name: fileName }];
      }
    }
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
      deviceMode: 'auto',
      useOptimized: false,
      enableSymmetry: false,
      verbose: true,
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
  assert.deepEqual(job.script.params, { L: 120, a: 45 });
  assert.equal(job.script.meshValidationMode, 'strict');
  assert.equal(job.script.frequencySpacing, 'log');
  assert.equal(job.script.deviceMode, 'auto');
  assert.equal(job.script.useOptimized, false);
  assert.equal(job.script.enableSymmetry, false);
  assert.equal(job.script.verbose, true);
});

test('simulation use case summarizes canonical tag counts and warnings', () => {
  const summary = summarizeCanonicalSimulationMesh({
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices: [0, 1, 2, 0, 2, 1],
    surfaceTags: [1, 4]
  });

  assert.equal(summary.vertexCount, 3);
  assert.equal(summary.triangleCount, 2);
  assert.deepEqual(summary.tagCounts, { 1: 1, 2: 0, 3: 0, 4: 1 });
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

test('simulation workspace service rebuilds folder index from task manifests', async () => {
  const root = createMemoryDirectory();
  setSelectedFolderHandle(root, { label: 'workspace' });

  try {
    const manifest = await syncSimulationWorkspaceJobManifest({
      id: 'job-folder-1',
      label: 'horn_1',
      status: 'queued',
      createdAt: '2026-03-11T12:00:00.000Z'
    });

    assert.equal(manifest.id, 'job-folder-1');

    const restored = await readSimulationWorkspaceJobs();
    assert.equal(restored.available, true);
    assert.equal(restored.repaired, true);
    assert.equal(restored.items.length, 1);
    assert.equal(restored.items[0].id, 'job-folder-1');
    assert.equal(root.files.has('.waveguide-tasks.index.v1.json'), true);
  } finally {
    resetSelectedFolder();
  }
});

test('simulation workspace service writes normalized folder index entries', async () => {
  const root = createMemoryDirectory();
  setSelectedFolderHandle(root, { label: 'workspace' });

  try {
    const result = await syncSimulationWorkspaceIndex([
      {
        id: 'job-folder-2',
        status: 'complete',
        exportedFiles: ['result.csv'],
        scriptSnapshot: { outputName: 'horn' }
      }
    ]);

    assert.equal(result.synced, true);
    assert.equal(result.items.length, 1);

    const restored = await readSimulationWorkspaceJobs();
    assert.equal(restored.repaired, false);
    assert.equal(restored.items.length, 1);
    assert.equal(restored.items[0].id, 'job-folder-2');
    assert.deepEqual(restored.items[0].exportedFiles, ['result.csv']);
    assert.deepEqual(restored.items[0].scriptSnapshot, { outputName: 'horn' });
  } finally {
    resetSelectedFolder();
  }
});

test('simulation use case builds cancelled job state and resolves failed cleanup IDs', () => {
  const pending = buildCancellationRequestedSimulationJob(
    { id: 'job-1', status: 'running', stage: 'solver_setup' },
    { message: 'Cancellation requested by user' }
  );
  assert.equal(pending.status, 'running');
  assert.equal(pending.stage, 'cancelling');
  assert.equal(pending.stageMessage, 'Cancellation requested by user');
  assert.equal(pending.cancellationRequested, true);

  const cancelled = buildCancelledSimulationJob(
    { id: 'job-1', status: 'running', stage: 'solver_setup' },
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
