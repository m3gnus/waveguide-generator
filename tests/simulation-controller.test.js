import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SIMULATION_CONTROLLER_FIELDS,
  cancelSimulationControllerJob,
  clearSimulationControllerJobs,
  createSimulationControllerStore,
  createSimulationPanelRuntime,
  bindSimulationControllerState,
  ensureSimulationControllerJobResults,
  queueSimulationControllerJob,
  reconcileSimulationControllerRemoteJobs,
  recordSimulationControllerExport,
  removeSimulationControllerJob,
  restoreSimulationControllerJobs,
  restoreSimulationPanelRuntime,
  disposeSimulationPanelRuntime,
  stopSimulationControllerJob,
  submitSimulationControllerJob
} from '../src/ui/simulation/controller.js';

test('createSimulationControllerStore initializes expected controller state', () => {
  const solver = {};
  const controller = createSimulationControllerStore({ solver });

  assert.equal(controller.solver, solver);
  assert.equal(controller.activeJobId, null);
  assert.equal(controller.currentJobId, null);
  assert.equal(controller.pollDelayMs, 1000);
  assert.equal(controller.pollBackoffMs, 1000);
  assert.equal(controller.isPolling, false);
  assert.ok(controller.jobs instanceof Map);
  assert.ok(controller.resultCache instanceof Map);
  assert.equal(Array.isArray(controller.simulationParamBindings), true);
  assert.equal(controller.simulationParamBindings.length, 3);
});

test('bindSimulationControllerState creates live field proxies on panel adapter', () => {
  const controller = createSimulationControllerStore({ solver: {} });
  const panelAdapter = {};

  bindSimulationControllerState(panelAdapter, controller);

  for (const field of SIMULATION_CONTROLLER_FIELDS) {
    assert.ok(field in panelAdapter, `expected proxied field ${field}`);
  }

  panelAdapter.activeJobId = 'job-42';
  assert.equal(controller.activeJobId, 'job-42');

  controller.pollDelayMs = 2500;
  assert.equal(panelAdapter.pollDelayMs, 2500);
});

test('restoreSimulationControllerJobs hydrates job state and signals polling for active remote jobs', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async listJobs() {
        return {
          items: [
            {
              id: 'job-live-1',
              status: 'running',
              progress: 0.6,
              stage: 'solving',
              stage_message: 'Simulation running',
              created_at: '2026-03-11T09:00:00.000Z',
              started_at: '2026-03-11T09:00:02.000Z'
            }
          ]
        };
      }
    }
  });

  let jobsUpdatedCalls = 0;
  let startPollingCalls = 0;

  await restoreSimulationControllerJobs(controller, {
    onJobsUpdated: () => {
      jobsUpdatedCalls += 1;
    },
    onStartPolling: () => {
      startPollingCalls += 1;
    }
  });

  assert.ok(controller.jobs.has('job-live-1'));
  assert.equal(controller.activeJobId, 'job-live-1');
  assert.equal(controller.currentJobId, 'job-live-1');
  assert.equal(startPollingCalls, 1);
  assert.equal(jobsUpdatedCalls >= 2, true);
});

test('createSimulationPanelRuntime binds a controller store and injected ui coordinator', () => {
  const panelAdapter = {};
  const solver = { id: 'solver-test' };
  const fakeUiCoordinator = { bind() {}, dispose() {} };

  const runtime = createSimulationPanelRuntime(panelAdapter, {
    solver,
    createUiCoordinator() {
      return fakeUiCoordinator;
    }
  });

  assert.equal(runtime.controller.solver, solver);
  assert.equal(runtime.uiCoordinator, fakeUiCoordinator);
  assert.equal(panelAdapter.solver, solver);

  panelAdapter.activeJobId = 'job-runtime-1';
  assert.equal(runtime.controller.activeJobId, 'job-runtime-1');
});

test('restoreSimulationPanelRuntime delegates to controller restore using runtime controller', async () => {
  const panelAdapter = {};
  const runtime = createSimulationPanelRuntime(panelAdapter, {
    solver: {
      async listJobs() {
        return {
          items: [
            {
              id: 'job-runtime-live',
              status: 'running',
              progress: 0.2,
              created_at: '2026-03-11T09:00:00.000Z'
            }
          ]
        };
      }
    },
    createUiCoordinator() {
      return { bind() {}, dispose() {} };
    }
  });

  let startPollingCalls = 0;
  await restoreSimulationPanelRuntime(runtime, {
    onStartPolling: () => {
      startPollingCalls += 1;
    }
  });

  assert.equal(runtime.controller.activeJobId, 'job-runtime-live');
  assert.equal(startPollingCalls, 1);
});

