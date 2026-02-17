import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeParamInput } from '../src/ui/paramInput.js';
import { validateSimulationConfig } from '../src/ui/simulation/actions.js';
import { applyExportSelection } from '../src/ui/simulation/exports.js';

test('normalizeParamInput parses numeric literals consistently', () => {
  assert.equal(normalizeParamInput('1.0'), 1);
  assert.equal(normalizeParamInput(' 1e3 '), 1000);
  assert.equal(normalizeParamInput('-0.25'), -0.25);
  assert.equal(normalizeParamInput('45 + 10*cos(p)'), '45 + 10*cos(p)');
  assert.equal(normalizeParamInput('2+3'), '2+3');
});

test('validateSimulationConfig catches invalid ranges and counts', () => {
  assert.match(
    validateSimulationConfig({
      frequencyStart: 1000,
      frequencyEnd: 100,
      numFrequencies: 50
    }),
    /Start frequency/
  );

  assert.match(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 0
    }),
    /Number of frequencies/
  );

  assert.equal(
    validateSimulationConfig({
      frequencyStart: 100,
      frequencyEnd: 1000,
      numFrequencies: 50
    }),
    null
  );
});

test('applyExportSelection routes to expected handler', () => {
  const calls = [];
  const originalError = console.error;
  console.error = () => {};

  const handlers = {
    '1': () => calls.push('image'),
    '2': () => calls.push('csv'),
    '3': () => calls.push('json'),
    '4': () => calls.push('text')
  };

  try {
    assert.equal(applyExportSelection({}, '2', handlers), true);
    assert.deepEqual(calls, ['csv']);

    assert.equal(applyExportSelection({}, '9', handlers), false);
    assert.deepEqual(calls, ['csv']);
  } finally {
    console.error = originalError;
  }
});

test('applyExportSelection includes VACS spectrum option 7', () => {
  const calls = [];
  const handlers = {
    '1': () => calls.push('image'),
    '2': () => calls.push('csv'),
    '3': () => calls.push('json'),
    '4': () => calls.push('text'),
    '5': () => calls.push('polar'),
    '6': () => calls.push('impedance'),
    '7': () => calls.push('vacs')
  };

  assert.equal(applyExportSelection({}, '7', handlers), true);
  assert.deepEqual(calls, ['vacs']);
});
