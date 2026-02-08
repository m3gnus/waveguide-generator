import test from 'node:test';
import assert from 'node:assert/strict';

import {
  generateAbecSolvingFile,
  generateAbecObservationFile
} from '../src/export/abecProject.js';

test('ABEC solving file uses 3D when CircSym profile is off', () => {
  const text = generateAbecSolvingFile({
    abecF1: 100,
    abecF2: 2000,
    abecNumFreq: 20,
    abecSimProfile: -1,
    quadrants: '1234'
  });
  assert.match(text, /Dim=3D/);
});

test('ABEC solving file uses CircSym when profile is non-negative', () => {
  const text = generateAbecSolvingFile({
    abecF1: 100,
    abecF2: 2000,
    abecNumFreq: 20,
    abecSimProfile: 0,
    quadrants: '1234'
  });
  assert.match(text, /Dim=CircSym/);
});

test('ABEC observation file emits horizontal and vertical polar blocks', () => {
  const text = generateAbecObservationFile();
  assert.match(text, /GraphHeader="PM_SPL_H"/);
  assert.match(text, /GraphHeader="PM_SPL_V"/);
  assert.match(text, /ID=5001/);
  assert.match(text, /ID=5002/);
});
