import test from 'node:test';
import assert from 'node:assert/strict';

import { provideMeshForSimulation } from '../src/app/mesh.js';
import { validateCanonicalMeshPayload } from '../src/solver/index.js';

test('app mesh provider emits HornLab solve contract placeholder, not a JS geometry mesh', () => {
  let publishedPayload = null;
  const originalDebug = globalThis.__WAVEGUIDE_DEBUG__;
  const originalConsoleLog = console.log;
  const logMessages = [];

  const app = {
    publishSimulationMesh(payload) {
      publishedPayload = payload;
      return payload;
    },
    publishSimulationMeshError() {
      throw new Error('unexpected mesh error');
    }
  };

  globalThis.__WAVEGUIDE_DEBUG__ = false;
  console.log = (...args) => {
    logMessages.push(args.join(' '));
  };

  try {
    provideMeshForSimulation(app);
  } finally {
    console.log = originalConsoleLog;
    if (typeof originalDebug === 'undefined') {
      delete globalThis.__WAVEGUIDE_DEBUG__;
    } else {
      globalThis.__WAVEGUIDE_DEBUG__ = originalDebug;
    }
  }

  assert.deepEqual(logMessages, []);
  assert.ok(publishedPayload);
  validateCanonicalMeshPayload(publishedPayload);
  assert.equal(publishedPayload.metadata.source, 'hornlab_mesher_contract_placeholder');
  assert.deepEqual(publishedPayload.vertices, [0, 0, 0, 1, 0, 0, 0, 1, 0]);
  assert.deepEqual(publishedPayload.indices, [0, 1, 2]);
  assert.deepEqual(publishedPayload.surfaceTags, [2]);
});

test('app mesh provider emits explicit simulation:mesh-error on contract publish failure', () => {
  let errorMessage = null;

  const app = {
    publishSimulationMesh() {
      throw new Error('intentional mesh publish failure');
    },
    publishSimulationMeshError(message) {
      errorMessage = message;
      return null;
    }
  };

  provideMeshForSimulation(app);

  assert.match(errorMessage, /intentional mesh publish failure/);
});
