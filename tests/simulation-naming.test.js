import test from 'node:test';
import assert from 'node:assert/strict';

import {
  ensureDatedSolveLabel,
  formatSolveDatePrefix,
  hasSolveDatePrefix,
  resolveAvailableSolveCounter,
  resolveDatedSolveLabel,
} from '../src/modules/simulation/naming.js';

test('simulation naming prepends YYMMDD before solve name and counter', () => {
  assert.equal(formatSolveDatePrefix('2026-06-05T12:00:00.000Z'), '260605');
  assert.equal(
    resolveDatedSolveLabel({
      outputName: 'horn',
      counter: 12,
      timestamp: '2026-06-05T12:00:00.000Z',
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

test('simulation naming chooses the next available counter for an existing solve name', () => {
  assert.equal(
    resolveAvailableSolveCounter({
      outputName: 'horn',
      counter: 1,
      existingJobs: [
        { label: '260605_horn_1' },
        { label: '260605_horn_2' },
        { script: { outputName: 'horn', counter: 3 } },
        { label: '260605_other_4' },
      ],
    }),
    4
  );
});

test('simulation naming does not treat similar prefixes as the same solve name', () => {
  assert.equal(
    resolveAvailableSolveCounter({
      outputName: 'horn',
      counter: 1,
      existingJobs: [{ label: '260605_horn_large_1' }],
    }),
    1
  );
});
