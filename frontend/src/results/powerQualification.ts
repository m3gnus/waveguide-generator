import type { EChartsOption } from 'echarts';
import type { ChartTokens } from './EChart';
import type { NamedResult } from './mappers';
import { combineMetadataOf, resultChannels, type ResultPayload } from './types';
import { formatValidityFrequency, resultFrequencyValidity } from './validity';

/**
 * Radiated-power qualification: which frequencies of a channel failed the
 * sphere/driven-face power check inside the channel's validity band.
 *
 * The server owns the flags (`metadata.power_qualification`, written at solve
 * time or derived when an archived run is opened). A payload that never went
 * through the server -- a live partial result -- is evaluated here by the same
 * rules, so the view never goes quiet about a failure it can see.
 */

export const POWER_QUALIFICATION_THRESHOLD_DB = 0.5;

export const UNQUALIFIED_MESSAGE = "Unqualified: this channel's result is sensitive to solver stabilisation here; treat it as unreliable until re-solved with a qualified solver.";

export type PowerQualificationStatus = 'qualified' | 'unqualified' | 'unknown';
export type FrequencyQualification = 'qualified' | 'unqualified' | 'outside_validity' | 'unchecked';

export interface UnqualifiedRange {
  start_hz: number;
  end_hz: number;
  count: number;
  reasons: string[];
  channels: string[];
}

export interface PowerQualification {
  status: PowerQualificationStatus;
  evaluated: 'solve' | 'read_time' | 'client';
  validityMaxHz: number | null;
  frequencyStatus: FrequencyQualification[];
  frequencyReasons: Array<string | null>;
  ranges: UnqualifiedRange[];
  reasons: string[];
  unknownReason: string | null;
  formulation: string | null;
  complexKShift: number | null;
  unqualifiedChannels: string[];
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
}

const STATUSES = new Set<PowerQualificationStatus>(['qualified', 'unqualified', 'unknown']);
const FREQUENCY_STATUSES = new Set<FrequencyQualification>(['qualified', 'unqualified', 'outside_validity', 'unchecked']);

function fromServer(raw: Record<string, unknown>): PowerQualification | null {
  if (!STATUSES.has(raw.status as PowerQualificationStatus)) return null;
  const provenance = record(raw.provenance) ?? {};
  const ranges = Array.isArray(raw.unqualified_ranges) ? raw.unqualified_ranges.flatMap((item): UnqualifiedRange[] => {
    const range = record(item);
    if (!range || !finite(range.start_hz) || !finite(range.end_hz)) return [];
    return [{
      start_hz: range.start_hz,
      end_hz: range.end_hz,
      count: finite(range.count) ? range.count : 1,
      reasons: strings(range.reasons),
      channels: strings(range.channels),
    }];
  }) : [];
  return {
    status: raw.status as PowerQualificationStatus,
    evaluated: raw.evaluated === 'read_time' ? 'read_time' : 'solve',
    validityMaxHz: finite(raw.validity_max_hz) ? raw.validity_max_hz : null,
    frequencyStatus: Array.isArray(raw.frequency_status)
      ? raw.frequency_status.map((value) => FREQUENCY_STATUSES.has(value as FrequencyQualification) ? value as FrequencyQualification : 'unchecked')
      : [],
    frequencyReasons: Array.isArray(raw.frequency_reasons) ? raw.frequency_reasons.map((value) => typeof value === 'string' ? value : null) : [],
    ranges,
    reasons: strings(raw.reasons),
    unknownReason: typeof raw.unknown_reason === 'string' ? raw.unknown_reason : null,
    formulation: typeof provenance.formulation === 'string' ? provenance.formulation : null,
    complexKShift: finite(provenance.complex_k_shift) ? provenance.complex_k_shift : null,
    unqualifiedChannels: strings(raw.unqualified_channels),
  };
}

function rangesOf(frequencies: number[], status: FrequencyQualification[], reasons: Array<string | null>, culprits?: string[][]): UnqualifiedRange[] {
  const order = frequencies.map((_, index) => index).filter((index) => finite(frequencies[index])).sort((a, b) => frequencies[a] - frequencies[b]);
  const ranges: UnqualifiedRange[] = [];
  let current: UnqualifiedRange | null = null;
  for (const index of order) {
    if (status[index] !== 'unqualified') { current = null; continue; }
    if (!current) {
      current = { start_hz: frequencies[index], end_hz: frequencies[index], count: 0, reasons: [], channels: [] };
      ranges.push(current);
    }
    current.end_hz = frequencies[index];
    current.count += 1;
    const reason = reasons[index];
    if (reason && !current.reasons.includes(reason)) current.reasons.push(reason);
    for (const channel of culprits?.[index] ?? []) if (!current.channels.includes(channel)) current.channels.push(channel);
  }
  return ranges;
}

