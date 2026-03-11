import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildCanonicalMeshPayload } from '../src/geometry/index.js';
import { SimulationModule } from '../src/modules/simulation/index.js';
import { DesignModule } from '../src/modules/design/index.js';
import {
  validateSimulationConfig,
  buildQueuedSimulationJob,
  buildCancelledSimulationJob,
  resolveClearedFailedJobIds
} from '../src/modules/simulation/useCases.js';

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
      frequencySpacing: 'log',
      deviceMode: 'auto',
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
});

test('simulation use case builds cancelled job state and resolves failed cleanup IDs', () => {
  const cancelled = buildCancelledSimulationJob(
    { id: 'job-1', status: 'running', stage: 'solver_setup' },
    { completedAt: '2026-03-11T10:01:00.000Z' }
  );
  assert.equal(cancelled.status, 'cancelled');
  assert.equal(cancelled.stageMessage, 'Simulation cancelled by user');
  assert.equal(cancelled.completedAt, '2026-03-11T10:01:00.000Z');
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
