import test from 'node:test';
import assert from 'node:assert/strict';

import { runBackendRuntimeDoctor } from '../scripts/doctor-backend-runtime.js';

test('runBackendRuntimeDoctor prepends --doctor and forwards args', () => {
  const calls = [];
  const exitCode = runBackendRuntimeDoctor({
    args: ['--json', '--strict'],
    runBackendRuntimePreflightFn(input) {
      calls.push(input);
      return 0;
    }
  });

  assert.equal(exitCode, 0);
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].args, ['--doctor', '--json', '--strict']);
});

test('runBackendRuntimeDoctor returns wrapped preflight exit code', () => {
  const exitCode = runBackendRuntimeDoctor({
    runBackendRuntimePreflightFn() {
      return 1;
    }
  });

  assert.equal(exitCode, 1);
});
