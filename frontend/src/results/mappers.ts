import type { JobResults, NullableNumber, PolarSample } from '../api/results';
import { applySmoothing, type SmoothingMode } from './smoothing';
import { resultChannels, type ResultPayload } from './types';
import { drivePowerSeries, excursionSeries, hasElectricalImpedance } from './drivePower';
import { displayPhaseDegrees, groupDelayMilliseconds, propagationReference } from './phaseAnalysis';

export interface NamedResult {
  id: string;
  label: string;
  result: JobResults;
  /** The CAD envelope owns per-source evidence that its channel does not repeat. */
  wrapper?: JobResults;
}

/** Expand an imported result's unit-drive bases into the same named flow as runs. */
export function expandResultChannels(id: string, label: string, result: JobResults): NamedResult[] {
  if (!result.channels) return [{ id, label, result }];
  const channels = resultChannels(result as ResultPayload);
  if (!channels.length) return [{ id, label, result }];
  return channels.map(({ id: channel, result: channelResult }) => ({
    id: `${id}#${channel}`,
    label: `${label} · ${channel}`,
    result: channelResult,
    wrapper: result,
  }));
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function complexToDb(real: number, imaginary: number, reference = 1): number | null {
  const magnitude = Math.hypot(real, imaginary);
  if (!Number.isFinite(magnitude) || magnitude <= 0 || reference <= 0) return null;
  return 20 * Math.log10(magnitude / reference);
}

function patternDb(value: NullableNumber | [number, number]): number | null {
  if (Array.isArray(value)) return complexToDb(Number(value[0]), Number(value[1]));
  return finite(value) ? value : null;
}

function frequencyAxis(result: JobResults, nested?: number[]): number[] {
  return nested?.length ? nested : result.frequencies;
}

export function splSeries(items: NamedResult[], smoothing: SmoothingMode = 'none') {
  return items.map(({ label, result }) => {
    const frequencies = frequencyAxis(result, result.spl_on_axis?.frequencies);
    const spl = applySmoothing(frequencies, result.spl_on_axis?.spl ?? [], smoothing);
    return {
      name: label,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: frequencies.map((frequency, index) => [frequency, spl[index] ?? null]),
    };
  });
}

export interface DirectivityGrid {
  frequencies: number[];
  angles: number[];
  data: Array<[number, number, number | null]>;
  minDb: number;
  maxDb: number;
}

export function directivityGrid(result: JobResults, plane = 'horizontal'): DirectivityGrid {
  const frequencies = result.frequencies;
  const patterns = (result.directivity as Record<string, PolarSample[][]> | undefined)?.[plane] ?? [];
  const angles = [...new Set(patterns.flatMap((row) => row.map((sample) => Number(sample[0]))))]
    .filter(Number.isFinite).sort((a, b) => a - b);
  const data: DirectivityGrid['data'] = [];
  let minDb = 0;
  let maxDb = -Infinity;
  patterns.forEach((row, frequencyIndex) => {
    row.forEach((sample: PolarSample) => {
      const db = patternDb(sample[1]);
      if (db !== null) {
        minDb = Math.min(minDb, db);
        maxDb = Math.max(maxDb, db);
      }
      data.push([frequencies[frequencyIndex], Number(sample[0]), db]);
    });
  });
  return { frequencies, angles, data, minDb: Math.max(-60, Math.floor(minDb / 5) * 5), maxDb: Number.isFinite(maxDb) ? maxDb : 0 };
}

export function nearestFrequencyIndex(frequencies: number[], target: number): number {
  if (!frequencies.length) return 0;
  return frequencies.reduce((best, frequency, index) => (
    Math.abs(frequency - target) < Math.abs(frequencies[best] - target) ? index : best
  ), 0);
}

export function polarSeries(result: JobResults, frequencyIndex: number, plane: 'horizontal' | 'vertical') {
  const row = result.directivity?.[plane]?.[frequencyIndex] ?? [];
  // Radius first, angle second: the polar chart's series contract, and the
  // tuple type keeps the null-able level distinguishable from the angle.
  return row.map(([angle, value]) => [patternDb(value), angle] as [number | null, number]);
}

export function impedanceSeries(result: JobResults, mode: 'cartesian' | 'polar', smoothing: SmoothingMode = 'none') {
  const frequencies = frequencyAxis(result, result.impedance?.frequencies);
  const real = applySmoothing(frequencies, result.impedance?.real ?? [], smoothing);
  const imaginary = applySmoothing(frequencies, result.impedance?.imaginary ?? [], smoothing);
  if (mode === 'cartesian') {
    return [
      { name: 'Re', type: 'line' as const, showSymbol: false, data: frequencies.map((f, i) => [f, real[i] ?? null]) },
      { name: 'Im', type: 'line' as const, showSymbol: false, data: frequencies.map((f, i) => [f, imaginary[i] ?? null]) },
    ];
  }
  return [
    { name: '|Z|', type: 'line' as const, showSymbol: false, data: frequencies.map((f, i) => [f, finite(real[i]) && finite(imaginary[i]) ? Math.hypot(real[i]!, imaginary[i]!) : null]) },
    { name: 'phase', type: 'line' as const, yAxisIndex: 1, showSymbol: false, data: frequencies.map((f, i) => [f, finite(real[i]) && finite(imaginary[i]) ? Math.atan2(imaginary[i]!, real[i]!) * 180 / Math.PI : null]) },
  ];
}

export function directivityIndexSeries(result: ResultPayload, smoothing: SmoothingMode = 'none') {
  const frequencies = result.di?.frequencies?.length ? result.di.frequencies : result.frequencies;
  const raw = result.di?.di;
  const entries = Array.isArray(raw) ? [['DI', raw] as const] : Object.entries(raw ?? {});
  return entries.filter(([, values]) => values.some(finite)).map(([name, values]) => {
    const smoothed = applySmoothing(frequencies, values, smoothing);
    return {
      name,
      type: 'line' as const,
      showSymbol: false,
      smooth: 0.32,
      smoothMonotone: 'x' as const,
      data: frequencies.map((frequency, index) => [frequency, smoothed[index] ?? null]),
    };
  });
}

export function beamShapeSeries(result: ResultPayload) {
  const beam = result.beam_shape;
  const frequencies = beam?.frequencies?.length ? beam.frequencies : result.balloon?.frequencies ?? [];
  return [
    { name: 'H −6 dB', values: beam?.horizontal_beamwidth_deg ?? [] },
    { name: 'V −6 dB', values: beam?.vertical_beamwidth_deg ?? [] },
  ].map(({ name, values }) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    smooth: 0.32,
    smoothMonotone: 'x' as const,
    data: frequencies.map((frequency, index) => [frequency, values[index] ?? null]),
  }));
}

