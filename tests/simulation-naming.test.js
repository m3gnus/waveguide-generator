import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ensureDatedSolveLabel,
  formatSolveDatePrefix,
  hasSolveDatePrefix,
  resolveDatedSolveLabel
} from '../src/modules/simulation/naming.js';

test('simulation naming prepends YYMMDD before solve name and counter', () => {
  assert.equal(formatSolveDatePrefix('2026-06-05T12:00:00.000Z'), '260605');
  assert.equal(
    resolveDatedSolveLabel({
      outputName: 'horn',
      counter: 12,
      timestamp: '2026-06-05T12:00:00.000Z'
    }),
    '260605_horn_12'
  );
});

test('simulation naming does not duplicate an existing date prefix', () => {
  assert.equal(hasSolveDatePrefix('260605_horn_12'), true);
  assert.equal(
    ensureDatedSolveLabel('260605_horn_12', '2026-06-06T12:00:00.000Z'),
    '260605_horn_12'
  );
});
