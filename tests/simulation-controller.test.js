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
  recordSimulationControllerRating,
  removeSimulationControllerJob,
  restoreSimulationControllerJobs,
  restoreSimulationPanelRuntime,
  disposeSimulationPanelRuntime,
  stopSimulationControllerJob,
  submitSimulationControllerJob
} from '../src/ui/simulation/controller.js';
import { JOB_TRACKER_CONSTANTS } from '../src/ui/simulation/jobTracker.js';

test('createSimulationControllerStore initializes expected controller state', () => {
  const solver = {};
  const controller = createSimulationControllerStore({ solver });

  assert.equal(controller.solver, solver);
  assert.equal(controller.activeJobId, null);
  assert.equal(controller.currentJobId, null);
  assert.equal(controller.pollDelayMs, 1000);
  assert.equal(controller.pollBackoffMs, 1000);
  assert.equal(controller.isPolling, false);
  assert.equal(controller.jobSourceMode, 'backend');
  assert.equal(controller.jobSourceLabel, 'Backend Jobs');
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

test('restoreSimulationControllerJobs initializes with empty workspace and folder source mode', async () => {
  const controller = createSimulationControllerStore({ solver: {} });

  let jobsUpdatedCalls = 0;

  await restoreSimulationControllerJobs(controller, {
    onJobsUpdated: () => {
      jobsUpdatedCalls += 1;
    }
  });

  // readSimulationWorkspaceJobs always returns empty items (backend-only mode)
  assert.equal(controller.jobs.size, 0);
  assert.equal(controller.jobSourceMode, 'folder');
  assert.equal(jobsUpdatedCalls >= 1, true);
});

test('restoreSimulationControllerJobs returns empty jobs with no stale localStorage items', async () => {
  const controller = createSimulationControllerStore({ solver: {} });

  await restoreSimulationControllerJobs(controller);

  // Workspace always returns empty (backend-only); no stale localStorage items loaded
  assert.equal(controller.jobs.has('job-stale-complete'), false);
  assert.equal(controller.jobs.size, 0);
  assert.equal(controller.jobSourceMode, 'folder');
});

test('restoreSimulationControllerJobs sets folder source mode without calling solver listJobs', async () => {
  let listJobsCalls = 0;
  const controller = createSimulationControllerStore({
    solver: {
      async listJobs() {
        listJobsCalls += 1;
        return { items: [{ id: 'job-backend-1', status: 'complete' }] };
      }
    }
  });

  await restoreSimulationControllerJobs(controller);

  // Restore does not call solver.listJobs; uses workspace which returns empty
  assert.equal(listJobsCalls, 0);
  assert.equal(controller.jobSourceMode, 'folder');
  assert.equal(controller.jobSourceLabel, 'Folder Tasks');
  assert.deepEqual(Array.from(controller.jobs.keys()), []);
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
    solver: {},
    createUiCoordinator() {
      return { bind() {}, dispose() {} };
    }
  });

  await restoreSimulationPanelRuntime(runtime);

  // Workspace returns empty items; controller initializes with folder source mode
  assert.equal(runtime.controller.jobs.size, 0);
  assert.equal(runtime.controller.jobSourceMode, 'folder');
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
        return {
          jobId,
          spl_on_axis: { frequencies: [100], spl: [90] },
          metadata: {}
        };
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

test('reconcileSimulationControllerRemoteJobs updates active jobs via per-job status check', async () => {
  const controller = createSimulationControllerStore({
    solver: {
      async getJobStatus(id) {
        if (id === 'job-active') {
          return {
            id: 'job-active',
            status: 'running',
            progress: 0.7,
            stage: 'solving',
            stage_message: 'Almost done',
            created_at: '2026-03-11T10:00:00.000Z'
          };
        }
        throw new Error('not found');
      }
    }
  });

  controller.jobs.set('job-active', {
    id: 'job-active',
    status: 'running',
    progress: 0.2,
    createdAt: '2026-03-11T10:00:00.000Z'
  });
  controller.activeJobId = 'job-active';
  controller.currentJobId = 'job-active';

  const result = await reconcileSimulationControllerRemoteJobs(controller);

  assert.equal(result.anyActive, true);
  assert.equal(result.activeJob?.id, 'job-active');
  assert.equal(controller.jobs.get('job-active')?.progress, 0.7);
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
      polarConfig: {}
    },
    waveguidePayload: { formula_type: 'OSSE' },
    preparedParams: { L: 120 },
    stateSnapshot: { type: 'OSSE', params: { L: 120 } }
  });

  assert.equal(created.id, 'job-queued-1');
  assert.equal(controller.activeJobId, 'job-queued-1');
  assert.equal(controller.currentJobId, 'job-queued-1');

  const updated = await recordSimulationControllerExport(controller, 'job-queued-1', {
    exportedFiles: ['csv:simulation_results.csv', 'json:simulation_results.json'],
    autoExportCompletedAt: '2026-03-11T10:01:00.000Z',
    rawResultsFile: 'simulation_2_raw.results.json',
    meshArtifactFile: 'simulation_2_solver.mesh.msh',
    justCompleted: false
  });
  assert.deepEqual(updated.exportedFiles, ['csv:simulation_results.csv', 'json:simulation_results.json']);
  assert.equal(updated.autoExportCompletedAt, '2026-03-11T10:01:00.000Z');
  assert.equal(updated.rawResultsFile, 'simulation_2_raw.results.json');
  assert.equal(updated.meshArtifactFile, 'simulation_2_solver.mesh.msh');
  assert.equal(updated.justCompleted, false);
});

test('recordSimulationControllerRating persists bounded rating values', async () => {
  const controller = createSimulationControllerStore({ solver: {} });
  controller.jobs.set('job-rate-1', {
    id: 'job-rate-1',
    status: 'complete',
    rating: null,
    exportedFiles: []
  });

  const updated = await recordSimulationControllerRating(controller, 'job-rate-1', 7);
  assert.equal(updated.rating, 5);
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
        return {
          solverReady: false,
          occBuilderReady: true,
          dependencyDoctor: {
            components: [
              {
                id: 'bempp_cl',
                name: 'bempp-cl',
                category: 'required',
                status: 'missing',
                featureImpact: '/api/solve BEM simulation is unavailable.',
                guidance: ['Install bempp-cl: pip install git+https://github.com/bempp/bempp-cl.git']
              }
            ]
          }
        };
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
    /Install bempp-cl/i
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