test('disposeSimulationPanelRuntime clears timers and disposes ui coordinator', () => {
  const originalClearTimeout = global.clearTimeout;
  const cleared = [];
  global.clearTimeout = (timerId) => {
    cleared.push(timerId);
  };

  try {
    const runtime = {
      controller: {
        pollTimer: { id: 'poll' },
        pollInterval: { id: 'interval' },
        isPolling: true,
        connectionPollTimer: { id: 'connection' }
      },
      uiCoordinator: {
        disposed: false,
        dispose() {
          this.disposed = true;
        }
      }
    };

    disposeSimulationPanelRuntime(runtime);

    assert.deepEqual(cleared, [{ id: 'poll' }, { id: 'connection' }]);
    assert.equal(runtime.controller.pollTimer, null);
    assert.equal(runtime.controller.pollInterval, null);
    assert.equal(runtime.controller.isPolling, false);
    assert.equal(runtime.controller.connectionPollTimer, null);
    assert.equal(runtime.uiCoordinator.disposed, true);
  } finally {
    global.clearTimeout = originalClearTimeout;
  }
});

test('ensureSimulationControllerJobResults handles missing, incomplete, cached, and fetched jobs', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async getResults(jobId) {
        return { jobId, spl_on_axis: { frequencies: [100], spl: [90] } };
      }
    }
  });
  controller.jobs.set('job-complete', { id: 'job-complete', status: 'complete' });
  controller.jobs.set('job-running', { id: 'job-running', status: 'running' });
  controller.resultCache.set('job-cached', { cached: true });
  controller.jobs.set('job-cached', { id: 'job-cached', status: 'complete' });

  const displayed = [];

  const missing = await ensureSimulationControllerJobResults(controller, 'job-missing');
  assert.equal(missing.reason, 'missing_job');

  const incomplete = await ensureSimulationControllerJobResults(controller, 'job-running');
  assert.equal(incomplete.reason, 'not_complete');

  const cached = await ensureSimulationControllerJobResults(controller, 'job-cached', {
    displayResults(results) {
      displayed.push(results);
    }
  });
  assert.equal(cached.reason, 'cached');
  assert.deepEqual(displayed[0], { cached: true });

  const fetched = await ensureSimulationControllerJobResults(controller, 'job-complete', {
    displayResults(results) {
      displayed.push(results);
    }
  });
  assert.equal(fetched.reason, 'fetched');
  assert.equal(controller.activeJobId, 'job-complete');
  assert.equal(controller.currentJobId, 'job-complete');
  assert.equal(controller.resultCache.has('job-complete'), true);
});

test('reconcileSimulationControllerRemoteJobs merges remote data and marks missing active jobs as lost', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async listJobs() {
        return {
          items: [
            {
              id: 'job-remote-running',
              status: 'running',
              progress: 0.4,
              stage: 'solving',
              stage_message: 'Solving on backend',
              created_at: '2026-03-11T10:00:00.000Z'
            }
          ]
        };
      }
    }
  });

  controller.jobs.set('job-lost-running', {
    id: 'job-lost-running',
    status: 'running',
    progress: 0.2,
    createdAt: '2026-03-11T09:59:00.000Z'
  });
  controller.activeJobId = 'job-lost-running';
  controller.currentJobId = 'job-lost-running';

  const result = await reconcileSimulationControllerRemoteJobs(controller);

  assert.equal(result.anyActive, true);
  assert.equal(result.activeJob?.id, 'job-lost-running');
  assert.equal(controller.activeJobId, 'job-lost-running');
  assert.equal(controller.currentJobId, 'job-lost-running');
  assert.equal(controller.jobs.get('job-remote-running')?.status, 'running');
  assert.equal(controller.jobs.get('job-lost-running')?.status, 'error');
  assert.match(controller.jobs.get('job-lost-running')?.errorMessage || '', /lost/i);
});

test('queueSimulationControllerJob and recordSimulationControllerExport update controller job metadata', async () => {
  const controller = createSimulationControllerStore({ solver: {} });

  const created = await queueSimulationControllerJob(controller, {
    jobId: 'job-queued-1',
    startedIso: '2026-03-11T10:00:00.000Z',
    outputName: 'simulation',
    counter: 2,
    config: {
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 5,
      frequencySpacing: 'log',
      deviceMode: 'auto',
      polarConfig: {}
    },
    waveguidePayload: { formula_type: 'OSSE' },
    preparedParams: { L: 120 },
    stateSnapshot: { type: 'OSSE', params: { L: 120 } }
  });

  assert.equal(created.id, 'job-queued-1');
  assert.equal(controller.activeJobId, 'job-queued-1');
  assert.equal(controller.currentJobId, 'job-queued-1');

  const updated = await recordSimulationControllerExport(controller, 'job-queued-1', 'export-csv:2026-03-11T10:01:00.000Z');
  assert.deepEqual(updated.exportedFiles, ['export-csv:2026-03-11T10:01:00.000Z']);
});

