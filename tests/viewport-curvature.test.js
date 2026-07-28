import test from 'node:test';
import assert from 'node:assert/strict';
import * as THREE from 'three';

import { calculateCurvatureColors } from '../src/app/scene.js';

test('curvature colors preserve the established per-vertex jet mapping', () => {
  const normals = new Float32Array([
    1, 0, 0, 0.9238795, 0.3826834, 0, 0.7071068, 0.7071068, 0, 0, 1, 0, -0.7071068, 0.7071068, 0,
    -1, 0, 0, 0, 0, 1,
  ]);
  const indices = new Uint16Array([0, 1, 2, 1, 3, 2, 2, 3, 4, 2, 4, 5]);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('normal', new THREE.BufferAttribute(normals, 3));
  geometry.setIndex(new THREE.BufferAttribute(indices, 1));

  assert.deepEqual(
    Array.from(calculateCurvatureColors(geometry)),
    [
      0, 0.9510001540184021, 1, 0.07451574504375458, 1, 0.9254842400550842, 1, 0.578778088092804, 0,
      0.7170846462249756, 1, 0.2829153537750244, 1, 0.42121344804763794, 0, 0.5, 0, 0, 0, 0, 0.5,
    ]
  );
});
