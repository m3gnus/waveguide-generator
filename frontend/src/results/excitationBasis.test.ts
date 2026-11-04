import { describe, expect, it } from 'vitest';
import { channelExcitationBasis, excitationBasisFactor, withExcitationBasis, withRunExcitationBasis } from './excitationBasis';
import { compareResultChannels, selectResultChannels, splSeries } from './mappers';
import type { ResultPayload } from './types';

const frequencies = [100, 200, 400];
function channel(basis: string, legacy = false): ResultPayload {
  return {
    frequencies,
    spl_on_axis: { frequencies, spl: frequencies.map(f => 80 - (legacy ? 20 * Math.log10(2 * Math.PI * f) : 0)), phase_degrees: frequencies.map(() => legacy ? 90 : 0) },
    metadata: { source_normalization: basis, phase_time_convention: 'exp(+ikr)' },
  };
}
const rms = () => channel('native_rms_velocity');
const acceleration = () => channel('unit_normal_acceleration', true);

describe('stored excitation comparison', () => {
  it('aligns old CAD acceleration magnitude and phase with new RMS CAD', () => {
    const old = acceleration();
    const converted = withExcitationBasis(old, 'rms_velocity')!;
    converted.spl_on_axis!.spl!.forEach(level => expect(level).toBeCloseTo(80, 10));
    expect(converted.spl_on_axis!.phase_degrees).toEqual([0, 0, 0]);
    expect(old.spl_on_axis!.phase_degrees).toEqual([90, 90, 90]);
    const restored = withExcitationBasis(converted, 'acceleration_legacy')!;
    expect(restored.spl_on_axis!.phase_degrees).toEqual(old.spl_on_axis!.phase_degrees);
    restored.spl_on_axis!.spl!.forEach((level, i) => expect(level).toBeCloseTo(old.spl_on_axis!.spl![i]!, 10));
  });
  it('matches server pressure_per_velocity_scale and conjugates engineering factors', () => {
    expect(excitationBasisFactor('acceleration_legacy', 'rms_velocity', 100, 1)).toEqual([0, -2 * Math.PI * 100]);
    expect(excitationBasisFactor('acceleration_legacy', 'rms_velocity', 100, -1)).toEqual([0, 2 * Math.PI * 100]);
    expect(excitationBasisFactor('acceleration_legacy', 'rms_velocity', 0, 1)).toBeNull();
  });
  it('leaves voltage-driven channels unchanged even without a target basis', () => {
    for (const basis of ['native_rms_voltage', 'voltage_driven_driver_lem']) {
      const driven = channel(basis);
      expect(withExcitationBasis(driven, 'rms_velocity')).toBe(driven);
      expect(withExcitationBasis(driven, null)).toBe(driven);
    }
  });
  it('uses the selected comparison channel and primary stored basis', () => {
    const wrapper = { frequencies, channels: { hf: acceleration() } };
    const entries = compareResultChannels(selectResultChannels('old', 'Old CAD', wrapper, 'hf'), rms());
    expect(entries[0].notComparable).toBeUndefined();
    expect(splSeries(entries)[0].data).toEqual(frequencies.map(f => [f, 80]));
  });
  it('marks missing and unrecognized bases not comparable', () => {
    for (const source_normalization of [undefined, 'future']) {
      const unknown = { ...rms(), metadata: { source_normalization } };
      const [entry] = compareResultChannels([{ id: 'old', label: 'Old', result: unknown }], rms());
      expect(entry.notComparable).toContain('not comparable');
    }
  });
  it('converts homogeneous combined fields and retained member responses together', () => {
    const a = acceleration();
    const combined = { ...a, metadata: { ...a.metadata, combine: { members: ['a', 'b'], crossovers_hz: [200], member_responses: { a: { spl: a.spl_on_axis!.spl, phase_degrees: a.spl_on_axis!.phase_degrees } } } } };
    const converted = withRunExcitationBasis({ frequencies, channels: { a, b: a, combined } }, 'rms_velocity')!;
    expect(converted.channels!.combined.spl_on_axis!.spl).toEqual([80, 80, 80]);
    expect((converted.channels!.combined as ResultPayload).metadata!.combine!.member_responses!.a.phase_degrees).toEqual([0, 0, 0]);
  });
  it('rebuilds a mixed prescribed/voltage sum from each member in the target basis', () => {
    const a = acceleration();
    const b = channel('native_rms_voltage');
    const settings = { hp: null, lp: null, gain_db: 0, delay_ms: 0 };
    const combined = {
      frequencies,
      spl_on_axis: { frequencies,
        spl: frequencies.map(f => 80 + 20 * Math.log10(Math.hypot(1, 1 / (2 * Math.PI * f)))),
        phase_degrees: frequencies.map(f => Math.atan2(1 / (2 * Math.PI * f), 1) * 180 / Math.PI) },
      metadata: { phase_time_convention: 'exp(+ikr)', combine: { members: ['a', 'b'], crossovers_hz: [null], channels: { a: settings, b: settings } } },
    };
    const converted = withRunExcitationBasis({ frequencies, channels: { a, b, combined } }, 'rms_velocity')!;
    const result = converted.channels!.combined;
    expect(result.metadata!.excitation_comparison_on_axis_only).toBe(true);
    result.spl_on_axis!.spl!.forEach(db => expect(db).toBeCloseTo(80 + 20 * Math.log10(2), 10));
    result.spl_on_axis!.phase_degrees!.forEach(phase => expect(phase).toBeCloseTo(0, 10));
    expect(converted.channels!.b).toBe(b);
    expect(result.directivity).toBeUndefined();
  });

  it('withholds a mixed combined field whose prescribed members need conversion', () => {
    const combined = { ...rms(), metadata: { combine: { members: ['a', 'b'], crossovers_hz: [200] } } };
    const converted = withRunExcitationBasis({ frequencies, channels: { a: acceleration(), b: channel('native_rms_voltage'), combined } }, 'rms_velocity')!;
    expect(converted.channels!.combined).toBeUndefined();
    expect(converted.channels!.b.metadata!.source_normalization).toBe('native_rms_voltage');
  });
});

it('never converts a voltage-driven LEM channel that kept a prescribed tag', () => {
  expect(channelExcitationBasis({ metadata: { source_normalization: 'native_rms_velocity', impedance_drive: 'voltage' } } as never)).toBe('voltage');
});