/**
 * What `result.impedance` actually holds.
 *
 * A driver-modelled channel replaces the normalized acoustic impedance with the
 * driver's electrical terminal impedance in ohms and says so in the metadata.
 * The chart used to hardcode `Z/ρc` regardless, so a driver-coupled run drew
 * ohms under an acoustic label — the ZMA exporter has always read this tag, and
 * the chart did not.
 */
export interface ImpedanceUnits {
  /** Axis title and title-chip unit. */
  axis: string;
  /**
   * What the curve is, spelled out. The card title is just "Impedance" and the
   * unit chip carries Ω against Z/ρc, which works on screen; a screen-reader
   * label has no chip to lean on and has to say the quantity itself.
   */
  spokenName: string;
  electrical: boolean;
}

export const NORMALIZED_ACOUSTIC_IMPEDANCE: ImpedanceUnits = {
  axis: 'Z/ρc',
  spokenName: 'normalized acoustic impedance',
  electrical: false,
};

export const ELECTRICAL_IMPEDANCE: ImpedanceUnits = {
  axis: 'Ω',
  spokenName: 'electrical impedance',
  electrical: true,
};

export function impedanceUnits(result: ResultPayload): ImpedanceUnits {
  return hasElectricalImpedance(result) ? ELECTRICAL_IMPEDANCE : NORMALIZED_ACOUSTIC_IMPEDANCE;
}

/**
 * Runs that can share one impedance axis.
 *
 * Ohms and Z/ρc are different quantities; overlaying them would draw two
 * incomparable curves against one scale and invite exactly the misreading this
 * fix exists to prevent. The primary run decides the axis and any run in the
 * other unit is left out.
 *
 * `excluded` is what the card puts in its subtitle. A comparison run vanishing
 * from one chart with no note reads as a solve that failed, so the count has to
 * reach the screen — see `impedanceSubtitle`.
 */
export function impedanceComparable(items: NamedResult[]): { items: NamedResult[]; units: ImpedanceUnits; excluded: number } {
  const present = items.filter(({ result }) => {
    const payload = result as ResultPayload;
    const impedance = payload.impedance;
    const frequencies = impedance?.frequencies?.length ? impedance.frequencies : payload.frequencies;
    return Boolean(frequencies?.length && [...(impedance?.real ?? []), ...(impedance?.imaginary ?? [])].some(finite));
  });
  if (!present.length) return { items: [], units: NORMALIZED_ACOUSTIC_IMPEDANCE, excluded: 0 };
  const units = impedanceUnits(present[0].result as ResultPayload);
  const matching = present.filter(({ result }) => impedanceUnits(result as ResultPayload).electrical === units.electrical);
  return { items: matching, units, excluded: present.length - matching.length };
}

