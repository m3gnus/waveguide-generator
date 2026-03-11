import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  GeometryModule,
  prepareGeometryParams,
  buildGeometryArtifacts,
  buildCanonicalMeshPayload
} from '../src/geometry/index.js';

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

test('GeometryModule exposes import, task, and output stages with legacy parity', () => {
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
  const expectedArtifacts = buildGeometryArtifacts(expectedPrepared, {
    includeEnclosure: true
  });

  assert.equal(geometryInput.module, 'geometry');
  assert.equal(geometryInput.stage, 'import');
  assert.equal(geometryTask.stage, 'task');
  assert.deepEqual(GeometryModule.output.mesh(geometryTask), expectedArtifacts.mesh);
  assert.deepEqual(GeometryModule.output.simulation(geometryTask), expectedArtifacts.simulation);
  assert.equal(
    GeometryModule.output.export(geometryTask).verticalOffset,
    expectedArtifacts.export.verticalOffset
  );
  assert.deepEqual(
    GeometryModule.output.export(geometryTask).toAthVertices(),
    expectedArtifacts.export.toAthVertices()
  );
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
  const expectedArtifacts = buildGeometryArtifacts(preparedParams, {
    includeEnclosure: false
  });

  assert.equal(geometryInput.params.L, preparedParams.L);
  assert.equal(geometryInput.params.r0, preparedParams.r0);
  assert.deepEqual(GeometryModule.output.mesh(geometryTask), expectedArtifacts.mesh);
});

test('GeometryModule canonical output matches legacy canonical payload builder', () => {
  const rawParams = makeRawParams({ encDepth: 180, quadrants: '14' });
  const geometryInput = GeometryModule.import(rawParams, {
    type: 'OSSE',
    applyVerticalOffset: true
  });

  const expected = buildCanonicalMeshPayload(geometryInput.params, {
    includeEnclosure: true
  });

  assert.deepEqual(
    GeometryModule.output.canonical(geometryInput, { includeEnclosure: true }),
    expected
  );
});
