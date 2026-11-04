/** Display-only pressure-basis conversion. Counterpart: solver/excitation_basis.py. */
import type { ResultData, PolarSample } from '../api/results';
import { complexMultiply, isSupportedSection, type Complex } from './crossoverFilters';
import { onAxisComplex, rawWeight } from './memberResponse';
import { fromResult } from './crossoverSpec';
import { phaseSpatialSign, wrapPhaseDegrees } from './phaseConvention';
import { combineMetadataOf, type ResultPayload } from './types';

export type ExcitationBasis = 'rms_velocity' | 'acceleration_legacy';
type ChannelBasis = ExcitationBasis | 'voltage' | null;

export function channelExcitationBasis(result: ResultData): ChannelBasis {
  const metadata = result.metadata;
  switch (metadata?.source_normalization) {
    case 'native_rms_voltage': case 'voltage_driven_driver_lem': return 'voltage';
    case 'native_rms_velocity': return 'rms_velocity';
    case 'unit_normal_acceleration': return 'acceleration_legacy';
  }
  return null;
}

/** Only recorded identities, including the server's legacy decoder, are accepted. */
export function runExcitationBasis(result: ResultData): ExcitationBasis | null {
  const stored = result.metadata?.excitation_normalization;
  if (stored === 'rms_velocity' || stored === 'acceleration_legacy') return stored;
  const bases = Object.values(result.channels ?? { result }).filter(channel => !combineMetadataOf(channel as ResultPayload)).map(channelExcitationBasis).filter(b => b !== 'voltage');
  return bases.length && bases.every(b => b !== null && b === bases[0]) ? bases[0] : null;
}

/**
 * Pressure per acceleration -> pressure per velocity multiplies by -jω in
 * solver exp(-jωt), exactly undoing acceleration_scale = 1/(-jω).
 * Engineering phasors are conjugate, so their factor is +jω. The inverse
 * divides by that factor. These are pressure transfer functions, not a
 * conversion of an acceleration amplitude to a velocity amplitude.
 */
export function excitationBasisFactor(from: ExcitationBasis, to: ExcitationBasis, frequencyHz: number, spatialSign: 1 | -1): Complex | null {
  if (from === to) return [1, 0];
  if (!Number.isFinite(frequencyHz) || frequencyHz <= 0) return null;
  const imaginary = -spatialSign * 2 * Math.PI * frequencyHz;
  return from === 'acceleration_legacy' ? [0, imaginary] : [0, -1 / imaginary];
}

