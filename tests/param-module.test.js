import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import { prepareGeometryParams } from '../src/geometry/index.js';
import { ParamModule } from '../src/modules/param/index.js';

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

test('ParamModule prepares raw params with staged import, task, and output parity', () => {
  const rawParams = makeRawParams({ encDepth: 180, quadrants: '1' });
  const paramInput = ParamModule.import(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const paramTask = ParamModule.task(paramInput);
  const expected = prepareGeometryParams(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });

  assert.equal(paramInput.module, 'param');
  assert.equal(paramInput.stage, 'import');
  assert.equal(paramTask.stage, 'task');
  assert.equal(
    JSON.stringify(ParamModule.output.params(paramTask)),
    JSON.stringify(expected)
  );
});

test('ParamModule.importState derives type and params from app state', () => {
  const state = {
    type: 'OSSE',
    params: makeRawParams({ L: '150', scale: 2 })
  };

  const paramTask = ParamModule.task(
    ParamModule.importState(state, {
      applyVerticalOffset: false
    })
  );

  assert.equal(ParamModule.output.params(paramTask).type, 'OSSE');
  assert.equal(ParamModule.output.params(paramTask).verticalOffset, 0);
  assert.equal(ParamModule.output.params(paramTask).L, 300);
});

test('ParamModule.importPrepared preserves already prepared params', () => {
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

  const paramTask = ParamModule.task(ParamModule.importPrepared(preparedParams));

  assert.equal(ParamModule.output.params(paramTask), preparedParams);
});
