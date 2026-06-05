import test from 'node:test';
import assert from 'node:assert/strict';

import {
  analyzeBemMeshIntegrity,
  assertBemMeshIntegrity,
  orientMeshConsistently
} from '../src/geometry/meshIntegrity.js';

test('assertBemMeshIntegrity rejects non-manifold edges', () => {
  const vertices = [
    0, 0, 0,
    1, 0, 0,
    0, 1, 0,
    0, 0, 1,
    0, -1, 0
  ];
  const indices = [
    0, 1, 2,
    1, 0, 3,
    0, 1, 4
  ];

  const report = analyzeBemMeshIntegrity(vertices, indices, {
    requireClosed: false,
    requireSingleComponent: false
  });
  assert.ok(report.nonManifoldEdges > 0);

  assert.throws(
    () => assertBemMeshIntegrity(vertices, indices, { requireClosed: false, requireSingleComponent: false }),
    /shared by more than two triangles/
  );
});

test('assertBemMeshIntegrity rejects duplicate/coincident triangles', () => {
  const vertices = [
    0, 0, 0,
    1, 0, 0,
    0, 1, 0
  ];
  const indices = [
    0, 1, 2,
    2, 1, 0
  ];

  const report = analyzeBemMeshIntegrity(vertices, indices, {
    requireClosed: false,
    requireSingleComponent: false
  });
  assert.ok(report.duplicateTrianglesByGeometry > 0);

  assert.throws(
    () => assertBemMeshIntegrity(vertices, indices, { requireClosed: false, requireSingleComponent: false }),
    /Duplicate\/coincident surface check failed/
  );
});

test('analyzeBemMeshIntegrity scale diagnostics stay behind debug logging', () => {
  const originalDebug = globalThis.__WAVEGUIDE_DEBUG__;
  const originalError = console.error;
  const errors = [];

  globalThis.__WAVEGUIDE_DEBUG__ = false;
  console.error = (...args) => {
    errors.push(args);
  };

  try {
    const report = analyzeBemMeshIntegrity(
      [
        0, 0, 0,
        1, 0, 0,
        0, 1, 0
      ],
      [
        0, 1, 2
      ],
      {
        requireClosed: false,
        requireSingleComponent: false,
        scale: 0.5
      }
    );
    assert.equal(report.triCount, 1);
  } finally {
    console.error = originalError;
    if (typeof originalDebug === 'undefined') {
      delete globalThis.__WAVEGUIDE_DEBUG__;
    } else {
      globalThis.__WAVEGUIDE_DEBUG__ = originalDebug;
    }
  }

  assert.deepEqual(errors, []);
});

test('orientMeshConsistently fixes flipped orientation islands in a closed mesh', () => {
  const vertices = [
    0, 0, 0,
    1, 0, 0,
    0, 1, 0,
    0, 0, 1
  ];

  const indices = [
    0, 2, 1,
    0, 1, 3,
    1, 2, 3,
    2, 3, 0
  ];

  const before = analyzeBemMeshIntegrity(vertices, indices, {
    requireClosed: true,
    requireSingleComponent: true
  });
  assert.ok(before.sameDirectionSharedEdges > 0);

  orientMeshConsistently(vertices, indices, { preferOutward: true });

  const after = analyzeBemMeshIntegrity(vertices, indices, {
    requireClosed: true,
    requireSingleComponent: true
  });
  assert.equal(after.sameDirectionSharedEdges, 0);
  assert.equal(after.boundaryEdges, 0);
  assert.equal(after.nonManifoldEdges, 0);
  assertBemMeshIntegrity(vertices, indices, {
    requireClosed: true,
    requireSingleComponent: true
  });
});
