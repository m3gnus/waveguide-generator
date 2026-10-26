import { expect, it } from 'vitest';
import baseline from './fixtures/imported-identity-71fce3e63.json';
import { importedIdentityCases } from '../testing/importedIdentityCases';

it('matches the 71fce3e63 CAD and legacy file wire after explicit edits', async () => {
  const current = await importedIdentityCases();
  expect(current.cad.hasOpenedSetup).toBe(false);
  expect(current.file.hasOpenedSetup).toBe(false);
  expect(current.file.submission.geometry.drive_channels[0].motion).toBe('normal');
  expect(current.cad.sweep).toEqual([125, 8000, 29]);
  // Compare serialization too: wire field order and values must both survive.
  expect(JSON.stringify(current)).toBe(JSON.stringify(baseline));
});