/** The same rules as `server/solver/power_qualification.py`, for payloads the server never flagged. */
function evaluateChannel(result: ResultPayload, wrapper: ResultPayload): PowerQualification | null {
  const power = record(result.metadata?.radiated_power);
  if (!power) return null;
  const frequencies = result.frequencies ?? [];
  const pick = (key: string) => frequencies.map((_, index) => {
    const value = Array.isArray(power[key]) ? (power[key] as unknown[])[index] : undefined;
    return finite(value) ? value : null;
  });
  const surface = pick('surface_w');
  const sphere = pick('sphere_w');
  const agreement = pick('agreement_db');
  if (!surface.some((value) => value !== null) || !sphere.some((value) => value !== null)) return null;
  const validityMaxHz = resultFrequencyValidity(result, wrapper)?.governingMaxFrequencyHz ?? null;
  const status: FrequencyQualification[] = [];
  const reasons: Array<string | null> = [];
  frequencies.forEach((frequency, index) => {
    if (!finite(frequency)) { status.push('unchecked'); reasons.push(null); return; }
    if (validityMaxHz !== null && frequency > validityMaxHz) { status.push('outside_validity'); reasons.push(null); return; }
    const face = surface[index];
    const far = sphere[index];
    let reason: string | null;
    if (face === null) reason = 'nonfinite_face_power';
    else if (face <= 0) reason = 'nonpositive_face_power';
    else if (far === null || far <= 0) reason = 'invalid_sphere_power';
    else {
      const ratio = agreement[index] ?? 10 * Math.log10(far / face);
      reason = Math.abs(ratio) > POWER_QUALIFICATION_THRESHOLD_DB ? 'power_mismatch' : null;
    }
    status.push(reason ? 'unqualified' : 'qualified');
    reasons.push(reason);
  });
  const ranges = rangesOf(frequencies, status, reasons);
  return {
    status: ranges.length ? 'unqualified' : 'qualified',
    evaluated: 'client',
    validityMaxHz,
    frequencyStatus: status,
    frequencyReasons: reasons,
    ranges,
    reasons: [...new Set(reasons.filter((reason): reason is string => Boolean(reason)))].sort(),
    unknownReason: null,
    formulation: null,
    complexKShift: null,
    unqualifiedChannels: [],
  };
}

function evaluateCombined(result: ResultPayload, wrapper: ResultPayload, members: string[]): PowerQualification | null {
  const frequencies = result.frequencies ?? [];
  const status: FrequencyQualification[] = frequencies.map(() => 'qualified');
  const reasons: Array<string | null> = frequencies.map(() => null);
  const culprits: string[][] = frequencies.map(() => []);
  let evaluatedAny = false;
  for (const member of members) {
    const payload = wrapper.channels?.[member] as ResultPayload | undefined;
    const flags = payload ? powerQualificationOf(payload, wrapper) : null;
    if (!payload || !flags) continue;
    evaluatedAny = true;
    const flagged = (payload.frequencies ?? []).filter((_, index) => flags.frequencyStatus[index] === 'unqualified');
    frequencies.forEach((frequency, index) => {
      if (flagged.some((value) => Math.abs(value - frequency) <= 1e-9 * Math.max(1, Math.abs(frequency)))) {
        status[index] = 'unqualified';
        reasons[index] = 'member_unqualified';
        culprits[index].push(member);
      }
    });
  }
  if (!evaluatedAny) return null;
  const ranges = rangesOf(frequencies, status, reasons, culprits);
  const unqualifiedChannels = [...new Set(culprits.flat())].sort();
  return {
    status: unqualifiedChannels.length ? 'unqualified' : 'qualified',
    evaluated: 'client',
    validityMaxHz: null,
    frequencyStatus: status,
    frequencyReasons: reasons,
    ranges,
    reasons: unqualifiedChannels.length ? ['member_unqualified'] : [],
    unknownReason: null,
    formulation: null,
    complexKShift: null,
    unqualifiedChannels,
  };
}

/** The qualification of one shown payload, or null when nothing can be said. */
export function powerQualificationOf(result: ResultPayload | undefined, wrapper: ResultPayload | undefined = result): PowerQualification | null {
  if (!result) return null;
  const raw = record(result.metadata?.power_qualification);
  const server = raw ? fromServer(raw) : null;
  if (server) return server;
  const combine = combineMetadataOf(result);
  if (combine && wrapper?.channels) return evaluateCombined(result, wrapper, combine.members);
  return evaluateChannel(result, wrapper ?? result);
}

