import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams } from '../src/geometry/index.js';
import { DesignModule } from '../src/modules/design/index.js';

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

test('DesignModule prepares state params with staged import, task, and output parity', () => {
  const rawParams = makeRawParams({ encDepth: 180, quadrants: '1' });
  const designInput = DesignModule.import(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const designTask = DesignModule.task(designInput);
  const expected = prepareGeometryParams(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });

  assert.equal(designInput.module, 'design');
  assert.equal(designInput.stage, 'import');
  assert.equal(designTask.stage, 'task');
  assert.equal(
    JSON.stringify(DesignModule.output.preparedParams(designTask)),
    JSON.stringify(expected)
  );
});

test('DesignModule.importState derives type and params from app state', () => {
  const state = {
    type: 'OSSE',
    params: makeRawParams({ L: '150', scale: 2 })
  };

  const designTask = DesignModule.task(
    DesignModule.importState(state, {
      applyVerticalOffset: false
    })
  );

  assert.equal(DesignModule.output.preparedParams(designTask).type, 'OSSE');
  assert.equal(DesignModule.output.preparedParams(designTask).verticalOffset, 0);
  assert.equal(DesignModule.output.preparedParams(designTask).L, 300);
});

test('DesignModule output helpers preserve pre-prepared params', () => {
  const preparedParams = prepareGeometryParams(
    makeRawParams({
      scale: 2,
      L: '100',
      r0: '10'
    }),
    {
      type: 'OSSE',
      applyVerticalOffset: true
    }
  );

  const designTask = DesignModule.task(DesignModule.importPrepared(preparedParams));

  assert.equal(DesignModule.output.preparedParams(designTask), preparedParams);
  assert.equal(DesignModule.output.exportParams(designTask), preparedParams);
  assert.equal(DesignModule.output.simulationParams(designTask), preparedParams);
});
