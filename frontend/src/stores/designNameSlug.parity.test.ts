import { describe, expect, it } from 'vitest';
import { designNameSlug } from './designName';

/**
 * The archive folder rule exists twice: here and in
 * `server/workspace/archive.py::archive_folder_slug`, because the browser names
 * the folder it writes into while the server names the same folder when it
 * files a captured CAD document there. This table is asserted identically by
 * `server/tests/test_run_archive.py`; change both or neither.
 */
export const SLUG_PARITY_TABLE: Array<[string, string]> = [
  ['Big Horn', 'Big_Horn'],
  ["Björn's Horn", 'Bjorn_s_Horn'],
  ['  ..weird__name..  ', 'weird_name'],
  ['ÅÄÖ 12', 'AAO_12'],
  ['R-OSSE 40x30', 'R-OSSE_40x30'],
];

describe('archive folder slug parity', () => {
  it('matches the server rule for every name in the shared table', () => {
    for (const [name, expected] of SLUG_PARITY_TABLE) {
      expect(designNameSlug(name, 'design')).toBe(expected);
    }
  });

  it('falls back rather than producing an empty folder name', () => {
    expect(designNameSlug('', 'design')).toBe('design');
    expect(designNameSlug('///', 'design')).toBe('design');
  });
});
