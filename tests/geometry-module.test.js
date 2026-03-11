import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  GeometryModule,
  prepareGeometryParams,
  buildGeometryMesh
} from '../src/geometry/index.js';
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

test('GeometryModule exposes import, task, and mesh-only output stages', () => {
  const rawParams = makeRawParams({ encDepth: 200, quadrants: '1' });
  const geometryInput = GeometryModule.import(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const geometryTask = GeometryModule.task(geometryInput, {
    includeEnclosure: true
  });

  const expectedPrepared = prepareGeometryParams(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });
  const expectedMesh = buildGeometryMesh(expectedPrepared, {
    includeEnclosure: true
  });

  assert.equal(geometryInput.module, 'geometry');
  assert.equal(geometryInput.stage, 'import');
  assert.equal(geometryTask.stage, 'task');
  assert.deepEqual(GeometryModule.output.mesh(geometryTask), expectedMesh);
  assert.equal(typeof GeometryModule.output.simulation, 'undefined');
  assert.equal(typeof GeometryModule.output.export, 'undefined');
  assert.equal(typeof GeometryModule.output.canonical, 'undefined');
});

test('GeometryModule.importPrepared preserves already prepared geometry params', () => {
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

  const geometryInput = GeometryModule.importPrepared(preparedParams);
  const geometryTask = GeometryModule.task(geometryInput, {
    includeEnclosure: false
  });
  const expectedMesh = buildGeometryMesh(preparedParams, {
    includeEnclosure: false
  });

  assert.equal(geometryInput.params.L, preparedParams.L);
  assert.equal(geometryInput.params.r0, preparedParams.r0);
  assert.deepEqual(GeometryModule.output.mesh(geometryTask), expectedMesh);
});

test('GeometryModule.importDesign consumes DesignModule task output directly', () => {
  const rawParams = makeRawParams({ encDepth: 180, quadrants: '12' });
  const designTask = DesignModule.task(
    DesignModule.import(rawParams, {
      type: 'OSSE',
      applyVerticalOffset: true
    })
  );
  const geometryInput = GeometryModule.importDesign(designTask);

  const expectedPrepared = prepareGeometryParams(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });

  assert.equal(
    JSON.stringify(geometryInput.params),
    JSON.stringify(expectedPrepared)
  );
});
