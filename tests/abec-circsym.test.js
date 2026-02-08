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

test('ABEC observation file can be generated from imported ABEC.Polars blocks', () => {
  const text = generateAbecObservationFile({
    polarBlocks: {
      'ABEC.Polars:45': {
        _items: {
          MapAngleRange: '-180,180,96',
          Distance: '2',
          Offset: '140',
          NormAngle: '20',
          Inclination: '45'
        }
      },
      'ABEC.Polars:hor': {
        _items: {
          MapAngleRange: '-180,180,96',
          Distance: '1',
          Offset: '140',
          NormAngle: '20',
          Inclination: '0'
        }
      }
    }
  });

  assert.match(text, /GraphHeader="PM_45"/);
  assert.match(text, /GraphHeader="PM_hor"/);
  assert.match(text, /Offset=140mm/);
  assert.match(text, /NormalizingAngle=20/);
  assert.match(text, /Inclination=45/);
});

test('ABEC solving file uses explicit interface metadata when provided', () => {
  const text = generateAbecSolvingFile(
    {
      abecF1: 100,
      abecF2: 2000,
      abecNumFreq: 20,
      abecSimProfile: -1,
      quadrants: '1234',
      encDepth: 0,
      interfaceOffset: ''
    },
    { interfaceEnabled: true }
  );

  assert.match(text, /SubDomain=1; ElType=Interior/);
  assert.match(text, /Elements \"SD2G0\"/);
  assert.match(text, /Elements \"I1-2\"/);
});
