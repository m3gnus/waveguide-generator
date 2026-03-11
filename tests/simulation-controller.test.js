import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SIMULATION_CONTROLLER_FIELDS,
  createSimulationControllerStore,
  createSimulationPanelRuntime,
  bindSimulationControllerState,
  restoreSimulationControllerJobs,
  restoreSimulationPanelRuntime,
  disposeSimulationPanelRuntime
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
