import { describe, expect, it } from 'vitest';
import {
  driverCountText,
  driverEmptyState,
  driverKindCounts,
  driverKindLabel,
  driverKindTotal,
  driverLibraryHoldingText,
  openingSearchKind,
  type DriverKindTally,
} from './driverLibraryCounts';
import type { DriverLibraryInfo } from '../api/drivers';

const info = (kinds: DriverLibraryInfo['kinds'], rest: Partial<DriverLibraryInfo> = {}): DriverLibraryInfo => ({
  folder: '/library',
  files: [{ name: 'hornlab-drivers.csv', rows: 1249, bundled: true }],
  total_drivers: 1046,
  complete_drivers: 1046,
  kinds,
  ...rest,
});

/** What actually ships: 1,045 cone drivers and exactly one compression driver. */
const SHIPPED: DriverKindTally = { lf: 1045, cd: 1, unknown: 0, total: 1046, known: true };

describe('driverKindCounts', () => {
  it('counts what the picker can offer, which is the complete column', () => {
    // A catalogue row indexes but cannot drive a channel, and the picker always
    // searches with complete=true -- so counting it would promise a driver the
    // very next search withholds.
    const counts = driverKindCounts(info([
      { kind: 'lf', total: 1200, complete: 1045 },
      { kind: 'cd', total: 40, complete: 1 },
    ]));
    expect(counts).toEqual(SHIPPED);
  });

  it('carries a type it has no name for into the total but not into a bucket', () => {
    const counts = driverKindCounts(info([
      { kind: 'lf', total: 2, complete: 2 },
      { kind: 'planar', total: 3, complete: 3 },
    ]));
    // "All" will offer five drivers, so the total says five.
    expect(counts.total).toBe(5);
    expect(counts.lf).toBe(2);
    expect(counts.cd).toBe(0);
  });

  it('reports the breakdown as unknown against a server that does not send one', () => {
    const counts = driverKindCounts(info(undefined, { complete_drivers: 900 }));
    expect(counts.known).toBe(false);
    // The total is still real, so "browse all 900" stays truthful; the split is
    // simply absent rather than invented.
    expect(counts.total).toBe(900);
    expect(counts.lf).toBe(0);
  });

  it('treats no library at all as empty rather than throwing', () => {
    expect(driverKindCounts(null)).toEqual({ lf: 0, cd: 0, unknown: 0, total: 0, known: false });
  });

  it('falls back to the indexed total when the complete count is missing too', () => {
    expect(driverKindCounts(info(undefined, { complete_drivers: undefined })).total).toBe(1046);
  });
});

describe('driverKindTotal and labels', () => {
  it('gives the filter its own count, with all meaning the whole library', () => {
    expect(driverKindTotal(SHIPPED, 'lf')).toBe(1045);
    expect(driverKindTotal(SHIPPED, 'cd')).toBe(1);
    expect(driverKindTotal(SHIPPED, 'all')).toBe(1046);
  });

  it('names the types the way the toggle does', () => {
    expect(driverKindLabel('lf')).toBe('Cone');
    expect(driverKindLabel('cd')).toBe('Compression');
    expect(driverKindLabel('all')).toBe('All');
  });

  it('agrees with itself on singular and plural, and groups the thousands', () => {
    expect(driverCountText(1, 'cd')).toBe('1 compression driver');
    expect(driverCountText(1045, 'lf')).toBe('1,045 cone drivers');
    expect(driverCountText(1046)).toBe('1,046 drivers');
  });
});

describe('driverLibraryHoldingText', () => {
  it('leads with the total and then breaks it down', () => {
    expect(driverLibraryHoldingText(SHIPPED))
      .toBe('The library has 1,046 drivers — 1,045 cone and 1 compression.');
  });

  it('drops the breakdown when there is only one type to break down', () => {
    expect(driverLibraryHoldingText({ lf: 12, cd: 0, unknown: 0, total: 12, known: true }))
      .toBe('The library has 12 drivers.');
  });

  it('says nothing it cannot back up when the server sent no breakdown', () => {
    expect(driverLibraryHoldingText({ lf: 0, cd: 0, unknown: 0, total: 900, known: false }))
      .toBe('The library has 900 drivers.');
  });

  it('states an empty library as empty', () => {
    expect(driverLibraryHoldingText({ lf: 0, cd: 0, unknown: 0, total: 0, known: false }))
      .toBe('The library is empty.');
  });
});

