import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

function read(path) {
  return fs.readFileSync(new URL(path, import.meta.url), 'utf8');
}

test('startup scripts and backend entrypoint avoid mock-runtime messaging', () => {
  const startAll = read('../scripts/start-all.js');
  const serverStart = read('../server/start.sh');
  const appPy = read('../server/app.py');

  assert.doesNotMatch(startAll, /mock data/i);
  assert.doesNotMatch(serverStart, /mock solver/i);
  assert.doesNotMatch(appPy, /enable simulations/i);

  assert.match(startAll, /backend-dependent features are blocked/i);
  assert.match(serverStart, /\/api\/solve will stay unavailable/i);
  assert.match(appPy, /\/api\/solve is unavailable/i);
});