/** "200 Hz–1.21 kHz, 1.81 kHz": the flagged samples, as solved. */
export function formatUnqualifiedRanges(ranges: UnqualifiedRange[]): string {
  return ranges.map(({ start_hz: start, end_hz: end }) => start === end
    ? formatValidityFrequency(start)
    : `${formatValidityFrequency(start)}–${formatValidityFrequency(end)}`).join(', ');
}

export interface UnqualifiedBand {
  /** Drawn edges: halfway (geometrically) to the neighbouring samples, so a single flagged sample still has width. */
  fromHz: number;
  toHz: number;
  label: string;
}

/** Chart bands for one payload's unqualified runs. */
export function unqualifiedBands(result: ResultPayload, flags: PowerQualification, label = ''): UnqualifiedBand[] {
  const frequencies = result.frequencies ?? [];
  const order = frequencies.map((_, index) => index).filter((index) => finite(frequencies[index]) && frequencies[index] > 0).sort((a, b) => frequencies[a] - frequencies[b]);
  const bands: UnqualifiedBand[] = [];
  let start: number | null = null;
  const close = (position: number) => {
    if (start === null) return;
    const first = order[start];
    const last = order[position - 1];
    const below = start > 0 ? Math.sqrt(frequencies[order[start - 1]] * frequencies[first]) : frequencies[first];
    const above = position < order.length ? Math.sqrt(frequencies[last] * frequencies[order[position]]) : frequencies[last];
    bands.push({ fromHz: below, toHz: above, label });
    start = null;
  };
  order.forEach((index, position) => {
    if (flags.frequencyStatus[index] === 'unqualified') {
      if (start === null) start = position;
    } else close(position);
  });
  close(order.length);
  return bands;
}

/**
 * Every unqualified band among the runs a chart draws. Members of a combined
 * sum are covered by the sum's own flags, which already name them.
 */
export function chartUnqualifiedBands(entries: NamedResult[]): UnqualifiedBand[] {
  const primaries = entries.filter(({ secondary }) => !secondary);
  const flagged = primaries.flatMap((entry) => {
    const payload = entry.result as ResultPayload;
    const flags = powerQualificationOf(payload, (entry.wrapper ?? entry.result) as ResultPayload);
    return flags?.status === 'unqualified' ? [{ entry, payload, flags }] : [];
  });
  return flagged.flatMap(({ entry, payload, flags }) => unqualifiedBands(payload, flags, flagged.length > 1 ? entry.label : ''));
}

let hatchPattern: { image: HTMLCanvasElement; repeat: 'repeat' } | null | undefined;

/** Diagonal hatching; plain translucent fill where no canvas exists. */
function hatchFill(): { image: HTMLCanvasElement; repeat: 'repeat' } | string {
  const fallback = 'rgba(224, 176, 98, .16)';
  if (hatchPattern === undefined) {
    hatchPattern = null;
    try {
      // OffscreenCanvas marks a real rendering engine: a DOM emulator has
      // canvas elements whose 2D context is only a stub.
      const canvas = typeof document !== 'undefined' && typeof OffscreenCanvas !== 'undefined' ? document.createElement('canvas') : null;
      const context = canvas?.getContext('2d');
      if (canvas && context) {
        canvas.width = 8;
        canvas.height = 8;
        context.fillStyle = 'rgba(224, 176, 98, .10)';
        context.fillRect(0, 0, 8, 8);
        context.strokeStyle = 'rgba(224, 176, 98, .55)';
        context.lineWidth = 1;
        context.beginPath();
        context.moveTo(0, 8);
        context.lineTo(8, 0);
        context.moveTo(-2, 2);
        context.lineTo(2, -2);
        context.moveTo(6, 10);
        context.lineTo(10, 6);
        context.stroke();
        hatchPattern = { image: canvas, repeat: 'repeat' };
      }
    } catch {
      hatchPattern = null;
    }
  }
  return hatchPattern ?? fallback;
}

/**
 * Hatch the unqualified frequency bands behind a frequency-axis line chart.
 *
 * The bands ride on the first series as a mark area, which needs no legend
 * entry and no data of its own. Nothing about any curve changes: the marking
 * says the level is unreliable, it never offers a corrected one.
 */
export function withUnqualifiedBands(option: EChartsOption, bands: UnqualifiedBand[], tokens: ChartTokens): EChartsOption {
  if (!bands.length || !Array.isArray(option.series) || !option.series.length) return option;
  const [first, ...rest] = option.series;
  const markArea = {
    silent: true,
    itemStyle: { color: hatchFill(), borderColor: 'rgba(224, 176, 98, .6)', borderWidth: .6, borderType: 'dashed' as const },
    label: { show: true, position: 'insideTop' as const, color: tokens.muted, fontSize: 10, formatter: (params: { name?: string }) => params.name || 'Unqualified' },
    emphasis: { disabled: true },
    data: bands.map(({ fromHz, toHz, label }) => [
      { name: label ? `Unqualified · ${label}` : 'Unqualified', xAxis: fromHz },
      { xAxis: toHz },
    ]),
  };
  return { ...option, series: [{ ...(first as object), markArea } as typeof first, ...rest] };
}

