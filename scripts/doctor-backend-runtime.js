import { pathToFileURL } from 'url';

import { runBackendRuntimePreflight } from './preflight-backend-runtime.js';

export function runBackendRuntimeDoctor({
  args = process.argv.slice(2),
  runBackendRuntimePreflightFn = runBackendRuntimePreflight,
  ...rest
} = {}) {
  return runBackendRuntimePreflightFn({
    ...rest,
    args: ['--doctor', ...args],
  });
}

const launchedDirectly = process.argv[1]
  && import.meta.url === pathToFileURL(process.argv[1]).href;

if (launchedDirectly) {
  process.exit(runBackendRuntimeDoctor());
}
