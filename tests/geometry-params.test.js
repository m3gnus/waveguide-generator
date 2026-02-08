import test from 'node:test';
import assert from 'node:assert/strict';

import { getDefaults } from '../src/config/defaults.js';
import {
  prepareGeometryParams,
  coerceConfigParams,
  applyAthImportDefaults
} from '../src/geometry/index.js';

test('prepareGeometryParams parses values and applies normalization options', () => {
  const raw = {
    ...getDefaults('OSSE'),
    type: 'OSSE',
    L: '120',
    a: '30 + p',
    scale: '2',
    quadrants: '14',
    verticalOffset: 17,
    gcurveSf: '1,2,3,4'
  };

  const prepared = prepareGeometryParams(raw, {
    type: 'OSSE',
    forceFullQuadrants: true,
    applyVerticalOffset: false
  });

  assert.equal(prepared.L, 240);
  assert.equal(typeof prepared.a, 'function');
  assert.equal(prepared.a(0), 30);
  assert.equal(prepared.quadrants, '1234');
  assert.equal(prepared.verticalOffset, 0);
  assert.equal(prepared.gcurveSf, '1,2,3,4');
  assert.equal(prepared.type, 'OSSE');
});

test('coerceConfigParams and applyAthImportDefaults preserve ATH compatibility defaults', () => {
  const typed = coerceConfigParams({
    L: '120',
    label: 'expr + p'
  });

  assert.equal(typed.L, 120);
  assert.equal(typed.label, 'expr + p');

  const parsed = { type: 'OSSE', blocks: {} };
  applyAthImportDefaults(parsed, typed);

  assert.equal(typed.morphTarget, 0);
  assert.equal(typed.quadrants, '14');
  assert.equal(typed.encDepth, 0);
  assert.equal(typed.k, 1);
  assert.equal(typed.h, 0);
});
