import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  GeometryModule,
  prepareGeometryParams,
  buildGeometryShape,
  buildGeometryMeshFromShape
} from '../src/geometry/index.js';
import { DesignModule } from '../src/modules/design/index.js';
import { prepareViewportMesh } from '../src/modules/geometry/useCases.js';

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

test('GeometryModule exposes import, task, and shape-only output stages', () => {
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
  const expectedShape = buildGeometryShape(expectedPrepared, {
    includeEnclosure: true
  });

  assert.equal(geometryInput.module, 'geometry');
  assert.equal(geometryInput.stage, 'import');
  assert.equal(geometryTask.stage, 'task');
  const geometryShape = GeometryModule.output.shape(geometryTask);
  assert.equal(GeometryModule.output.geometry(geometryTask), geometryShape);
  assert.equal(geometryShape.kind, expectedShape.kind);
  assert.deepEqual(geometryShape.tessellation, expectedShape.tessellation);
  assert.equal(geometryShape.buildParams.type, expectedShape.buildParams.type);
  assert.equal(geometryShape.buildParams.quadrants, expectedShape.buildParams.quadrants);
  assert.equal(geometryShape.buildParams.angularSegments, expectedShape.buildParams.angularSegments);
  assert.equal(geometryShape.buildParams.lengthSegments, expectedShape.buildParams.lengthSegments);
  const expectedMesh = buildGeometryMeshFromShape(expectedShape, {
    includeEnclosure: true
  });
  const actualMesh = buildGeometryMeshFromShape(geometryShape, {
    includeEnclosure: true
  });
  assert.deepEqual(actualMesh, expectedMesh);
  assert.equal(typeof GeometryModule.output.simulation, 'undefined');
  assert.equal(typeof GeometryModule.output.export, 'undefined');
  assert.equal(typeof GeometryModule.output.canonical, 'undefined');
  assert.equal(typeof GeometryModule.output.mesh, 'undefined');
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
  const expectedShape = buildGeometryShape(preparedParams, {
    includeEnclosure: false
  });

  assert.equal(geometryInput.params.L, preparedParams.L);
  assert.equal(geometryInput.params.r0, preparedParams.r0);
  const shape = GeometryModule.output.shape(geometryTask);
  assert.equal(shape.kind, expectedShape.kind);
  assert.deepEqual(shape.tessellation, expectedShape.tessellation);
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

test('prepareViewportMesh consumes an explicit state snapshot instead of ambient state', () => {
  const state = {
    type: 'OSSE',
    params: makeRawParams({
      encDepth: 180,
      quadrants: '12'
    })
  };

  const viewportMesh = prepareViewportMesh(state);
  const expectedPrepared = prepareGeometryParams(state.params, {
    type: state.type,
    applyVerticalOffset: true
  });
  const expectedShape = buildGeometryShape(expectedPrepared, {
    adaptivePhi: false
  });
  const expectedMesh = buildGeometryMeshFromShape(expectedShape, {
    adaptivePhi: false
  });

  assert.equal(viewportMesh.preparedParams.type, expectedPrepared.type);
  assert.deepEqual(viewportMesh.vertices, expectedMesh.vertices);
  assert.deepEqual(viewportMesh.indices, expectedMesh.indices);
  assert.deepEqual(viewportMesh.groups, expectedMesh.groups);
});
