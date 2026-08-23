/**
 * The crossover spec the rail, the results strip, the summary and the exports
 * all read.
 *
 * One model, mirrored from `ChannelCombineSpec` in `server/jobs/models.py`
 * (CADLINK-CROSSOVER-DRIVERS.md §2). The wire form this module emits is always
 * the per-channel v2 form: the legacy `crossovers_hz`/`level_match`/`align`
 * triple is still accepted by the server, but it can only say "LR4 with auto
 * everything", so expanding it here once (`expandLegacy`) keeps every reader on
 * a single shape instead of asking each one to handle two.
 */

export type FilterFamily = 'lr' | 'butterworth' | 'bessel' | 'linear_phase';

/** The one table of what a family is offered in; mirrors `FAMILY_ORDERS` in
 * `server/solver/filters.py`, which is what actually evaluates it. */
export const FILTER_FAMILY_ORDERS: Record<FilterFamily, readonly number[]> = {
  lr: [2, 4, 6, 8],
  butterworth: [1, 2, 3, 4, 5, 6, 7, 8],
  bessel: [2, 3, 4],
  linear_phase: [2, 4, 8],
};

export const FILTER_FAMILIES: readonly FilterFamily[] = ['lr', 'butterworth', 'bessel', 'linear_phase'];

export const FILTER_FAMILY_LABELS: Record<FilterFamily, string> = {
  lr: 'Linkwitz-Riley',
  butterworth: 'Butterworth',
  bessel: 'Bessel',
  linear_phase: 'Linear phase',
};

/** The form a slope reads in beside a frequency: "LR4", "BW3", "BE2", "LP8". */
export const FILTER_FAMILY_SHORT: Record<FilterFamily, string> = {
  lr: 'LR',
  butterworth: 'BW',
  bessel: 'BE',
  linear_phase: 'LP',
};

export interface FilterSection {
  family: FilterFamily;
  order: number;
  fcHz: number;
}

export type GainSetting = { mode: 'auto' } | { mode: 'manual'; db: number };
export type DelaySetting = { mode: 'auto' } | { mode: 'manual'; ms: number };

export interface CrossoverChannel {
  hp: FilterSection | null;
  lp: FilterSection | null;
  gain: GainSetting;
  delay: DelaySetting;
  /** Null is "auto from the filter pair"; true/false override it. */
  invert: boolean | null;
}

export interface CrossoverSpec {
  /** The chain in band order, lowest first. */
  members: string[];
  /** The channel auto alignment pins at 0 ms. Always one of `members`. */
  reference: string;
  channels: Record<string, CrossoverChannel>;
}

export interface FilterSectionWire {
  family: FilterFamily;
  order: number;
  fc_hz: number;
}

export interface CrossoverChannelWire {
  hp: FilterSectionWire | null;
  lp: FilterSectionWire | null;
  gain: GainSetting;
  delay: DelaySetting;
  invert: boolean | null;
}

export interface CrossoverWire {
  members: string[];
  reference: string;
  channels: Record<string, CrossoverChannelWire>;
}

export function isFilterFamily(value: unknown): value is FilterFamily {
  return typeof value === 'string' && value in FILTER_FAMILY_ORDERS;
}

/** The orders this family is offered in, or an empty list for a foreign one. */
export function familyOrders(family: FilterFamily): readonly number[] {
  return FILTER_FAMILY_ORDERS[family] ?? [];
}

/** The order nearest the requested one that the family actually supports, so
 * switching family never leaves the spec holding a combination the server
 * would refuse. */
export function nearestOrder(family: FilterFamily, order: number): number {
  const orders = familyOrders(family);
  if (!orders.length) return order;
  return orders.reduce((best, value) => (
    Math.abs(value - order) < Math.abs(best - order) ? value : best
  ), orders[0]);
}

