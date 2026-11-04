import { onAxisComplex, rawWeight } from './memberResponse';
import { channelExcitationBasis, runExcitationBasis, withRunExcitationBasis } from './excitationBasis';
import { complexAbs, complexMultiply, isSupportedSection, type Complex } from './crossoverFilters';
import { fromResult } from './crossoverSpec';
import type { CombineMetadata, ResultPayload } from './types';

/**
 * The reverse null: the same sum with one member's polarity flipped.
 *
 * It is the standard bench check on a crossover. If the two members really are
 * summing the way their filter pair says, inverting one of them must produce a
 * deep cancellation at the crossover; a shallow one means the levels, the
 * delay or the polarity is wrong, and the flat-looking sum was luck.
 *
 * The curve is rebuilt in the browser from what the payload already carries:
 * each member's on-axis magnitude and phase, and the filters, gains, delays
 * and polarities the solve resolved. That reconstruction is then checked
 * against the combined channel's own curve, and the overlay is withheld unless
 * the two agree — an overlay that does not correspond to the drawn sum would
 * be a decoration, not a measurement.
 */

/** How far the rebuilt sum may sit from the shipped one before the overlay is
 * withheld. Comfortably above JSON rounding and far below anything a wrong
 * convention would produce. */
const RECONSTRUCTION_TOLERANCE_DB = 0.5;

export interface ReverseNullTrace {
  name: string;
  points: Array<[number, number]>;
}

function memberLabel(combine: CombineMetadata, index: number): string {
  return combine.member_labels?.[index] ?? combine.member_roles?.[index]
    ?? (combine.member_channels ? `Driver ${index + 1}` : combine.members[index]);
}

function median(values: number[]): number {
  const sorted = [...values].sort((left, right) => left - right);
  return sorted[Math.floor(sorted.length / 2)];
}

/**
 * One trace per adjacent pair, or an empty list when the payload cannot
 * support the overlay honestly.
 *
 * Returns nothing — never a guess — when a member curve is missing, when a
 * filter names a family this build does not evaluate, or when the rebuilt sum
 * does not reproduce the combined channel already on screen.
 */
export function reverseNullTraces(
  wrapper: ResultPayload | undefined,
  combined: ResultPayload | undefined,
  combine: CombineMetadata | null,
): ReverseNullTrace[] {
  if (!wrapper?.channels || !combined || !combine) return [];
  const target = runExcitationBasis(wrapper);
  const physical = combine.members.flatMap(member => combine!.member_channels?.[member] ?? [member]);
  const allVoltage = physical.length > 0 && physical.every(id => channelExcitationBasis(wrapper!.channels![id] ?? { frequencies: [] }) === 'voltage');
  if (!target && !allVoltage) return [];
  if (target) {
    const id = Object.keys(wrapper.channels).find(id => wrapper!.channels![id] === combined);
    const converted = withRunExcitationBasis(wrapper, target);
    if (!converted?.channels || (id && !converted.channels[id])) return [];
    wrapper = converted as ResultPayload;
    if (id) {
      combined = wrapper.channels![id] as ResultPayload;
      // Preserve the caller's requested combine (legacy callers may pass a
      // different reconstruction spec); only converted retained fields change.
      if (combine.member_responses && combined.metadata?.combine?.member_responses) combine = { ...combine, member_responses: combined.metadata.combine.member_responses };
    }
  }
  const spec = fromResult(combine);
  if (!spec || spec.members.length < 2) return [];
  const frequencies = combined.spl_on_axis?.frequencies ?? combined.frequencies;
  if (!frequencies?.length) return [];
  const count = frequencies.length;
  const sum = onAxisComplex(combined.spl_on_axis, count);
  if (!sum) return [];
  const members = spec.members;
  if (members.some((member) => {
    const channel = spec.channels[member];
    return [channel.hp, channel.lp].some((section) => section !== null && !isSupportedSection(section));
  })) return [];

  const pressures: Complex[][] = [];
  for (const member of members) {
    if (combine.member_responses) {
      const response = combine.member_responses[member];
      const field = response && onAxisComplex({ frequencies, ...response }, count);
      if (!field) return [];
      pressures.push(field);
      continue;
    }
    const physical = combine.member_channels?.[member] ?? [member];
    const fields = physical.map(id => onAxisComplex((wrapper.channels![id] as ResultPayload | undefined)?.spl_on_axis, count));
    if (!fields.length || fields.some(field => !field)) return [];
    pressures.push(Array.from({ length: count }, (_, index) => fields.reduce<Complex>(
      (sum, field) => [sum[0] + field![index][0], sum[1] + field![index][1]], [0, 0])));
  }

  const gainDb = members.map((member) => (
    combine.channels?.[member]?.gain_db ?? combine.gains_db?.[member] ?? 0
  ));
  const delayMs = members.map((member) => (
    combine.channels?.[member]?.delay_ms ?? combine.delays_ms?.[member] ?? 0
  ));
  const inverted = members.map((member) => combine.channels?.[member]?.inverted === true);
  if (gainDb.some((value) => !Number.isFinite(value)) || delayMs.some((value) => !Number.isFinite(value))) return [];

  // Weight every member at every frequency once; both the check and each pair's
  // trace are sums over the same table.
  const weighted: Complex[][] = members.map((member, memberIndex) => frequencies.map((frequency, index) => {
    const weight = rawWeight(spec, member, gainDb[memberIndex], delayMs[memberIndex], inverted[memberIndex], frequency);
    return complexMultiply(weight, pressures[memberIndex][index]);
  }));

  const errors: number[] = [];
  for (let index = 0; index < count; index += 1) {
    let real = 0;
    let imaginary = 0;
    for (const member of weighted) {
      real += member[index][0];
      imaginary += member[index][1];
    }
    const rebuilt = Math.hypot(real, imaginary);
    const shipped = complexAbs(sum[index]);
    if (rebuilt <= 0 || shipped <= 0) continue;
    errors.push(Math.abs(20 * Math.log10(rebuilt / shipped)));
  }
  if (!errors.length || median(errors) > RECONSTRUCTION_TOLERANCE_DB) return [];

  return members.slice(0, -1).map((_member, pairIndex) => ({
    name: `Reverse null · ${memberLabel(combine, pairIndex)} → ${memberLabel(combine, pairIndex + 1)}`,
    points: frequencies.map((frequency, index): [number, number] => {
      let real = 0;
      let imaginary = 0;
      weighted.forEach((member, memberIndex) => {
        const sign = memberIndex === pairIndex + 1 ? -1 : 1;
        real += sign * member[index][0];
        imaginary += sign * member[index][1];
      });
      const magnitude = Math.hypot(real, imaginary);
      return [frequency, magnitude > 0 ? 20 * Math.log10(magnitude) : Number.NaN];
    }).filter(([, level]) => Number.isFinite(level)),
  })).filter((trace) => trace.points.length > 0);
}