/**
 * The card subtitle naming the runs `impedanceComparable` dropped.
 *
 * Null when nothing was dropped, so the subtitle only appears when it has
 * something to say. Naming the *other* unit is the point: "hidden" alone reads
 * as a failure, while "2 Ω runs hidden" says the runs are fine and the axis is
 * the constraint.
 */
export function impedanceSubtitle(items: NamedResult[]): string | null {
  const { units, excluded } = impedanceComparable(items);
  if (!excluded) return null;
  const other = units.electrical ? NORMALIZED_ACOUSTIC_IMPEDANCE : ELECTRICAL_IMPEDANCE;
  return `${excluded} ${other.axis} run${excluded === 1 ? '' : 's'} hidden · cannot share a ${units.axis} axis`;
}

/** On-axis phase, referenced and detrended exactly as the exported PNG draws it. */
export function phaseSeries(items: NamedResult[]) {
  return items.map(({ label, result }) => {
    const payload = result as ResultPayload;
    const frequencies = payload.spl_on_axis?.frequencies?.length
      ? payload.spl_on_axis.frequencies
      : payload.frequencies;
    const points = displayPhaseDegrees(
      { frequencies, phaseDegrees: payload.spl_on_axis?.phase_degrees ?? [] },
      payload.spl_on_axis?.spl ?? [],
      propagationReference(payload),
    );
    return {
      name: label,
      points: points.map(({ frequencyHz, value }) => [frequencyHz, value] as [number, number]),
    };
  }).filter(({ points }) => points.length > 0);
}

/** Group delay in ms, common time-of-flight removed. */
export function groupDelaySeries(items: NamedResult[]) {
  return items.map(({ label, result }) => {
    const payload = result as ResultPayload;
    const frequencies = payload.spl_on_axis?.frequencies?.length
      ? payload.spl_on_axis.frequencies
      : payload.frequencies;
    const points = groupDelayMilliseconds(
      { frequencies, phaseDegrees: payload.spl_on_axis?.phase_degrees ?? [] },
      propagationReference(payload),
    );
    return {
      name: label,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: points.map(({ frequencyHz, value }) => [frequencyHz, value]),
    };
  }).filter(({ data }) => data.length > 0);
}

/**
 * Real power and RMS current per frequency.
 *
 * Both live on one card because they answer the same question — what the
 * amplifier has to supply — and they peak in different places: power follows
 * the resistive part, current follows the impedance minimum.
 */
export function drivePowerChartSeries(result: ResultPayload) {
  const series = drivePowerSeries(result);
  if (!series) return [];
  const { frequencies, watts, amps } = series;
  return [
    { name: 'Power', axis: 0, values: watts },
    { name: 'Current', axis: 1, values: amps },
  ].map(({ name, axis, values }) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    connectNulls: false,
    yAxisIndex: axis,
    data: frequencies.map((frequency, index) => [frequency, values[index] ?? null]),
  }));
}

/** Cone excursion with the Xmax limit drawn as a reference line when known. */
export function excursionChartSeries(result: ResultPayload) {
  const series = excursionSeries(result);
  if (!series) return [];
  const { frequencies, millimetres, xmaxMm } = series;
  const traces = [{
    name: 'Excursion',
    type: 'line' as const,
    showSymbol: false,
    connectNulls: false,
    data: frequencies.map((frequency, index) => [frequency, millimetres[index] ?? null]),
  }];
  if (xmaxMm !== null && frequencies.length) {
    traces.push({
      // Same rounding as the summary row: a spec float like 5.300000000000001
      // otherwise lands verbatim in the legend.
      name: `Xmax ${Number(xmaxMm.toPrecision(3))} mm`,
      type: 'line' as const,
      showSymbol: false,
      connectNulls: false,
      data: [[frequencies[0], xmaxMm], [frequencies[frequencies.length - 1], xmaxMm]],
    });
  }
  return traces;
}

/**
 * The beam-fit numbers that were transported to the browser and never drawn.
 *
 * Aspect ratio and the superellipse exponent are what actually say *what shape*
 * the beam is; the beam-width chart only says how wide. Fit residual rides with
 * them because a shape read off a poor fit is not a shape.
 */
