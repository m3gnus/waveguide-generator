import test from 'node:test';
import assert from 'node:assert/strict';

import { normalizeParamInput } from '../src/ui/paramInput.js';
import { validateSimulationConfig } from '../src/ui/simulation/actions.js';
import { applyExportSelection } from '../src/ui/simulation/exports.js';
import {
  deriveExportFieldsFromFileName,
  markParametersChanged,
  resetParameterChangeTracking
} from '../src/ui/fileOps.js';

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

test('applyExportSelection includes CAD exports options 8 and 9', () => {
  const calls = [];
  const handlers = {
    '8': () => calls.push('stl'),
    '9': () => calls.push('fusion-csv')
  };

  assert.equal(applyExportSelection({}, '8', handlers), true);
  assert.equal(applyExportSelection({}, '9', handlers), true);
  assert.deepEqual(calls, ['stl', 'fusion-csv']);
});

test('deriveExportFieldsFromFileName parses output name and counter from file names', () => {
  assert.deepEqual(
    deriveExportFieldsFromFileName('horn.cfg'),
    { outputName: 'horn', counter: 1 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('horn_design_12.cfg'),
    { outputName: 'horn_design', counter: 12 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('horn_design_0.cfg'),
    { outputName: 'horn_design_0', counter: 1 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('my file name_3.txt'),
    { outputName: 'my file name', counter: 3 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('260219superhorn35.cfg'),
    { outputName: '260219superhorn', counter: 35 }
  );
  assert.deepEqual(
    deriveExportFieldsFromFileName('   '),
    { outputName: 'horn_design', counter: 1 }
  );
});

test('markParametersChanged increments counter once per change cycle and skips import baseline update', () => {
  const originalDocument = global.document;
  const counterEl = { value: '35' };
  global.document = {
    getElementById(id) {
      if (id === 'export-counter') return counterEl;
      return null;
    }
  };

  try {
    resetParameterChangeTracking({ skipNext: true });
    markParametersChanged();
    assert.equal(counterEl.value, '35');

    markParametersChanged();
    assert.equal(counterEl.value, '36');

    markParametersChanged();
    assert.equal(counterEl.value, '36');

    resetParameterChangeTracking();
    markParametersChanged();
    assert.equal(counterEl.value, '37');
  } finally {
    global.document = originalDocument;
    resetParameterChangeTracking();
  }
});
