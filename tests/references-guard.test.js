import test from 'node:test';
import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';

test('_references files are never tracked in git', () => {
  const output = execSync('git ls-files', { encoding: 'utf8' });
  const tracked = output
    .split(/\r?\n/)
    .filter(Boolean)
    .filter((line) => line.startsWith('_references/'));

  assert.deepEqual(
    tracked,
    [],
    `Tracked reference files detected under _references/: ${tracked.join(', ')}`
  );
});
