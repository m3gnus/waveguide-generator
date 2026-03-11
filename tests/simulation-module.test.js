import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams, buildCanonicalMeshPayload } from '../src/geometry/index.js';
import { SimulationModule } from '../src/modules/simulation/index.js';

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
