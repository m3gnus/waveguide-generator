/**
 * What the driver library holds, and what to say when a search finds none of it.
 *
 * The shipped library is 1,045 cone drivers and exactly one compression driver.
 * That is a fact about the world's datasheets, not a fault -- but a horn
 * designer whose search starts on the compression half types a driver name,
 * gets nothing back, and concludes the database is broken or empty. It has
 * happened to a real user.
 *
 * So nothing here invents drivers. It makes the shape of the library legible
 * before a search (the type filter carries its own counts) and turns every
 * dead end into a sentence that names what was searched, what exists, and the
 * one click out. The logic is pure so it can be tested without a picker.
 */

import type { DriverKind, DriverLibraryInfo, DriverSearchKind } from '../api/drivers';

/** The types a driver can be, in the order the filter lists them. */
export const DRIVER_KINDS: readonly DriverKind[] = ['lf', 'cd', 'unknown'];

export interface DriverKindTally {
  lf: number;
  cd: number;
  unknown: number;
  /** The three added up: every driver the picker can offer. */
  total: number;
  /**
   * Whether the breakdown is real. A server older than the `kinds` field sends
   * a total and nothing else, and a made-up split would be worse than none --
   * so the filter drops its counts rather than guessing at them.
   */
  known: boolean;
}

const EMPTY_TALLY: DriverKindTally = { lf: 0, cd: 0, unknown: 0, total: 0, known: false };

/**
 * The library's breakdown by driver type, counted the way the picker searches.
 *
 * `complete` and not `total`: the picker asks for `complete=true`, so a
 * catalogue row with no motor data is never offered and counting it would
 * promise drivers the search then withholds.
 */
export function driverKindCounts(info: DriverLibraryInfo | null | undefined): DriverKindTally {
  if (!info) return EMPTY_TALLY;
  if (!Array.isArray(info.kinds) || info.kinds.length === 0) {
    const total = info.complete_drivers ?? info.total_drivers ?? 0;
    return { ...EMPTY_TALLY, total };
  }
  const tally = { lf: 0, cd: 0, unknown: 0, total: 0, known: true };
  for (const entry of info.kinds) {
    const count = entry.complete ?? entry.total ?? 0;
    if (!Number.isFinite(count) || count <= 0) continue;
    tally.total += count;
    // A type this build has no name for still counts toward the total: it is a
    // driver the "all" filter will offer, and a total that omits it would be a
    // promise the search over-delivers on.
    if (entry.kind === 'lf' || entry.kind === 'cd' || entry.kind === 'unknown') {
      tally[entry.kind] += count;
    }
  }
  return tally;
}

/** How many drivers a filter setting can offer. */
export function driverKindTotal(counts: DriverKindTally, kind: DriverSearchKind): number {
  return kind === 'all' ? counts.total : counts[kind];
}

/** The filter button's label. */
export function driverKindLabel(kind: DriverSearchKind): string {
  return kind === 'lf' ? 'Cone' : kind === 'cd' ? 'Compression' : kind === 'unknown' ? 'Other' : 'All';
}

/** The same word inside a sentence, where it is an adjective on "driver". */
export function driverKindWord(kind: DriverSearchKind): string {
  return kind === 'lf' ? 'cone' : kind === 'cd' ? 'compression' : kind === 'unknown' ? 'unclassified' : '';
}

export function driverCountText(count: number, kind: DriverSearchKind = 'all'): string {
  const word = driverKindWord(kind);
  const noun = count === 1 ? 'driver' : 'drivers';
  return `${count.toLocaleString()} ${word ? `${word} ` : ''}${noun}`;
}

/**
 * One sentence naming everything the library holds.
 *
 * This is the line that answers "is the database empty?", so it leads with the
 * total and then breaks it down -- and it says the breakdown only when the
 * server actually sent one.
 */
export function driverLibraryHoldingText(counts: DriverKindTally): string {
  if (counts.total === 0) return 'The library is empty.';
  const parts = DRIVER_KINDS
    .filter((kind) => counts[kind] > 0)
    .map((kind) => `${counts[kind].toLocaleString()} ${driverKindWord(kind)}`);
  const total = `The library has ${driverCountText(counts.total)}`;
  return parts.length > 1 ? `${total} — ${joinWords(parts)}.` : `${total}.`;
}