/** A slope in the unit a crossover is spoken in: 6 dB per octave per order. */
export function slopeLabel(order: number): string {
  return `${order * 6} dB/oct`;
}

/** "LR4", the short form that fits beside a frequency. */
export function sectionShort(section: FilterSection): string {
  return `${FILTER_FAMILY_SHORT[section.family]}${section.order}`;
}

export function frequencyText(hz: number): string {
  if (!Number.isFinite(hz)) return '—';
  return hz >= 1_000
    ? `${Number((hz / 1_000).toPrecision(3))} kHz`
    : `${Number(hz.toPrecision(4))} Hz`;
}

/** "900 Hz BW3" — a section stated the way a designer reads one. */
export function sectionLabel(section: FilterSection): string {
  return `${frequencyText(section.fcHz)} ${sectionShort(section)}`;
}

export function pairKeyOf(lower: string, upper: string): string {
  return `${lower}→${upper}`;
}

export interface CrossoverPair {
  key: string;
  index: number;
  lower: string;
  upper: string;
  lowerLp: FilterSection | null;
  upperHp: FilterSection | null;
  /** The two sections agree on frequency, family and order, so "the crossover"
   * names one setting rather than two that happen to overlap. */
  linked: boolean;
  /** The pair's crossover when it is linked, else null. */
  hz: number | null;
  family: FilterFamily | null;
  order: number | null;
}

function sameSection(lower: FilterSection | null, upper: FilterSection | null): boolean {
  return lower !== null && upper !== null
    && lower.family === upper.family
    && lower.order === upper.order
    && Math.abs(lower.fcHz - upper.fcHz) <= 1e-9 * Math.max(1, Math.abs(lower.fcHz));
}

/** The adjacent pairs of a chain, each with the sections that form it. */
export function pairsOf(spec: CrossoverSpec): CrossoverPair[] {
  return spec.members.slice(0, -1).map((lower, index) => {
    const upper = spec.members[index + 1];
    const lowerLp = spec.channels[lower]?.lp ?? null;
    const upperHp = spec.channels[upper]?.hp ?? null;
    const linked = sameSection(lowerLp, upperHp);
    return {
      key: pairKeyOf(lower, upper),
      index,
      lower,
      upper,
      lowerLp,
      upperHp,
      linked,
      hz: linked ? lowerLp!.fcHz : null,
      family: linked ? lowerLp!.family : null,
      order: linked ? lowerLp!.order : null,
    };
  });
}

/**
 * Whether the rail's simple controls describe this spec completely.
 *
 * Every pair linked, one gain mode across the chain, one delay mode, and no
 * channel overriding its polarity. Anything else has content the pair fields
 * cannot show, which is exactly when the rail must say so rather than draw a
 * value that is not the whole setting.
 */
export function isSimple(spec: CrossoverSpec): boolean {
  const members = spec.members;
  if (members.length < 2) return false;
  if (!pairsOf(spec).every((pair) => pair.linked)) return false;
  const channels = members.map((member) => spec.channels[member]).filter(Boolean);
  if (channels.length !== members.length) return false;
  const gainMode = channels[0].gain.mode;
  const delayMode = channels[0].delay.mode;
  return channels.every((channel) => channel.gain.mode === gainMode
    && channel.delay.mode === delayMode
    && channel.invert === null);
}

function sectionWire(section: FilterSection | null): FilterSectionWire | null {
  return section === null
    ? null
    : { family: section.family, order: section.order, fc_hz: section.fcHz };
}

/** The v2 body: `members`, `reference` and one entry per channel. No
 * `crossovers_hz` — a linked pair is already stated by the two sections, and
 * sending both invites them to disagree. */