export function beamFitSeries(result: ResultPayload) {
  const beam = result.beam_shape;
  const frequencies = beam?.frequencies?.length ? beam.frequencies : result.balloon?.frequencies ?? [];
  return [
    { name: 'Aspect ratio (H/V)', values: beam?.aspect_ratio ?? [], axis: 0 },
    { name: 'Superellipse exponent', values: beam?.shape_exponent ?? [], axis: 0 },
    { name: 'Fit residual [%]', values: beam?.fit_residual_percent ?? [], axis: 1 },
  ].filter(({ values }) => values.some(finite)).map(({ name, values, axis }) => ({
    name,
    type: 'line' as const,
    showSymbol: false,
    connectNulls: false,
    smooth: 0.32,
    smoothMonotone: 'x' as const,
    yAxisIndex: axis,
    data: frequencies.map((frequency, index) => [frequency, values[index] ?? null]),
  }));
}

type PolarPlane = 'horizontal' | 'vertical';

/**
 * Which polar cut each reduced domain makes exact, by `resolved_quadrants`.
 *
 * Read this as: the cut that *sweeps across* the reduced plane is the one the
 * solve already guarantees. Derived from the server, not assumed:
 *
 * - `quadrants.py` `_PLANE_BY_QUADRANTS` maps 1 -> `yz+xz`, 12 -> `xz`,
 *   14 -> `yz`, 1234 -> none, and the mesher agrees (`config_builder.py`:
 *   "12" spans y >= 0, the xz mirror; "14" spans x >= 0, the yz mirror).
 * - `result_mapping.py` `_gmsh22_observation_frame_parts` builds the cut basis
 *   from the axis: WG sweeps cross-sections along z, so the axis is ±z, which
 *   takes the `reference_axis = x̂` branch and yields `horizontal = x̂`,
 *   `vertical = axis × horizontal = ŷ`. `_project_to_symmetry` puts the sweep
 *   origin on the plane. So the horizontal cut sweeps the xz plane (crossing
 *   x = 0) and the vertical cut sweeps the yz plane (crossing y = 0).
 * - Therefore: 12 reduces about y = 0, which only the *vertical* cut crosses;
 *   14 reduces about x = 0, which only the *horizontal* cut crosses. The other
 *   cut lies flat *inside* its own mirror plane, where reflection maps every
 *   sample onto itself and says nothing about the unsampled side.
 * - `imported.py` `polar_grid_from_symmetry` states the same pairing
 *   independently, for the CAD path: "horizontal <- x0, vertical <- y0".
 *
 * Getting this backwards would draw a radiation pattern that never existed and
 * is indistinguishable from computed data, so the table is exhaustive and
 * anything not in it — 1234, an absent tag, a value we do not recognise —
 * mirrors nothing.
 */
const MIRRORED_CUT_BY_QUADRANTS: Record<number, readonly PolarPlane[]> = {
  1: ['horizontal', 'vertical'],
  12: ['vertical'],
  14: ['horizontal'],
};

/**
 * Whether this one-sided polar cut may be reflected about the reference axis.
 *
 * WG solves a reduced domain and samples only the angles it computed, so every
 * result's polar data runs 0..90 or 0..180 rather than -180..180. Reflecting
 * that is not invention when the solve *itself* was built on the symmetry: the
 * mirror side is identical to the sampled side by construction rather than
 * merely assumed to be. Which cut that applies to depends on which plane was
 * reduced — see `MIRRORED_CUT_BY_QUADRANTS`.
 */
export function polarMirrorsAcrossAxis(
  result: ResultPayload,
  plane: PolarPlane,
  wrapper?: ResultPayload,
): boolean {
  // An imported CAD result records the solve's symmetry once on the envelope,
  // not on each unit-drive channel, so reading only the channel would leave
  // every CAD-import polar one-sided. Same wrapper-then-channel join the
  // summary card uses.
  const symmetry = result.metadata?.symmetry ?? wrapper?.metadata?.symmetry;
  if (!symmetry || typeof symmetry !== 'object') return false;
  const quadrants = Number((symmetry as Record<string, unknown>).resolved_quadrants);
  return MIRRORED_CUT_BY_QUADRANTS[quadrants]?.includes(plane) ?? false;
}

/** A polar cut spanning both sides of the reference axis, mirroring if allowed. */
export function polarCut(
  result: ResultPayload,
  frequencyIndex: number,
  plane: PolarPlane,
  wrapper?: ResultPayload,
): Array<[number | null, number]> {
  const points = polarSeries(result, frequencyIndex, plane);
  if (!points.length) return points;
  const oneSided = points.every(([, angle]) => angle >= 0);
  if (!oneSided || !polarMirrorsAcrossAxis(result, plane, wrapper)) return points;
  const mirrored = points
    .filter(([, angle]) => angle > 0)
    .map(([db, angle]) => [db, -angle] as [number | null, number]);
  return [...mirrored, ...points].sort((left, right) => left[1] - right[1]);
}