function joinWords(parts: string[]): string {
  if (parts.length <= 1) return parts.join('');
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`;
}

/** The types the query matched that the current filter is hiding. */
function elsewhereMatches(
  kind: DriverSearchKind,
  matchesByKind: Partial<Record<DriverKind, number>>,
): { total: number; text: string } {
  const others = DRIVER_KINDS
    .filter((other) => other !== kind && (matchesByKind[other] ?? 0) > 0)
    .map((other) => ({ kind: other, count: matchesByKind[other] ?? 0 }));
  if (kind === 'all' || others.length === 0) return { total: 0, text: '' };
  return {
    total: others.reduce((sum, entry) => sum + entry.count, 0),
    text: joinWords(others.map((entry) => driverCountText(entry.count, entry.kind))),
  };
}

export interface DriverEmptyAction {
  label: string;
  /** What the filter becomes when it is pressed. */
  kind: DriverSearchKind;
  /** Whether it also empties the search box. */
  clearQuery: boolean;
}

export interface DriverEmptyState {
  title: string;
  detail: string;
  /** Absent when hand entry, which is always the last row, is the only way on. */
  action: DriverEmptyAction | null;
}

export interface DriverEmptyStateInput {
  query: string;
  kind: DriverSearchKind;
  counts: DriverKindTally;
  matchesByKind: Partial<Record<DriverKind, number>>;
  /** Matches the library holds but has no Thiele-Small data for. */
  hiddenIncomplete: number;
}

/**
 * What to say when the search offers nothing.
 *
 * Every branch answers the same three questions -- what was searched, what the
 * library actually holds, and what to press next -- because the failure this
 * replaces answered none of them. "No driver matches that search" is true of a
 * library with a thousand drivers in it and of one with none, and the user has
 * no way to tell which they are looking at.
 */
export function driverEmptyState(
  { query, kind, counts, matchesByKind, hiddenIncomplete }: DriverEmptyStateInput,
): DriverEmptyState {
  const q = query.trim();
  const holding = driverLibraryHoldingText(counts);
  const elsewhere = elsewhereMatches(kind, matchesByKind);
  const browseAll: DriverEmptyAction | null = kind === 'all' || counts.total === 0 ? null : {
    label: `Search all ${driverCountText(counts.total)}`,
    kind: 'all',
    clearQuery: false,
  };
  const showElsewhere: DriverEmptyAction | null = elsewhere.total === 0 ? null : {
    label: `Show ${elsewhere.total === 1 ? 'it' : `all ${elsewhere.total.toLocaleString()}`}`,
    kind: 'all',
    clearQuery: false,
  };

  // The library knows these drivers and cannot drive them, which is a different
  // answer from not knowing them -- and the one a compression-driver search
  // over a catalogue CSV usually deserves.
  if (hiddenIncomplete > 0) {
    const one = hiddenIncomplete === 1;
    const subject = one
      ? { noun: 'The one match', here: 'The one driver', verb: 'lacks' }
      : { noun: `All ${hiddenIncomplete.toLocaleString()} matches`, here: `All ${hiddenIncomplete.toLocaleString()} drivers`, verb: 'lack' };
    return {
      title: q
        ? `${subject.noun} for “${q}” ${subject.verb} Thiele-Small data`
        : `${subject.here} here ${subject.verb} Thiele-Small data`,
      detail: `The library lists ${one ? 'it' : 'them'} but publishes no moving mass or compliance, so ${one ? 'it' : 'they'} cannot drive a channel. ${holding}`,
      action: showElsewhere ?? browseAll,
    };
  }

  // The filter is hiding the answer. This is the shipped library's own trap: a
  // high-frequency channel starts on the compression half, which holds one
  // driver, so almost every query lands here.
  if (elsewhere.total > 0) {
    return {
      title: q
        ? `No ${driverKindWord(kind)} driver matches “${q}”`
        : `The library has no ${driverKindWord(kind)} drivers`,
      detail: q
        ? `${elsewhere.text} ${elsewhere.total === 1 ? 'does' : 'do'}. ${holding}`
        : holding,
      action: showElsewhere,
    };
  }

  if (q) {
    return {
      title: kind === 'all'
        ? `Nothing in the library matches “${q}”`
        : `No ${driverKindWord(kind)} driver matches “${q}”`,
      detail: `${holding} No brand or model contains that.`,
      action: browseAll ?? (counts.total === 0 ? null : {
        label: `Browse all ${driverCountText(counts.total)}`,
        kind: 'all',
        clearQuery: true,
      }),
    };
  }

  if (counts.total === 0) {
    return {
      title: 'The library is empty',
      detail: 'No CSV file in the driver library folder holds a driver with Thiele-Small data. Enter one by hand below.',
      action: null,
    };
  }

  return {
    title: kind === 'all'
      ? 'No drivers to show'
      : `The library has no ${driverKindWord(kind)} drivers`,
    detail: holding,
    action: browseAll,
  };
}

/**
 * Which half of the library a channel's search opens on.
 *
 * The channel's own preference wins, but only while it names a filter that can
 * still answer a question. A type the library holds one driver of is not a
 * filter, it is a dead end with a button on it: every query but that one
 * driver's name comes back empty, which is precisely how a stocked library
 * reads as a broken one. Unknown counts -- an older server -- leave the
 * preference alone rather than second-guessing it.
 */
export function openingSearchKind(preferred: DriverKind, counts: DriverKindTally): DriverSearchKind {
  if (!counts.known) return preferred;
  return counts[preferred] > 1 ? preferred : 'all';
}