export function toWire(spec: CrossoverSpec): CrossoverWire {
  return {
    members: [...spec.members],
    reference: spec.reference,
    channels: Object.fromEntries(spec.members.map((member) => {
      const channel = spec.channels[member];
      return [member, {
        hp: sectionWire(channel?.hp ?? null),
        lp: sectionWire(channel?.lp ?? null),
        gain: channel?.gain.mode === 'manual' ? { mode: 'manual' as const, db: channel.gain.db } : { mode: 'auto' as const },
        delay: channel?.delay.mode === 'manual' ? { mode: 'manual' as const, ms: channel.delay.ms } : { mode: 'auto' as const },
        invert: channel?.invert ?? null,
      }];
    })),
  };
}

/**
 * Expand the legacy LR4 chain into the per-channel form.
 *
 * The same definition `expand_legacy_channels` uses on the server: LR4 pairs,
 * auto gain when level matching is on and a manual 0 dB when it is off, auto
 * delay when alignment is on and a manual 0 ms when it is off, automatic
 * polarity.
 */
export function expandLegacy(
  members: readonly string[],
  crossoversHz: readonly number[],
  levelMatch = true,
  align = true,
  reference?: string,
): CrossoverSpec {
  const gain: GainSetting = levelMatch ? { mode: 'auto' } : { mode: 'manual', db: 0 };
  const delay: DelaySetting = align ? { mode: 'auto' } : { mode: 'manual', ms: 0 };
  const section = (fcHz: number): FilterSection => ({ family: 'lr', order: 4, fcHz });
  return {
    members: [...members],
    reference: reference && members.includes(reference) ? reference : members[members.length - 1],
    channels: Object.fromEntries(members.map((member, index) => [member, {
      hp: index > 0 && Number.isFinite(crossoversHz[index - 1]) ? section(crossoversHz[index - 1]) : null,
      lp: index < crossoversHz.length && Number.isFinite(crossoversHz[index]) ? section(crossoversHz[index]) : null,
      gain: { ...gain },
      delay: { ...delay },
      invert: null,
    } satisfies CrossoverChannel])),
  };
}

function finite(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function sectionFromWire(value: unknown): FilterSection | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const fcHz = finite(record.fc_hz);
  const order = finite(record.order);
  if (!isFilterFamily(record.family) || fcHz === null || order === null) return null;
  return { family: record.family, order, fcHz };
}

/** What one resolved channel of `metadata.combine` says. */
export interface ResolvedChannel {
  gainDb: number | null;
  gainMode: 'auto' | 'manual';
  gainAutoDb: number | null;
  delayMs: number | null;
  delayMode: 'auto' | 'manual';
  delayAutoMs: number | null;
  inverted: boolean | null;
  invertMode: 'auto' | 'manual';
}

/** The payload shape `fromResult` reads: `metadata.combine`, of either
 * generation. Deliberately loose — a legacy result carries neither `channels`
 * nor `reference`, and must still open. */
export interface CombinePayload {
  members?: unknown;
  reference?: unknown;
  crossovers_hz?: unknown;
  channels?: unknown;
  level_match?: unknown;
  align?: unknown;
}

/** Resolved per-channel values from a result: what auto actually chose. */
export function resolvedChannels(combine: CombinePayload | null | undefined): Record<string, ResolvedChannel> {
  const channels = combine?.channels;
  if (!channels || typeof channels !== 'object') return {};
  return Object.fromEntries(Object.entries(channels as Record<string, unknown>).flatMap(([id, value]) => {
    if (!value || typeof value !== 'object') return [];
    const record = value as Record<string, unknown>;
    return [[id, {
      gainDb: finite(record.gain_db),
      gainMode: record.gain_mode === 'manual' ? 'manual' as const : 'auto' as const,
      gainAutoDb: finite(record.gain_auto_db),
      delayMs: finite(record.delay_ms),
      delayMode: record.delay_mode === 'manual' ? 'manual' as const : 'auto' as const,
      delayAutoMs: finite(record.delay_auto_ms),
      inverted: typeof record.inverted === 'boolean' ? record.inverted : null,
      invertMode: record.invert_mode === 'manual' ? 'manual' as const : 'auto' as const,
    } satisfies ResolvedChannel]];
  }));
}