/** The chip's short label for the shown channel. */
export function powerChipLabel(flags: PowerQualification): string {
  if (flags.status === 'unqualified') return 'Power check: unqualified ⚠';
  if (flags.status === 'qualified') return 'Power check ✓';
  return 'Power check: unknown';
}

const REASON_TEXT: Record<string, string> = {
  power_mismatch: 'the far-field sphere power and the driven-face power differ by more than 0.5 dB',
  nonpositive_face_power: 'the driven-face power is zero or negative, so no power ratio exists',
  nonfinite_face_power: 'the driven-face power is missing',
  invalid_sphere_power: 'the far-field sphere power is missing or nonpositive',
  member_unqualified: 'a contributing channel is unqualified there',
};

const UNKNOWN_TEXT: Record<string, string> = {
  power_check_unavailable: 'This result carries no radiated-power check, so its solver stability cannot be judged from it.',
  provenance_missing: 'This result does not record which solver formulation produced it, so it cannot be qualified.',
  member_unknown: 'At least one channel in this sum carries no power check, so the sum cannot be qualified.',
};

/** Sentences for the chip's detail, beneath its heading. */
export function powerQualificationDetail(flags: PowerQualification, channelLabel: (id: string) => string = (id) => id): string[] {
  const lines: string[] = [];
  if (flags.status === 'unqualified') {
    lines.push(`${UNQUALIFIED_MESSAGE}`);
    lines.push(`Affected: ${formatUnqualifiedRanges(flags.ranges)}${flags.validityMaxHz !== null ? `, inside the ${formatValidityFrequency(flags.validityMaxHz)} validity limit` : ''}.`);
    const why = flags.reasons.map((reason) => REASON_TEXT[reason]).filter(Boolean);
    if (why.length) lines.push(`Why: ${why.join('; ')}.`);
    if (flags.unqualifiedChannels.length) lines.push(`Unqualified contributing channel${flags.unqualifiedChannels.length > 1 ? 's' : ''}: ${flags.unqualifiedChannels.map(channelLabel).join(', ')}.`);
  } else if (flags.status === 'qualified') {
    lines.push(`Driven-face and far-field power agree within ${POWER_QUALIFICATION_THRESHOLD_DB} dB at every checked frequency${flags.validityMaxHz !== null ? ` up to the ${formatValidityFrequency(flags.validityMaxHz)} validity limit` : ''}.`);
  } else {
    lines.push(UNKNOWN_TEXT[flags.unknownReason ?? ''] ?? 'This result cannot be qualified from what it records.');
  }
  if (flags.formulation) lines.push(`Solved with ${flags.formulation}${flags.complexKShift !== null ? ` (shift ${flags.complexKShift})` : ''}.`);
  if (flags.evaluated === 'read_time') lines.push('Flagged when this archived run was opened, from its stored power, validity and formulation records. No level has been changed.');
  return lines;
}

/** One line naming every unqualified run a card draws, or null when none is. */
export function unqualifiedCaption(entries: NamedResult[]): string | null {
  const primaries = entries.filter(({ secondary }) => !secondary);
  const parts = primaries.flatMap((entry) => {
    const flags = powerQualificationOf(entry.result as ResultPayload, (entry.wrapper ?? entry.result) as ResultPayload);
    if (flags?.status !== 'unqualified') return [];
    const ranges = formatUnqualifiedRanges(flags.ranges);
    return [primaries.length > 1 ? `${entry.label}: ${ranges}` : ranges];
  });
  return parts.length ? `Unqualified ${parts.join(' · ')}` : null;
}

/**
 * "Channels: LF unqualified · HF qualified · Combined unqualified" -- every
 * channel's standing at once, so the chip of one channel never hides another's
 * failure. Null for a single-channel run.
 */
export function channelQualificationSummary(wrapper: ResultPayload | undefined, channelLabel: (id: string) => string = (id) => id): string | null {
  if (!wrapper) return null;
  const channels = resultChannels(wrapper);
  if (channels.length < 2) return null;
  const parts = channels.map(({ id, result }) => `${channelLabel(id)} ${powerQualificationOf(result, wrapper)?.status ?? 'unknown'}`);
  return `Channels: ${parts.join(' · ')}.`;
}
