import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';

import { resolveBackendPython } from '../scripts/backend-python.js';

function createExistsSync(paths = []) {
  const known = new Set(paths);
  return (candidate) => known.has(candidate);
}

test('resolveBackendPython honors explicit env override first', () => {
  const rootDir = '/repo';
  const resolved = resolveBackendPython(rootDir, {
    env: {
      PYTHON_BIN: '/custom/python',
      WG_BACKEND_PYTHON: '/ignored/python'
    },
    existsSync: createExistsSync()
  });

  assert.equal(resolved.python, '/custom/python');
  assert.equal(resolved.source, 'env:PYTHON_BIN');
});

test('resolveBackendPython prefers project marker over fallback interpreters', () => {
  const rootDir = '/repo';
  const markerPath = path.join(rootDir, '.waveguide', 'backend-python.path');
  const markerPython = '/opt/verified/python';
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');
  const openclPython = '/home/user/.waveguide-generator/opencl-cpu-env/bin/python';

  const resolved = resolveBackendPython(rootDir, {
    env: {},
    homeDir: '/home/user',
    existsSync: createExistsSync([markerPath, markerPython, venvPython, openclPython]),
    readFileSync(candidate) {
      assert.equal(candidate, markerPath);
      return `${markerPython}\n`;
    }
  });

  assert.equal(resolved.python, markerPython);
  assert.match(resolved.source, /^marker:/);
});

test('resolveBackendPython falls back to .venv when marker is missing', () => {
  const rootDir = '/repo';
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');

  const resolved = resolveBackendPython(rootDir, {
    env: {},
    existsSync: createExistsSync([venvPython])
  });

  assert.equal(resolved.python, venvPython);
  assert.equal(resolved.source, 'fallback:.venv');
});

test('resolveBackendPython falls back to python3 when no managed interpreter exists', () => {
  const resolved = resolveBackendPython('/repo', {
    env: {},
    existsSync: createExistsSync()
  });

  assert.equal(resolved.python, 'python3');
  assert.equal(resolved.source, 'fallback:python3');
});