/**
 * Rebuild the spec a result was computed from.
 *
 * Manual values come back verbatim; an auto mode stays auto rather than being
 * frozen into the number it happened to choose, so re-applying an untouched
 * strip re-derives the same auto answer instead of pinning it.
 */
export function fromResult(combine: CombinePayload | null | undefined): CrossoverSpec | null {
  if (!combine) return null;
  const members = Array.isArray(combine.members) && combine.members.every((value) => typeof value === 'string')
    ? combine.members as string[]
    : [];
  if (members.length < 2) return null;
  const reference = typeof combine.reference === 'string' && members.includes(combine.reference)
    ? combine.reference
    : members[members.length - 1];
  const wireChannels = combine.channels && typeof combine.channels === 'object'
    ? combine.channels as Record<string, unknown>
    : null;
  if (!wireChannels || !members.every((member) => wireChannels[member])) {
    const crossovers = Array.isArray(combine.crossovers_hz)
      ? (combine.crossovers_hz as unknown[]).map((value) => finite(value))
      : [];
    if (crossovers.length !== members.length - 1 || crossovers.some((value) => value === null)) return null;
    const levelMatch = combine.level_match && typeof combine.level_match === 'object'
      ? (combine.level_match as Record<string, unknown>).enabled !== false
      : combine.level_match !== false;
    return expandLegacy(members, crossovers as number[], levelMatch, combine.align !== false, reference);
  }
  const resolved = resolvedChannels(combine);
  return {
    members,
    reference,
    channels: Object.fromEntries(members.map((member) => {
      const record = wireChannels[member] as Record<string, unknown>;
      const state = resolved[member];
      return [member, {
        hp: sectionFromWire(record.hp),
        lp: sectionFromWire(record.lp),
        gain: state?.gainMode === 'manual'
          ? { mode: 'manual' as const, db: state.gainDb ?? 0 }
          : { mode: 'auto' as const },
        delay: state?.delayMode === 'manual'
          ? { mode: 'manual' as const, ms: state.delayMs ?? 0 }
          : { mode: 'auto' as const },
        invert: state?.invertMode === 'manual' ? state.inverted ?? null : null,
      } satisfies CrossoverChannel];
    })),
  };
}

function withChannels(spec: CrossoverSpec, channels: Record<string, CrossoverChannel>): CrossoverSpec {
  return { members: [...spec.members], reference: spec.reference, channels };
}

/** Replace one channel, leaving the rest of the spec untouched. */
export function withChannel(
  spec: CrossoverSpec,
  member: string,
  patch: Partial<CrossoverChannel>,
): CrossoverSpec {
  const current = spec.channels[member];
  if (!current) return spec;
  return withChannels(spec, { ...spec.channels, [member]: { ...current, ...patch } });
}

/**
 * Set one pair symmetrically: the lower channel's low-pass and the upper
 * channel's high-pass together. This is what keeps a pair linked, and it is
 * the only edit the rail's pair fields make.
 */
export function withPair(
  spec: CrossoverSpec,
  key: string,
  patch: { hz?: number; family?: FilterFamily; order?: number },
): CrossoverSpec {
  const pair = pairsOf(spec).find((item) => item.key === key);
  if (!pair) return spec;
  const base = pair.lowerLp ?? pair.upperHp;
  const family = patch.family ?? base?.family ?? 'lr';
  const order = nearestOrder(family, patch.order ?? base?.order ?? 4);
  const fcHz = patch.hz ?? base?.fcHz;
  if (fcHz === undefined || !Number.isFinite(fcHz)) return spec;
  const section: FilterSection = { family, order, fcHz };
  return withChannels(spec, {
    ...spec.channels,
    [pair.lower]: { ...spec.channels[pair.lower], lp: { ...section } },
    [pair.upper]: { ...spec.channels[pair.upper], hp: { ...section } },
  });
}

