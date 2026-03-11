import test from 'node:test';
import assert from 'node:assert/strict';

import {
  SIMULATION_CONTROLLER_FIELDS,
  createSimulationControllerStore,
  bindSimulationControllerState,
  restoreSimulationControllerJobs
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
