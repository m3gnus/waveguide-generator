import test from 'node:test';
import assert from 'node:assert/strict';

import { runBackendRuntimePreflight } from '../scripts/preflight-backend-runtime.js';

test('runBackendRuntimePreflight invokes selected interpreter with preflight script', () => {
  const spawnCalls = [];
  const exitCode = runBackendRuntimePreflight({
    rootDir: '/repo',
    args: ['--strict'],
    env: {},
    resolveBackendPythonFn() {
      return { python: '/repo/.venv/bin/python', source: 'fallback:.venv' };
    },
    spawnSyncFn(python, pythonArgs, options) {
      spawnCalls.push({ python, pythonArgs, options });
      return { status: 0 };
    }
  });

  assert.equal(exitCode, 0);
  assert.equal(spawnCalls.length, 1);
  assert.equal(spawnCalls[0].python, '/repo/.venv/bin/python');
  assert.deepEqual(
    spawnCalls[0].pythonArgs,
    ['/repo/server/scripts/runtime_preflight.py', '--strict']
  );
  assert.equal(
    spawnCalls[0].options.env.WG_BACKEND_PYTHON_SOURCE,
    'fallback:.venv'
  );
});

test('runBackendRuntimePreflight returns non-zero when subprocess fails to start', () => {
  const stderrWrites = [];
  const exitCode = runBackendRuntimePreflight({
    rootDir: '/repo',
    env: {},
    resolveBackendPythonFn() {
      return { python: '/missing/python', source: 'env:PYTHON_BIN' };
    },
    spawnSyncFn() {
      return { error: new Error('spawn ENOENT'), status: null };
    },
    stderr: {
      write(message) {
        stderrWrites.push(String(message));
      }
    }
  });

  assert.equal(exitCode, 1);
  assert.equal(stderrWrites.some((line) => line.includes('Backend preflight failed to start')), true);
});

