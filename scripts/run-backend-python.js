import { spawnSync } from 'child_process';
import path from 'path';
import { fileURLToPath, pathToFileURL } from 'url';

import { resolveBackendPython } from './backend-python.js';

function parseArgs(argv) {
  const args = [...argv];
  let cwdRelative = null;
  if (args[0] === '--cwd') {
    cwdRelative = args[1] || null;
    args.splice(0, 2);
  }
  return { cwdRelative, pythonArgs: args };
}

export function runBackendPython({
  rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..'),
  args = process.argv.slice(2),
  env = process.env,
  resolveBackendPythonFn = resolveBackendPython,
  spawnSyncFn = spawnSync,
  stderr = process.stderr,
} = {}) {
  const { cwdRelative, pythonArgs } = parseArgs(args);
  if (pythonArgs.length === 0) {
    stderr.write('Usage: node scripts/run-backend-python.js [--cwd DIR] <python args...>\n');
    return 2;
  }

  const backendPython = resolveBackendPythonFn(rootDir, { env });
  const cwd = cwdRelative ? path.resolve(rootDir, cwdRelative) : rootDir;
  const child = spawnSyncFn(backendPython.python, pythonArgs, {
    cwd,
    stdio: 'inherit',
    env: {
      ...env,
      WG_BACKEND_PYTHON_SOURCE: backendPython.source,
    },
  });

  if (child.error) {
    stderr.write(`Backend Python command failed to start: ${child.error.message}\n`);
    return 1;
  }

  return Number.isInteger(child.status) ? child.status : 1;
}

const launchedDirectly = process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href;

if (launchedDirectly) {
  process.exit(runBackendPython());
}