test('submitSimulationControllerJob checks solver health and queues the submitted job through the controller boundary', async () => {
  const calls = [];
  const controller = createSimulationControllerStore({
    solver: {
      async getHealthStatus() {
        calls.push(['health']);
        return { solverReady: true, occBuilderReady: true };
      },
      async submitSimulation(config, meshData, submitOptions) {
        calls.push(['submit', config, meshData, submitOptions]);
        return 'job-submit-1';
      }
    }
  });

  const config = {
    frequencyStart: 100,
    frequencyEnd: 1000,
    numFrequencies: 6,
    meshValidationMode: 'strict',
    frequencySpacing: 'log',
    deviceMode: 'auto',
    useOptimized: true,
    enableSymmetry: true,
    verbose: false,
    polarConfig: {}
  };
  const meshData = {
    vertices: [0, 0, 0, 1, 0, 0, 0, 1, 0],
    indices: [0, 1, 2],
    surfaceTags: [2]
  };
  const submission = {
    waveguidePayload: { formula_type: 'OSSE' },
    submitOptions: { mesh: { strategy: 'occ_adaptive' } },
    preparedParams: { L: 120 },
    stateSnapshot: { type: 'OSSE', params: { L: 120 } }
  };

  const result = await submitSimulationControllerJob(controller, {
    config,
    meshData,
    outputName: 'simulation',
    counter: 3,
    submission
  });

  assert.equal(result.jobId, 'job-submit-1');
  assert.equal(result.createdJob.id, 'job-submit-1');
  assert.equal(controller.activeJobId, 'job-submit-1');
  assert.equal(controller.currentJobId, 'job-submit-1');
  assert.deepEqual(calls, [
    ['health'],
    ['submit', config, meshData, submission.submitOptions]
  ]);
});

test('submitSimulationControllerJob rejects when backend solver dependencies are unavailable', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async getHealthStatus() {
        return { solverReady: false, occBuilderReady: true };
      }
    }
  });

  await assert.rejects(
    () => submitSimulationControllerJob(controller, {
      config: {
        frequencyStart: 100,
        frequencyEnd: 1000,
        numFrequencies: 3
      },
      meshData: { vertices: [], indices: [], surfaceTags: [] },
      outputName: 'simulation',
      counter: 1,
      submission: {
        waveguidePayload: { formula_type: 'OSSE' },
        submitOptions: {},
        preparedParams: {},
        stateSnapshot: { params: {} }
      }
    }),
    /backend solver and OCC mesher must be ready/i
  );
});

test('stopSimulationControllerJob keeps running job in cancelling state until backend confirms stop', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async stopJob() {
        return {
          status: 'cancelling',
          message: 'Cancellation requested for job job-running'
        };
      }
    }
  });
  controller.jobs.set('job-running', { id: 'job-running', status: 'running', progress: 0.4 });
  controller.activeJobId = 'job-running';
  controller.currentJobId = 'job-running';

  const result = await stopSimulationControllerJob(controller, 'job-running');

  assert.equal(result.stopError, null);
  assert.equal(result.cancelledJob?.status, 'running');
  assert.equal(result.cancelledJob?.stage, 'cancelling');
  assert.equal(controller.jobs.get('job-running')?.status, 'running');
  assert.equal(controller.jobs.get('job-running')?.stage, 'cancelling');
});

test('stopSimulationControllerJob does not fake a local cancel when stop API fails', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async stopJob() {
        throw new Error('network down');
      }
    }
  });
  controller.jobs.set('job-running', { id: 'job-running', status: 'running' });
  controller.activeJobId = 'job-running';
  controller.currentJobId = 'job-running';

  const result = await stopSimulationControllerJob(controller, 'job-running');

  assert.match(result.stopError?.message || '', /network down/i);
  assert.equal(result.cancelledJob, null);
  assert.equal(controller.jobs.get('job-running')?.status, 'running');
});

test('controller job mutation helpers remove, clear, and cancel jobs via controller boundary', () => {
  const controller = createSimulationControllerStore({ solver: {} });
  controller.jobs.set('job-error-1', { id: 'job-error-1', status: 'error' });
  controller.jobs.set('job-error-2', { id: 'job-error-2', status: 'error' });
  controller.jobs.set('job-running', { id: 'job-running', status: 'running' });
  controller.activeJobId = 'job-running';
  controller.currentJobId = 'job-running';

  const cancelled = cancelSimulationControllerJob(controller, 'job-running');
  assert.equal(cancelled.status, 'cancelled');
  assert.equal(controller.jobs.get('job-running').status, 'cancelled');

  const cleared = clearSimulationControllerJobs(controller, ['job-error-1', 'job-error-2']);
  assert.equal(cleared, 2);
  assert.equal(controller.jobs.has('job-error-1'), false);
  assert.equal(controller.jobs.has('job-error-2'), false);

  const removed = removeSimulationControllerJob(controller, 'job-running');
  assert.equal(removed, true);
  assert.equal(controller.jobs.has('job-running'), false);
  assert.equal(controller.activeJobId, null);
  assert.equal(controller.currentJobId, null);
});