describe('driverEmptyState', () => {
  const base = { counts: SHIPPED, matchesByKind: {}, hiddenIncomplete: 0 };

  it('names the type that would have answered, and offers it in one press', () => {
    // The reported failure: a horn designer on the compression half searches a
    // driver the library has, on the cone side, and is told "no matches".
    const state = driverEmptyState({
      ...base,
      query: 'B&C 15',
      kind: 'cd',
      matchesByKind: { lf: 7, cd: 0 },
    });
    expect(state.title).toBe('No compression driver matches “B&C 15”');
    expect(state.detail).toBe('7 cone drivers do. The library has 1,046 drivers — 1,045 cone and 1 compression.');
    expect(state.action).toEqual({ label: 'Show all 7', kind: 'all', clearQuery: false });
  });

  it('says what exists and where the rest are when nothing matches anywhere', () => {
    const state = driverEmptyState({ ...base, query: 'DE250', kind: 'cd', matchesByKind: { lf: 0, cd: 0 } });
    expect(state.title).toBe('No compression driver matches “DE250”');
    // The three questions the old "No driver matches that search." answered
    // none of: what was searched, what exists, what to press.
    expect(state.detail).toBe('The library has 1,046 drivers — 1,045 cone and 1 compression. No brand or model contains that.');
    expect(state.action).toEqual({ label: 'Search all 1,046 drivers', kind: 'all', clearQuery: false });
  });

  it('offers a way back to browsing once the whole library has been searched', () => {
    const state = driverEmptyState({ ...base, query: 'compression', kind: 'all', matchesByKind: { lf: 0, cd: 0 } });
    expect(state.title).toBe('Nothing in the library matches “compression”');
    // Widening the type cannot help here -- it is already all of it -- so the
    // escape empties the box instead of moving the filter.
    expect(state.action).toEqual({ label: 'Browse all 1,046 drivers', kind: 'all', clearQuery: true });
  });

  it('separates "the library does not know it" from "it cannot drive it"', () => {
    const state = driverEmptyState({ ...base, query: 'DE250', kind: 'cd', hiddenIncomplete: 2 });
    expect(state.title).toBe('All 2 matches for “DE250” lack Thiele-Small data');
    expect(state.detail).toContain('no moving mass or compliance');
    expect(state.detail).toContain('cannot drive a channel');
    // Still says what the library holds, so the count is never left implied.
    expect(state.detail).toContain('1,046 drivers');
  });

  it('reads singular when exactly one match was withheld', () => {
    const state = driverEmptyState({ ...base, query: 'radian', kind: 'cd', hiddenIncomplete: 1 });
    expect(state.title).toBe('The one match for “radian” lacks Thiele-Small data');
    expect(state.detail).toContain('lists it but');
  });

  it('answers an untouched search on a type the library has none of', () => {
    const state = driverEmptyState({
      ...base,
      query: '',
      kind: 'cd',
      counts: { lf: 1045, cd: 0, unknown: 0, total: 1045, known: true },
      matchesByKind: { lf: 1045, cd: 0 },
    });
    expect(state.title).toBe('The library has no compression drivers');
    expect(state.action).toEqual({ label: 'Show all 1,045', kind: 'all', clearQuery: false });
  });

  it('does not pretend an empty library is a filtering problem', () => {
    const empty = { lf: 0, cd: 0, unknown: 0, total: 0, known: false };
    const state = driverEmptyState({ ...base, query: '', kind: 'all', counts: empty });
    expect(state.title).toBe('The library is empty');
    // Nothing to widen to, so hand entry -- always the last row -- is the way on.
    expect(state.action).toBeNull();
  });

  it('never leaves a queried dead end without an escape while a library exists', () => {
    for (const kind of ['lf', 'cd', 'all'] as const) {
      const state = driverEmptyState({ ...base, query: 'nothing at all', kind });
      expect(state.action).not.toBeNull();
      expect(state.title).toContain('“nothing at all”');
      expect(state.detail).toContain('1,046');
    }
  });
});

describe('openingSearchKind', () => {
  it('keeps the channel’s own type when the library can answer inside it', () => {
    expect(openingSearchKind('lf', SHIPPED)).toBe('lf');
  });

  it('opens on the whole library when the channel’s type holds one driver', () => {
    // A high-frequency channel prefers compression, and the shipped library has
    // one. Starting there means every query but that driver's own name comes
    // back empty -- which is exactly how a stocked library reads as broken.
    expect(openingSearchKind('cd', SHIPPED)).toBe('all');
  });

  it('opens on the whole library when the channel’s type holds none', () => {
    expect(openingSearchKind('cd', { lf: 40, cd: 0, unknown: 0, total: 40, known: true })).toBe('all');
  });

  it('leaves the preference alone when the counts are not known', () => {
    // An older server sends no breakdown, and guessing would be worse than
    // honouring the channel's own role.
    expect(openingSearchKind('cd', { lf: 0, cd: 0, unknown: 0, total: 900, known: false })).toBe('cd');
  });
});
