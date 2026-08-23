import { channelWeight, complexAbs, complexMultiply, isSupportedSection, type Complex } from './crossoverFilters';
import { fromResult, type CrossoverSpec } from './crossoverSpec';
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
const TWO_PI = 2 * Math.PI;

export interface ReverseNullTrace {
  name: string;
  points: Array<[number, number]>;
}

function onAxisComplex(payload: ResultPayload | undefined, count: number): Complex[] | null {
  const spl = payload?.spl_on_axis?.spl;
  const phase = payload?.spl_on_axis?.phase_degrees;
  if (!spl || !phase || spl.length !== count || phase.length !== count) return null;
  const values: Complex[] = [];
  for (let index = 0; index < count; index += 1) {
    const level = spl[index];
    const degrees = phase[index];
    if (typeof level !== 'number' || !Number.isFinite(level)
      || typeof degrees !== 'number' || !Number.isFinite(degrees)) return null;
    const amplitude = 10 ** (level / 20);
    const radians = (degrees * Math.PI) / 180;
    values.push([amplitude * Math.cos(radians), amplitude * Math.sin(radians)]);
  }
  return values;
}

/**
 * The factor the solver applied to one member's raw `exp(+ikr)` field.
 *
 * The filters, the gain and the delay are defined in the engineering
 * `e^{+jωt}` convention (`server/solver/combine.raw_channel_weights`), and the
 * on-axis phase this payload carries is the raw solver phase, so the
 * engineering weight is conjugated exactly once here — the same single
 * convention boundary the solver has.
 */
function rawWeight(
  spec: CrossoverSpec,
  member: string,
  gainDb: number,
  delayMs: number,
  inverted: boolean,
  frequencyHz: number,
): Complex {
  const channel = spec.channels[member];
  const band = channelWeight(channel.hp, channel.lp, frequencyHz);
  const gain = 10 ** (gainDb / 20);
  const angle = TWO_PI * frequencyHz * (delayMs / 1_000);
  const sign = inverted ? -1 : 1;
  // conj(band) * gain * exp(+j 2 pi f tau) * sign
  return complexMultiply(
    [band[0] * gain * sign, -band[1] * gain * sign],
    [Math.cos(angle), Math.sin(angle)],
  );
}

function memberLabel(combine: CombineMetadata, index: number): string {
  return combine.member_roles?.[index] ?? combine.members[index];
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
  const spec = fromResult(combine);
  if (!spec || spec.members.length < 2) return [];
  const frequencies = combined.spl_on_axis?.frequencies ?? combined.frequencies;
  if (!frequencies?.length) return [];
  const count = frequencies.length;
  const sum = onAxisComplex(combined, count);
  if (!sum) return [];
  const members = spec.members;
  if (members.some((member) => {
    const channel = spec.channels[member];
    return [channel.hp, channel.lp].some((section) => section !== null && !isSupportedSection(section));
  })) return [];

  const pressures: Complex[][] = [];
  for (const member of members) {
    const values = onAxisComplex(wrapper.channels[member] as ResultPayload | undefined, count);
    if (!values) return [];
    pressures.push(values);
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