/** Make every pair symmetric from the lower channel's low-pass. A pair whose
 * lower channel has no low-pass takes the upper channel's high-pass instead;
 * a pair with neither is left alone, because there is nothing to copy. */
export function relinkPairs(spec: CrossoverSpec): CrossoverSpec {
  return pairsOf(spec).reduce((current, pair) => {
    const source = pair.lowerLp ?? pair.upperHp;
    if (!source) return current;
    return withPair(current, pair.key, { hz: source.fcHz, family: source.family, order: source.order });
  }, spec);
}

/** Apply one gain mode to every channel. Manual starts from the auto value the
 * latest result reported, so taking over never begins by moving the curve. */
export function withGainMode(
  spec: CrossoverSpec,
  mode: 'auto' | 'manual',
  autoDb: Record<string, number | null> = {},
): CrossoverSpec {
  return withChannels(spec, Object.fromEntries(spec.members.map((member) => {
    const channel = spec.channels[member];
    const current = channel.gain.mode === 'manual' ? channel.gain.db : autoDb[member] ?? 0;
    return [member, {
      ...channel,
      gain: mode === 'manual' ? { mode: 'manual' as const, db: current } : { mode: 'auto' as const },
    }];
  })));
}

/** Apply one delay mode to every channel, on the same rule as `withGainMode`. */
export function withDelayMode(
  spec: CrossoverSpec,
  mode: 'auto' | 'manual',
  autoMs: Record<string, number | null> = {},
): CrossoverSpec {
  return withChannels(spec, Object.fromEntries(spec.members.map((member) => {
    const channel = spec.channels[member];
    const current = channel.delay.mode === 'manual' ? channel.delay.ms : autoMs[member] ?? 0;
    return [member, {
      ...channel,
      delay: mode === 'manual' ? { mode: 'manual' as const, ms: current } : { mode: 'auto' as const },
    }];
  })));
}

export function withReference(spec: CrossoverSpec, reference: string): CrossoverSpec {
  return spec.members.includes(reference)
    ? { members: [...spec.members], reference, channels: spec.channels }
    : spec;
}

/** The mode shared by every channel, or null when they differ. */
export function sharedGainMode(spec: CrossoverSpec): 'auto' | 'manual' | null {
  const modes = new Set(spec.members.map((member) => spec.channels[member]?.gain.mode));
  return modes.size === 1 ? [...modes][0] ?? null : null;
}

export function sharedDelayMode(spec: CrossoverSpec): 'auto' | 'manual' | null {
  const modes = new Set(spec.members.map((member) => spec.channels[member]?.delay.mode));
  return modes.size === 1 ? [...modes][0] ?? null : null;
}

/** The sentence an unlinked pair shows in place of one frequency. */
export function unlinkedPairNote(pair: CrossoverPair): string {
  const lower = pair.lowerLp ? `LP ${sectionLabel(pair.lowerLp)}` : 'no low-pass';
  const upper = pair.upperHp ? `HP ${sectionLabel(pair.upperHp)}` : 'no high-pass';
  return `${lower} / ${upper} — edit in Advanced`;
}

/** A deep copy, so an editor can hold a spec without aliasing the store's. */
export function cloneSpec(spec: CrossoverSpec): CrossoverSpec {
  return {
    members: [...spec.members],
    reference: spec.reference,
    channels: Object.fromEntries(Object.entries(spec.channels).map(([id, channel]) => [id, {
      hp: channel.hp ? { ...channel.hp } : null,
      lp: channel.lp ? { ...channel.lp } : null,
      gain: { ...channel.gain },
      delay: { ...channel.delay },
      invert: channel.invert,
    }])),
  };
}

/** Whether two specs describe the same crossover. Used to decide whether the
 * strip's Apply is live and whether a stored override still says anything. */
