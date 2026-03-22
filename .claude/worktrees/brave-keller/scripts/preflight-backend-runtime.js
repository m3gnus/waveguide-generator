import { spawnSync } from 'child_process';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

import { resolveBackendPython } from './backend-python.js';

export function runBackendRuntimePreflight({
  rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'),
  args = process.argv.slice(2),
  env = process.env,
  resolveBackendPythonFn = resolveBackendPython,
  spawnSyncFn = spawnSync,
  stderr = process.stderr
} = {}) {
  const backendPython = resolveBackendPythonFn(rootDir, { env });
  const runtimePreflightScript = path.join(rootDir, 'server', 'scripts', 'runtime_preflight.py');

  const child = spawnSyncFn(
    backendPython.python,
    [runtimePreflightScript, ...args],
    {
      cwd: rootDir,
      stdio: 'inherit',
      env: {
        ...env,
        WG_BACKEND_PYTHON_SOURCE: backendPython.source
      }
    }
  );

  if (child.error) {
    stderr.write(`Backend preflight failed to start: ${child.error.message}\n`);
    return 1;
  }

  return Number.isInteger(child.status) ? child.status : 1;
}

const launchedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;

if (launchedDirectly) {
  process.exit(runBackendRuntimePreflight());
}

