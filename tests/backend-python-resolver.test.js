import test from 'node:test';
import assert from 'node:assert/strict';
import path from 'node:path';
import fs from 'node:fs';

import { resolveBackendPython } from '../scripts/backend-python.js';
import { runBackendPython } from '../scripts/run-backend-python.js';

function createExistsSync(paths = []) {
  const known = new Set(paths);
  return (candidate) => known.has(candidate);
}

test('resolveBackendPython honors explicit env override first', () => {
  const rootDir = '/repo';
  const resolved = resolveBackendPython(rootDir, {
    env: {
      PYTHON_BIN: '/custom/python',
      WG_BACKEND_PYTHON: '/ignored/python',
    },
    existsSync: createExistsSync(),
  });

  assert.equal(resolved.python, '/custom/python');
  assert.equal(resolved.source, 'env:PYTHON_BIN');
});

test('resolveBackendPython prefers project marker over fallback interpreters', () => {
  const rootDir = '/repo';
  const markerPath = path.join(rootDir, '.waveguide', 'backend-python.path');
  const markerPython = '/opt/verified/python';
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');

  const resolved = resolveBackendPython(rootDir, {
    env: {},
    homeDir: '/home/user',
    existsSync: createExistsSync([markerPath, markerPython, venvPython]),
    readFileSync(candidate) {
      assert.equal(candidate, markerPath);
      return `${markerPython}\n`;
    },
  });

  assert.equal(resolved.python, markerPython);
  assert.match(resolved.source, /^marker:/);
});

test('resolveBackendPython falls back to .venv when marker is missing', () => {
  const rootDir = '/repo';
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');

  const resolved = resolveBackendPython(rootDir, {
    env: {},
    existsSync: createExistsSync([venvPython]),
    spawnSyncFn() {
      return {
        status: 0,
        stdout: JSON.stringify({ summary: { requiredReady: true } }),
      };
    },
  });

  assert.equal(resolved.python, venvPython);
  assert.equal(resolved.source, 'fallback:.venv');
});

test('resolveBackendPython prefers the first runtime-ready fallback interpreter', () => {
  const rootDir = '/repo';
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');

  const resolved = resolveBackendPython(rootDir, {
    env: {},
    homeDir: '/home/user',
    existsSync: createExistsSync([venvPython]),
    spawnSyncFn(python) {
      return {
        status: 0,
        stdout: JSON.stringify({
          summary: {
            requiredReady: python === 'python3',
          },
        }),
      };
    },
  });

  assert.equal(resolved.python, 'python3');
  assert.equal(resolved.source, 'fallback:python3');
});

test('resolveBackendPython keeps the original fallback order when no candidate is runtime-ready', () => {
  const rootDir = '/repo';
  const venvPython = path.join(rootDir, '.venv', 'bin', 'python');

  const resolved = resolveBackendPython(rootDir, {
    env: {},
    homeDir: '/home/user',
    existsSync: createExistsSync([venvPython]),
    spawnSyncFn() {
      return {
        status: 0,
        stdout: JSON.stringify({ summary: { requiredReady: false } }),
      };
    },
  });

  assert.equal(resolved.python, venvPython);
  assert.equal(resolved.source, 'fallback:.venv');
});

test('resolveBackendPython falls back to python3 when no managed interpreter exists', () => {
  const resolved = resolveBackendPython('/repo', {
    env: {},
    existsSync: createExistsSync(),
    spawnSyncFn() {
      return {
        status: 0,
        stdout: JSON.stringify({ summary: { requiredReady: false } }),
      };
    },
  });

  assert.equal(resolved.python, 'python3');
  assert.equal(resolved.source, 'fallback:python3');
});

test('runBackendPython forwards commands through resolved backend interpreter', () => {
  const rootDir = '/repo';
  const markerPath = path.join(rootDir, '.waveguide', 'backend-python.path');
  const markerPython = '/repo/.venv/bin/python';
  let spawnCall = null;

  const exitCode = runBackendPython({
    rootDir,
    args: ['--cwd', 'server', '-m', 'unittest', 'discover', '-s', 'tests'],
    env: {},
    resolveBackendPythonFn(resolvedRoot, { env }) {
      assert.equal(resolvedRoot, rootDir);
      assert.deepEqual(env, {});
      return {
        python: markerPython,
        source: `marker:${markerPath}`,
      };
    },
    spawnSyncFn(python, args, options) {
      spawnCall = { python, args, options };
      return { status: 0 };
    },
    stderr: { write() {} },
  });

  assert.equal(exitCode, 0);
  assert.equal(spawnCall.python, markerPython);
  assert.deepEqual(spawnCall.args, ['-m', 'unittest', 'discover', '-s', 'tests']);
  assert.equal(spawnCall.options.cwd, path.join(rootDir, 'server'));
  assert.match(spawnCall.options.env.WG_BACKEND_PYTHON_SOURCE, /^marker:/);
});

test('backend npm scripts use the shared backend Python runner', () => {
  const packageJson = JSON.parse(
    fs.readFileSync(new URL('../package.json', import.meta.url), 'utf8')
  );

  for (const scriptName of [
    'start:backend',
    'test:server',
    'diag:mesher:reference-horn',
    'diag:mesher:closed',
  ]) {
    const script = packageJson.scripts[scriptName];
    assert.match(script, /node scripts\/run-backend-python\.js/);
    assert.doesNotMatch(script, /\bpython3\b/);
  }
});