export function sameSpec(left: CrossoverSpec | null, right: CrossoverSpec | null): boolean {
  if (left === null || right === null) return left === right;
  return JSON.stringify(toWire(left)) === JSON.stringify(toWire(right));
}

function sectionFromModel(value: unknown): FilterSection | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const fcHz = finite(record.fcHz);
  const order = finite(record.order);
  if (!isFilterFamily(record.family) || fcHz === null || order === null) return null;
  return { family: record.family, order, fcHz };
}

/**
 * Parse a stored spec back into the model, or null when it is not one.
 *
 * This reads the model shape (`fcHz`), which is what the solve profile stores
 * — not the wire shape (`fc_hz`), which only ever travels to the server and
 * comes back through `fromResult`. Strict: a half-parsed crossover would
 * silently solve something other than what the user set.
 */
export function parseSpec(value: unknown): CrossoverSpec | null {
  return parseWith(value, sectionFromModel);
}

/**
 * Parse a spec in the wire shape (`fc_hz`), which is what a submitted job's
 * own `cad_setup.combine` carries.
 *
 * Distinct from `fromResult`: this reads what the user *asked* for, so a
 * manual gain stays manual and an explicit polarity stays explicit. A result's
 * `metadata.combine` states what was *resolved* instead, and reading one as
 * the other would silently turn every stated value back into "auto".
 */
export function parseWire(value: unknown): CrossoverSpec | null {
  return parseWith(value, sectionFromWire);
}

function parseWith(
  value: unknown,
  parseSection: (value: unknown) => FilterSection | null,
): CrossoverSpec | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  const members = Array.isArray(record.members) && record.members.every((item) => typeof item === 'string' && item)
    ? record.members as string[]
    : null;
  if (!members || members.length < 2 || new Set(members).size !== members.length) return null;
  if (typeof record.reference !== 'string' || !members.includes(record.reference)) return null;
  if (!record.channels || typeof record.channels !== 'object') return null;
  const wire = record.channels as Record<string, unknown>;
  if (Object.keys(wire).length !== members.length) return null;
  const channels: Record<string, CrossoverChannel> = {};
  for (const member of members) {
    const entry = wire[member];
    if (!entry || typeof entry !== 'object') return null;
    const channel = entry as Record<string, unknown>;
    const hp = channel.hp === null || channel.hp === undefined ? null : parseSection(channel.hp);
    const lp = channel.lp === null || channel.lp === undefined ? null : parseSection(channel.lp);
    if ((channel.hp && !hp) || (channel.lp && !lp)) return null;
    if (hp && !familyOrders(hp.family).includes(hp.order)) return null;
    if (lp && !familyOrders(lp.family).includes(lp.order)) return null;
    const gain = parseGain(channel.gain);
    const delay = parseDelay(channel.delay);
    if (!gain || !delay) return null;
    if (channel.invert !== null && channel.invert !== undefined && typeof channel.invert !== 'boolean') return null;
    channels[member] = { hp, lp, gain, delay, invert: (channel.invert as boolean | null) ?? null };
  }
  return { members, reference: record.reference, channels };
}

function parseGain(value: unknown): GainSetting | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (record.mode === 'auto') return { mode: 'auto' };
  if (record.mode !== 'manual') return null;
  const db = finite(record.db);
  return db === null ? null : { mode: 'manual', db };
}

function parseDelay(value: unknown): DelaySetting | null {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (record.mode === 'auto') return { mode: 'auto' };
  if (record.mode !== 'manual') return null;
  const ms = finite(record.ms);
  return ms === null ? null : { mode: 'manual', ms };
}

/** Speed of sound used to state a delay as a distance, which is how a baffle
 * offset is measured. The same 343 m/s the solver's own reporting uses. */
export const SPEED_OF_SOUND_M_S = 343;

export function delayDistanceMm(ms: number): number {
  return ms * SPEED_OF_SOUND_M_S;
}
