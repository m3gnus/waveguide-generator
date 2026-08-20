/**
 * Electrical power and current drawn by a driver-coupled solve.
 *
 * Everything needed is already in the result and none of it was displayed: a
 * driver-modelled channel replaces the normalized acoustic impedance with the
 * driver's *electrical terminal impedance in ohms*, and records the generator
 * voltage and source resistance next to it.
 *
 * The generator semantics matter and are easy to get wrong. In hornlab-sim's
 * `driver_coupling`, `drive_voltage_v` is the generator EMF and `Rg` sits in
 * series inside the drive loop, while the returned
 * `electrical_input_impedance` is the *terminal* impedance and excludes Rg.
 * So the current is set by `Z + Rg` and the terminal power by `Re(Z)`:
 *
 *     I  = V_g / (Z + Rg)
 *     P  = |I|² · Re(Z)        power into the driver
 *     S  = |I|² · |Z|          apparent power at the terminals
 *
 * Using `|V|²/|Z|` instead of `|V|²·Re(Z)/|Z|²` would overstate the power
 * everywhere the load is reactive, which is most of the range.
 *
 * These are small-signal LEM numbers at a fixed swept-sine drive voltage: no
 * thermal compression, no voice-coil heating, no inductance nonlinearity, and
 * no relationship to programme material. The chart says so.
 */
import type { ResultPayload } from './types';

export interface ElectricalDrive {
  voltageV: number;
  rgOhm: number;
}

export interface DrivePowerSeries {
  frequencies: number[];
  /** Real power delivered into the driver terminals, watts. */
  watts: Array<number | null>;
  /** RMS current through the driver, amperes. */
  amps: Array<number | null>;
  /** Apparent power at the terminals, volt-amperes. */
  voltAmps: Array<number | null>;
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

/** True when `result.impedance` holds ohms rather than the normalized Z/ρc. */
export function hasElectricalImpedance(result: ResultPayload): boolean {
  const units = result.metadata?.impedance_units;
  const quantity = result.metadata?.impedance_quantity;
  return String(units ?? '').toLowerCase() === 'ohms'
    || String(quantity ?? '') === 'electrical_input_impedance';
}

/**
 * Generator voltage and source resistance, from either driver path.
 *
 * The direct-radiator channel path writes `metadata.drive`; the passive
 * cardioid path carries the same two numbers under different names inside
 * `metadata.passive_cardioid`. Reading only the first left every cardioid run
 * without a power curve for no reason the user could see.
 */
export function electricalDrive(result: ResultPayload): ElectricalDrive | null {
  const drive = record(result.metadata?.drive);
  const voltage = Number(drive?.voltage_v);
  if (finite(voltage) && voltage > 0) {
    const rg = Number(drive?.rg_ohm);
    return { voltageV: voltage, rgOhm: finite(rg) && rg >= 0 ? rg : 0 };
  }
  const cardioid = record(result.metadata?.passive_cardioid);
  const cardioidVoltage = Number(cardioid?.drive_voltage_v);
  if (finite(cardioidVoltage) && cardioidVoltage > 0) {
    const rg = Number(cardioid?.rg_ohm);
    return { voltageV: cardioidVoltage, rgOhm: finite(rg) && rg >= 0 ? rg : 0 };
  }
  return null;
}

/**
 * Power, current and apparent power per frequency, or null when this result is
 * not driver-coupled. A unit-acceleration waveguide solve has no voltage and
 * no ohms, so there is no power to report — the chart shows a stub rather than
 * inventing a drive level.
 */
export function drivePowerSeries(result: ResultPayload): DrivePowerSeries | null {
  if (!hasElectricalImpedance(result)) return null;
  const drive = electricalDrive(result);
  if (!drive) return null;
  const impedance = result.impedance;
  const frequencies = impedance?.frequencies?.length ? impedance.frequencies : result.frequencies;
  const real = impedance?.real ?? [];
  const imaginary = impedance?.imaginary ?? [];
  if (!frequencies?.length || !real.length) return null;

  const watts: Array<number | null> = [];
  const amps: Array<number | null> = [];
  const voltAmps: Array<number | null> = [];
  frequencies.forEach((_, index) => {
    const re = real[index];
    const im = imaginary[index];
    if (!finite(re) || !finite(im)) {
      watts.push(null);
      amps.push(null);
      voltAmps.push(null);
      return;
    }
    // The loop impedance includes Rg; the terminal impedance does not.
    const loopMagnitudeSquared = (re + drive.rgOhm) ** 2 + im ** 2;
    if (!(loopMagnitudeSquared > 0)) {
      watts.push(null);
      amps.push(null);
      voltAmps.push(null);
      return;
    }
    const currentSquared = (drive.voltageV ** 2) / loopMagnitudeSquared;
    const current = Math.sqrt(currentSquared);
    // A passive load cannot have negative resistance; a solver artefact that
    // produced one would otherwise draw as negative power.
    watts.push(re > 0 ? currentSquared * re : null);
    amps.push(current);
    voltAmps.push(currentSquared * Math.hypot(re, im));
  });
  return { frequencies, watts, amps, voltAmps };
}

export interface ExcursionSeries {
  frequencies: number[];
  millimetres: Array<number | null>;
  peakMm: number | null;
  xmaxMm: number | null;
}

/**
 * Cone excursion per frequency, from either driver path.
 *
 * The direct-radiator path stores a `{frequencies, values, peak_mm}` block
 * under `metadata.driver`; the cardioid path stores a bare array on the
 * result's `passive_cardioid` key, sharing the result frequency grid.
 */
export function excursionSeries(result: ResultPayload): ExcursionSeries | null {
  const driver = record(result.metadata?.driver);
  // Direct and derived-cardioid channels now use the same metadata.driver
  // shape. Keep the nested cardioid fallback for retained results produced
  // while the spec only travelled with that derived-channel metadata.
  const cardioidMetadata = record(result.metadata?.passive_cardioid);
  const xmaxMm = Number(
    record(driver?.spec)?.xmax_mm
      ?? record(cardioidMetadata?.driver_spec)?.xmax_mm,
  );
  const block = record(driver?.cone_excursion_mm);
  const values = Array.isArray(block?.values) ? block.values as Array<number | null> : null;
  if (values?.length) {
    const blockFrequencies = Array.isArray(block?.frequencies) ? block.frequencies as number[] : null;
    const frequencies = blockFrequencies?.length ? blockFrequencies : result.frequencies;
    const peak = Number(block?.peak_mm);
    return {
      frequencies,
      millimetres: values,
      peakMm: finite(peak) ? peak : null,
      xmaxMm: finite(xmaxMm) ? xmaxMm : null,
    };
  }
  const cardioid = record(result.passive_cardioid);
  const cardioidValues = Array.isArray(cardioid?.cone_excursion_mm)
    ? cardioid.cone_excursion_mm as Array<number | null>
    : null;
  if (!cardioidValues?.length) return null;
  const present = cardioidValues.filter(finite);
  return {
    frequencies: result.frequencies,
    millimetres: cardioidValues,
    peakMm: present.length ? Math.max(...present) : null,
    xmaxMm: finite(xmaxMm) ? xmaxMm : null,
  };
}
