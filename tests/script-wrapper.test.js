import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'fs';
import path from 'path';

function readScript(name) {
  return fs.readFileSync(path.join(process.cwd(), 'scripts', name), 'utf8');
}

test('ath-compare uses shared geometry pipeline helpers', () => {
  const source = readScript('ath-compare.js');
  assert.match(source, /prepareGeometryParams/);
  assert.match(source, /buildGeometryArtifacts/);
  assert.doesNotMatch(source, /function prepareParamsForMesh/);
  assert.doesNotMatch(source, /function isNumericString/);
});

test('abec-compare uses shared geometry pipeline helpers', () => {
  const source = readScript('abec-compare.js');
  assert.match(source, /prepareGeometryParams/);
  assert.match(source, /buildGeometryArtifacts/);
  assert.doesNotMatch(source, /function prepareParamsForMesh/);
  assert.doesNotMatch(source, /function isNumericString/);
});
