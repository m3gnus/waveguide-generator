import { describe, expect, it } from 'vitest';
import {
  drivePowerSeries,
  electricalDrive,
  excursionSeries,
  hasElectricalImpedance,
} from './drivePower';
import type { ResultPayload } from './types';

/** A driver-coupled channel: impedance in ohms, with the drive recorded. */
function driven(overrides: Partial<ResultPayload> = {}, metadata: Record<string, unknown> = {}): ResultPayload {
  return {
    frequencies: [100],
    impedance: { frequencies: [100], real: [8], imaginary: [0] },
    metadata: {
      impedance_units: 'ohms',
      impedance_quantity: 'electrical_input_impedance',
      drive: { voltage_v: 2.83, rg_ohm: 0 },
      ...metadata,
    },
    ...overrides,
  } as ResultPayload;
}

describe('electrical impedance detection', () => {
  it('accepts a result tagged in ohms', () => {
    expect(hasElectricalImpedance(driven())).toBe(true);
  });

  it('rejects the normalized acoustic impedance a unit-acceleration solve carries', () => {
    const acoustic = {
      frequencies: [100],
      impedance: { frequencies: [100], real: [0.4], imaginary: [0.2] },
      metadata: { impedance_units: 'Z/(rho*c)', impedance_quantity: 'specific_acoustic_impedance' },
    } as ResultPayload;
    expect(hasElectricalImpedance(acoustic)).toBe(false);
    expect(drivePowerSeries(acoustic)).toBeNull();
  });
});

describe('drive resolution', () => {
  it('reads the direct-radiator channel shape', () => {
    expect(electricalDrive(driven())).toEqual({ voltageV: 2.83, rgOhm: 0 });
  });

  it('reads the passive-cardioid shape, which names the same two numbers differently', () => {
    const cardioid = driven({}, { drive: undefined, passive_cardioid: { drive_voltage_v: 4, rg_ohm: 0.5 } });
    expect(electricalDrive(cardioid)).toEqual({ voltageV: 4, rgOhm: 0.5 });
  });

  it('has no drive when neither shape is present', () => {
    expect(electricalDrive(driven({}, { drive: undefined }))).toBeNull();
    expect(drivePowerSeries(driven({}, { drive: undefined }))).toBeNull();
  });

  it('treats a missing source resistance as zero rather than discarding the drive', () => {
    expect(electricalDrive(driven({}, { drive: { voltage_v: 2.83 } }))).toEqual({ voltageV: 2.83, rgOhm: 0 });
  });
});

describe('power and current', () => {
  it('gives the textbook 2.83 V into 8 ohms', () => {
    const series = drivePowerSeries(driven())!;
    expect(series.watts[0]).toBeCloseTo(2.83 ** 2 / 8, 10);
    expect(series.amps[0]).toBeCloseTo(2.83 / 8, 10);
    // A purely resistive load has no reactive part, so W and VA coincide.
    expect(series.voltAmps[0]).toBeCloseTo(series.watts[0]!, 10);
  });

  it('drops the current through a series source resistance the driver never sees', () => {
    const series = drivePowerSeries(driven({}, { drive: { voltage_v: 2.83, rg_ohm: 2 } }))!;
    // Rg is inside the loop, so it sets the current; the power is still only
    // what the 8 ohm terminal resistance dissipates.
    expect(series.amps[0]).toBeCloseTo(2.83 / 10, 10);
    expect(series.watts[0]).toBeCloseTo((2.83 / 10) ** 2 * 8, 10);
  });

  it('uses the real part, not the magnitude, on a reactive load', () => {
    const reactive = driven({ impedance: { frequencies: [100], real: [6], imaginary: [8] } });
    const series = drivePowerSeries(reactive)!;
    // |Z| = 10, so a naive |V|^2/|Z| would claim 0.80 W; the true real power is
    // |I|^2 * Re(Z) = 0.48 W. Overstating it by two thirds is the whole reason
    // this is written out longhand.
    expect(series.watts[0]).toBeCloseTo((2.83 / 10) ** 2 * 6, 10);
    expect(series.watts[0]).toBeLessThan(2.83 ** 2 / 10);
    expect(series.voltAmps[0]).toBeCloseTo((2.83 / 10) ** 2 * 10, 10);
  });

  it('reports no power for a purely reactive sample rather than zero or negative', () => {
    const purelyReactive = driven({ impedance: { frequencies: [100], real: [0], imaginary: [8] } });
    const series = drivePowerSeries(purelyReactive)!;
    expect(series.watts[0]).toBeNull();
    expect(series.amps[0]).toBeCloseTo(2.83 / 8, 10);
  });

  it('carries nulls through rather than plotting a gap as a value', () => {
    const gapped = driven({
      frequencies: [100, 200],
      impedance: { frequencies: [100, 200], real: [8, null], imaginary: [0, 0] },
    });
    const series = drivePowerSeries(gapped)!;
    expect(series.watts[1]).toBeNull();
    expect(series.amps[1]).toBeNull();
  });

  it('falls back to the result sweep when the impedance block carries no grid', () => {
    const series = drivePowerSeries(driven({
      frequencies: [250],
      impedance: { real: [8], imaginary: [0] },
    }))!;
    expect(series.frequencies).toEqual([250]);
  });
});

describe('cone excursion', () => {
  it('reads the direct-radiator block with its own grid, peak and Xmax', () => {
    const series = excursionSeries(driven({}, {
      driver: {
        spec: { xmax_mm: 5 },
        cone_excursion_mm: { frequencies: [50, 100], values: [3, 1], peak_mm: 3 },
      },
    }))!;
    expect(series.frequencies).toEqual([50, 100]);
    expect(series.millimetres).toEqual([3, 1]);
    expect(series.peakMm).toBe(3);
    expect(series.xmaxMm).toBe(5);
  });

  it('reads the cardioid array, derives its peak and keeps the MF driver Xmax', () => {
    const series = excursionSeries({
      frequencies: [50, 100],
      passive_cardioid: { cone_excursion_mm: [2, 4] },
      metadata: { driver: { spec: { xmax_mm: 5 } } },
    } as ResultPayload)!;
    expect(series.frequencies).toEqual([50, 100]);
    expect(series.peakMm).toBe(4);
    expect(series.xmaxMm).toBe(5);
  });

  it('has nothing to report for a solve with no driver', () => {
    expect(excursionSeries({ frequencies: [100], metadata: {} } as ResultPayload)).toBeNull();
  });
});