/** Immutable conversion; unknown identity or phase convention fails closed. */
export function withExcitationBasis(result: ResultData, target: ExcitationBasis | null): ResultData | null {
  const basis = channelExcitationBasis(result);
  if (basis === 'voltage') return result;
  if (!target || !basis) return null;
  if (basis === target) return result;
  const sign = phaseSpatialSign(result.metadata?.phase_time_convention);
  if (sign === null) return null;
  const frequencies = result.spl_on_axis?.frequencies ?? result.frequencies;
  if (!frequencies?.length) return null;
  const factors = frequencies.map(f => excitationBasisFactor(basis, target, f, sign));
  if (factors.some(f => !f)) return null;
  const gain = (i: number) => 20 * Math.log10(Math.hypot(...factors[i]!));
  const phase = (i: number) => Math.atan2(factors[i]![1], factors[i]![0]) * 180 / Math.PI;
  const spl_on_axis = result.spl_on_axis && {
    ...result.spl_on_axis,
    spl: result.spl_on_axis.spl?.map((db, i) => db === null ? null : db + gain(i)),
    phase_degrees: result.spl_on_axis.phase_degrees?.map((degrees, i) => degrees === null ? null : wrapPhaseDegrees(degrees + phase(i))),
  };
  // Scalar directivity samples are normalized dB; complex samples are fields.
  const directivity = result.directivity && Object.fromEntries(Object.entries(result.directivity).map(([plane, rows]) => [plane,
    rows?.map((row, i) => row.map(([angle, value]): PolarSample => [angle, Array.isArray(value) ? [...complexMultiply(value, factors[i]!)] as [number, number] : value])),
  ]));
  const payload = result as ResultPayload;
  const directivity_phase = payload.directivity_phase && Object.fromEntries(Object.entries(payload.directivity_phase).map(([plane, rows]) => [plane,
    rows?.map((row, i) => row.map(([angle, degrees]) => [angle, degrees === null ? null : wrapPhaseDegrees(degrees + phase(i))])),
  ]));
  const combine = combineMetadataOf(payload);
  const member_responses = combine?.member_responses && Object.fromEntries(Object.entries(combine.member_responses).map(([member, response]) => [member, {
    spl: response.spl.map((db, i) => db + gain(i)),
    phase_degrees: response.phase_degrees.map((degrees, i) => wrapPhaseDegrees(degrees + phase(i))),
  }]));
  const axisMetadata = result.metadata?.spl_on_axis as { plane_reference_spl_db?: Record<string, Array<number | null>> } | undefined;
  const plane_reference_spl_db = axisMetadata?.plane_reference_spl_db && Object.fromEntries(Object.entries(axisMetadata.plane_reference_spl_db).map(([plane, levels]) => [plane, levels.map((db, i) => db === null ? null : db + gain(i))]));
  return { ...result, spl_on_axis, directivity, ...(directivity_phase ? { directivity_phase } : {}), metadata: {
    ...result.metadata, ...(plane_reference_spl_db ? { spl_on_axis: { ...axisMetadata, plane_reference_spl_db } } : {}), source_normalization: target === 'rms_velocity' ? 'native_rms_velocity' : 'unit_normal_acceleration',
    ...(combine && member_responses ? { combine: { ...combine, member_responses } } : {}),
  } };
}

/** Combined fields can be rescaled only if every physical member shares a basis. */
export function withRunExcitationBasis(wrapper: ResultData, target: ExcitationBasis | null): ResultData | null {
  if (!wrapper.channels) return withExcitationBasis(wrapper, target);
  const channels: Record<string, ResultData> = {};
  for (const [id, channel] of Object.entries(wrapper.channels)) {
    const combine = combineMetadataOf(channel as ResultPayload);
    let converted: ResultData | null;
    if (combine) {
      const physical = combine.members.flatMap(member => combine.member_channels?.[member] ?? [member]);
      const bases = physical.map(member => wrapper.channels![member] && channelExcitationBasis(wrapper.channels![member]));
      const prescribed = bases.filter(b => b !== 'voltage');
      if (bases.length && bases.every(b => b === 'voltage')) converted = channel;
      else if (target && prescribed.length && prescribed.every(b => b === target)) converted = channel;
      else if (bases.length && bases[0] && bases.every(b => b === bases[0])) {
        converted = withExcitationBasis({ ...channel, metadata: { ...channel.metadata,
          source_normalization: bases[0] === 'rms_velocity' ? 'native_rms_velocity' : 'unit_normal_acceleration',
        } }, target);
      } else converted = convertedCombinedOnAxis(wrapper, channel, target);
    } else converted = withExcitationBasis(channel, target);
    if (converted) channels[id] = converted;
  }
  return { ...wrapper, channels, metadata: { ...wrapper.metadata, ...(target ? { excitation_normalization: target } : {}) } };
}

