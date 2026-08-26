/**
 * The loudest a solved chain can be run, read back from its combined channel.
 *
 * The solver does the physics (`server/solver/driver_limits.py`); this module
 * only turns what it recorded into traces and sentences. Two different
 * questions are drawn on one chart and it matters which is which:
 *
 *  - a **member** trace is that driver alone at its own ceiling, with the
 *    crossover it has -- what a single driver could do if nothing else had to
 *    keep up with it;
 *  - the **system** trace is the tuned sum turned up until the first member
 *    stops it, which is the number a finished loudspeaker is rated at, and is
 *    never louder than the weakest member allows.
 *
 * Both are small-signal swept-sine ceilings. The caveat travels with the data
 * from the server and is shown, not paraphrased away.
 */
import type { CombineMetadata, MaxOutputLimit, MaxOutputMetadata, ResultPayload } from './types';
import { combineMetadataOf } from './types';
import { MAX_LIMIT_LABELS } from './crossoverSpec';

export function maxOutputOf(payload: ResultPayload | undefined): MaxOutputMetadata | null {
  const combine: CombineMetadata | null = combineMetadataOf(payload);
  const record = combine?.max_output;
  if (!record || !Array.isArray(record.frequencies) || !record.frequencies.length) return null;
  return record;
}

function points(frequencies: number[], values: Array<number | null> | undefined) {
  const series = values ?? [];
  return frequencies.map((frequency, index) => [frequency, series[index] ?? null]);
}

/**
 * The chart's traces, system first.
 *
 * The shown on-axis response rides along as a reference: the gap between it
 * and the system ceiling *is* the headroom, and reading a maximum without the
 * level it is a maximum above is how a 6 dB margin gets mistaken for 60.
 */
export function maxOutputChartSeries(
  result: ResultPayload,
  memberLabel: (member: string) => string = (member) => member,
) {
  const record = maxOutputOf(result);
  if (!record) return [];
  const frequencies = record.frequencies;
  const traces: Array<{
    name: string;
    type: 'line';
    showSymbol: false;
    connectNulls: false;
    data: Array<Array<number | null>>;
    role: 'system' | 'member' | 'shown';
  }> = [{
    name: 'System maximum',
    type: 'line',
    showSymbol: false,
    connectNulls: false,
    data: points(frequencies, record.combined.spl_max_db),
    role: 'system',
  }];
  for (const [member, trace] of Object.entries(record.members ?? {})) {
    traces.push({
      name: `${memberLabel(member)} alone`,
      type: 'line',
      showSymbol: false,
      connectNulls: false,
      data: points(frequencies, trace.spl_max_db),
      role: 'member',
    });
  }
  const shown = result.spl_on_axis;
  if (shown?.frequencies?.length) {
    traces.push({
      name: 'At the shown level',
      type: 'line',
      showSymbol: false,
      connectNulls: false,
      data: shown.frequencies.map((frequency, index) => [frequency, shown.spl?.[index] ?? null]),
      role: 'shown',
    });
  }
  return traces;
}

/**
 * Which member holds the system back, and on what -- the one line a
 * maximum-output chart owes, in the clipped form the card's subtitle is
 * written in ("MF · Xmax · 67% of band").
 *
 * A member with no rating at all is named instead, because that is the more
 * useful line: the system curve simply stops wherever such a member carries
 * the band, and the gap in it is a missing datasheet number, not a result.
 */
export function limitingSummary(
  record: MaxOutputMetadata | null,
  memberLabel: (member: string) => string = (member) => member,
): string | null {
  if (!record) return null;
  const unlimited = record.unlimited_members ?? [];
  if (unlimited.length) {
    return `no ceiling known for ${unlimited.map(memberLabel).join(', ')}`;
  }
  const limiting = record.combined.limiting_member ?? [];
  const limits = record.combined.limit ?? [];
  const counts = new Map<string, number>();
  limiting.forEach((member, index) => {
    const limit = limits[index];
    if (!member || !limit) return;
    // Tab-joined: a channel id is free to contain a space, and splitting
    // one back on a space would report a member that does not exist.
    const key = `${member}\t${limit}`;
    counts.set(key, (counts.get(key) ?? 0) + 1);
  });
  if (!counts.size) return null;
  const total = [...counts.values()].reduce((sum, value) => sum + value, 0);
  const [key, count] = [...counts.entries()].sort((left, right) => right[1] - left[1])[0];
  const [member, limit] = key.split('\t');
  const share = Math.round((100 * count) / Math.max(total, 1));
  return `${memberLabel(member)} \u00b7 ${MAX_LIMIT_LABELS[limit as MaxOutputLimit]} \u00b7 ${share}% of band`;
}

/** A member's band role when the run recorded one, and its channel id when
 * it did not -- "HF" says more in a legend than "drive-hf", and is what the
 * rail calls the same channel. */
export function memberLabelOf(result: ResultPayload | undefined): (member: string) => string {
  const combine = combineMetadataOf(result);
  const members = combine?.members ?? [];
  const roles = combine?.member_roles ?? [];
  const byId = new Map(members.map((member, index) => [member, roles[index] ?? null]));
  return (member) => byId.get(member) || member;
}

/** Why this run has no maximum output, said as the missing input rather than
 * as an absence: every one of these has an obvious thing to go and do. */
export function maxOutputMissingReason(result: ResultPayload): string {
  const combine = combineMetadataOf(result);
  if (!combine) {
    return 'Maximum Output is a property of the combined channel. Pick the combined view of a run whose drive channels were summed.';
  }
  return 'Maximum Output needs a driver limit to measure against: give each channel’s driver an Xmax or a rated power, or set an amplifier limit, then solve again.';
}