/** Rebuild a mixed sum on-axis, using recorded pre-DSP branches when available. */
function convertedCombinedOnAxis(wrapper: ResultData, combined: ResultData, target: ExcitationBasis | null): ResultData | null {
  if (!target) return null;
  const combine = combineMetadataOf(combined as ResultPayload);
  const spec = combine && fromResult(combine);
  const frequencies = combined.spl_on_axis?.frequencies ?? combined.frequencies;
  if (!combine || !spec || !frequencies?.length) return null;
  if (spec.members.some(member => [spec.channels[member].hp, spec.channels[member].lp].some(section => section && !isSupportedSection(section)))) return null;
  if (phaseSpatialSign(combined.metadata?.phase_time_convention) !== 1) return null;
  const original: Complex[][] = [];
  const converted: Complex[][] = [];
  const responses: NonNullable<typeof combine.member_responses> = {};
  for (const member of spec.members) {
    const physical = combine.member_channels?.[member] ?? [member];
    const bases = physical.map(id => wrapper.channels?.[id] && channelExcitationBasis(wrapper.channels[id]));
    if (bases.some(b => !b)) return null;
    const stored = combine.member_responses?.[member];
    let before: Complex[];
    let after: Complex[];
    if (stored) {
      if (!bases.every(b => b === bases[0])) return null;
      const metadata = wrapper.channels![physical[0]].metadata;
      const branch: ResultData = { frequencies, spl_on_axis: { frequencies, ...stored }, metadata };
      const next = withExcitationBasis(branch, target);
      const a = onAxisComplex(branch.spl_on_axis, frequencies.length);
      const b = next && onAxisComplex(next.spl_on_axis, frequencies.length);
      if (!a || !b) return null;
      before = a; after = b;
    } else {
      // Group processing needs its own retained branch; summing unprocessed
      // physical fields would silently drop member delay, polarity and weight.
      if (combine.member_channels && physical.length > 1) return null;
      const channel = wrapper.channels![physical[0]];
      const next = withExcitationBasis(channel, target);
      const a = onAxisComplex(channel.spl_on_axis, frequencies.length);
      const b = next && onAxisComplex(next.spl_on_axis, frequencies.length);
      const grid = channel.spl_on_axis?.frequencies ?? channel.frequencies;
      if (!a || !b || grid.length !== frequencies.length || grid.some((f, i) => f !== frequencies[i])) return null;
      before = a; after = b;
    }
    original.push(before); converted.push(after);
    responses[member] = { spl: after.map(z => 20 * Math.log10(Math.hypot(...z))), phase_degrees: after.map(z => Math.atan2(z[1], z[0]) * 180 / Math.PI) };
  }
  const weightedSum = (fields: Complex[][], index: number): Complex => spec.members.reduce<Complex>((sum, member, mi) => {
    const settings = combine.channels?.[member];
    const weight = rawWeight(spec, member, settings?.gain_db ?? combine.gains_db?.[member] ?? 0,
      settings?.delay_ms ?? combine.delays_ms?.[member] ?? 0, settings?.inverted === true, frequencies[index]);
    const value = complexMultiply(weight, fields[mi][index]);
    return [sum[0] + value[0], sum[1] + value[1]];
  }, [0, 0]);
  const shipped = onAxisComplex(combined.spl_on_axis, frequencies.length);
  if (!shipped) return null;
  // Check the complete complex sum, including sign; magnitude alone can hide
  // a phase convention error. Tolerance accommodates stored JSON rounding.
  if (frequencies.some((_, i) => {
    const rebuilt = weightedSum(original, i);
    return Math.hypot(rebuilt[0] - shipped[i][0], rebuilt[1] - shipped[i][1]) > 0.06 * Math.max(Math.hypot(...shipped[i]), 1e-12);
  })) return null;
  const field = frequencies.map((_, i) => weightedSum(converted, i));
  const { radiated_power: _power, ...metadata } = combined.metadata ?? {};
  return {
    ...combined, directivity: undefined,
    // A scalar on-axis branch carries no angular phase or sphere data from
    // which to rebuild mixed directivity and power. Keep those charts hidden.
    directivity_phase: undefined, balloon: undefined, di: undefined,
    spl_on_axis: { frequencies, spl: field.map(z => 20 * Math.log10(Math.hypot(...z))), phase_degrees: field.map(z => Math.atan2(z[1], z[0]) * 180 / Math.PI) },
    metadata: { ...metadata, excitation_comparison_on_axis_only: true,
      combine: { ...combine, member_responses: responses, max_output: undefined } },
  } as ResultData;
}
