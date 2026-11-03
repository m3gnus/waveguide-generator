import type { EChartsOption } from 'echarts';

/**
 * Smooth display lines through the solved points.
 *
 * A solve has about eight points per octave, and on an expanded chart the
 * straight segments between them read as corners. Each line series is drawn
 * twice under one name: first a dense cubic Hermite curve through every solved
 * point, which is what you see, then the original series with an invisible
 * line, which is what the tooltip and the hover symbol read. Sharing the name
 * makes a legend click hide or show both, and the legend key takes the first,
 * visible one's style.
 *
 * The curve is interpolated in log frequency on a log axis and in degrees on a
 * polar chart. Its slopes are Fritsch-Carlson monotone: flat at a local peak
 * or dip, and limited so that between two solved samples it never leaves their
 * range. It passes through every solved sample exactly and invents no peak,
 * dip or overshoot that the solve did not contain.
 * ECharts' own `smooth` is not used: its monotone mode puts a staircase ripple
 * on every curve and its plain mode flattens the tops of peaks.
 *
 * Applied at render time rather than in the option builders, so the options,
 * their tests and the frequency bounds all still describe the solved data.
 */

/** Dense points per drawn line; a long measured trace is left as it is. */
const MAX_DISPLAY_POINTS = 2_400;
const MAX_SUBDIVISIONS = 8;

type Datum = Array<number | null>;

interface LineSeries {
  type?: string;
  coordinateSystem?: string;
  xAxisIndex?: number;
  data?: unknown;
  areaStyle?: unknown;
  step?: unknown;
  smooth?: unknown;
  lineStyle?: Record<string, unknown>;
  markArea?: unknown;
  markLine?: unknown;
  markPoint?: unknown;
}

interface Layout {
  /** Index of the curve parameter (frequency, angle) and of its value in a datum. */
  param: number;
  value: number;
  logarithmic: boolean;
  clamp: [number, number];
}

function axisAt(axes: unknown, index: number): Record<string, unknown> | undefined {
  const list = Array.isArray(axes) ? axes : [axes];
  return list[index] as Record<string, unknown> | undefined;
}

function layoutOf(series: LineSeries, option: EChartsOption): Layout | null {
  if (series.coordinateSystem === 'polar') {
    const radius = axisAt(option.radiusAxis, 0);
    const min = typeof radius?.min === 'number' ? radius.min : -Infinity;
    const max = typeof radius?.max === 'number' ? radius.max : Infinity;
    // Radius first, angle second; an overshoot past the ring or below the
    // floor would draw outside the dial.
    return { param: 1, value: 0, logarithmic: false, clamp: [min, max] };
  }
  // Only frequency responses. A linear axis carries geometry (the bore
  // profile), whose corners are real and must not be rounded off.
  const axis = axisAt(option.xAxis, series.xAxisIndex ?? 0);
  return axis?.type === 'log' ? { param: 0, value: 1, logarithmic: true, clamp: [-Infinity, Infinity] } : null;
}

function isDatum(item: unknown): item is Datum {
  return Array.isArray(item) && item.length >= 2 && item.every((value) => value === null || typeof value === 'number');
}

/**
 * Fritsch-Carlson slopes: central differences, one-sided at the ends, zero
 * where the data turn, and scaled down wherever alpha^2 + beta^2 would exceed
 * 9. Each Hermite interval is then monotone, so it stays between its samples.
 */
function monotoneSlopes(t: number[], v: number[]): number[] {
  const count = t.length;
  const secants = t.slice(0, -1).map((param, index) => (v[index + 1] - v[index]) / (t[index + 1] - param));
  const slopes = v.map((_value, index) => {
    if (index === 0) return secants[0];
    if (index === count - 1) return secants[count - 2];
    if (secants[index - 1] * secants[index] <= 0) return 0;
    return (v[index + 1] - v[index - 1]) / (t[index + 1] - t[index - 1]);
  });
  secants.forEach((secant, index) => {
    if (secant === 0) {
      slopes[index] = 0;
      slopes[index + 1] = 0;
      return;
    }
    // A slope against the secant (only possible at an end) would overshoot.
    const alpha = Math.max(0, slopes[index] / secant);
    const beta = Math.max(0, slopes[index + 1] / secant);
    const size = alpha * alpha + beta * beta;
    const scale = size > 9 ? 3 / Math.sqrt(size) : 1;
    slopes[index] = scale * alpha * secant;
    slopes[index + 1] = scale * beta * secant;
  });
  return slopes;
}

/**
 * Monotone cubic Hermite samples through `(t, v)`, `subdivisions` per
 * interval, ending on the last point.
 */
export function hermiteSamples(t: number[], v: number[], subdivisions: number): Array<[number, number]> {
  const count = t.length;
  if (count < 3 || subdivisions < 2) return t.map((param, index) => [param, v[index]]);
  const slopes = monotoneSlopes(t, v);
  const samples: Array<[number, number]> = [];
  for (let index = 0; index < count - 1; index += 1) {
    const h = t[index + 1] - t[index];
    for (let step = 0; step < subdivisions; step += 1) {
      const u = step / subdivisions;
      const u2 = u * u;
      const u3 = u2 * u;
      samples.push([
        t[index] + u * h,
        (2 * u3 - 3 * u2 + 1) * v[index] + (u3 - 2 * u2 + u) * h * slopes[index]
          + (-2 * u3 + 3 * u2) * v[index + 1] + (u3 - u2) * h * slopes[index + 1],
      ]);
    }
  }
  samples.push([t[count - 1], v[count - 1]]);
  return samples;
}

/** The dense data of one series, or null when it should be drawn as it is. */
function denseData(data: Datum[], layout: Layout): Datum[] | null {
  const usable = (datum: Datum) => {
    const param = datum[layout.param];
    const value = datum[layout.value];
    return param !== null && value !== null && Number.isFinite(param) && Number.isFinite(value) && (!layout.logarithmic || param > 0);
  };
  const subdivisions = Math.min(MAX_SUBDIVISIONS, Math.floor(MAX_DISPLAY_POINTS / Math.max(1, data.filter(usable).length)));
  if (subdivisions < 2) return null;
  const toParam = (param: number) => (layout.logarithmic ? Math.log10(param) : param);
  const fromParam = (param: number) => (layout.logarithmic ? 10 ** param : param);
  const datum = (param: number, value: number | null): Datum => {
    const point: Datum = [];
    point[layout.param] = param;
    point[layout.value] = value;
    return point;
  };
  const dense: Datum[] = [];
  let run: Datum[] = [];
  let smoothed = false;
  const flush = () => {
    const t = run.map((point) => toParam(point[layout.param] as number));
    const v = run.map((point) => point[layout.value] as number);
    const monotonic = t.every((param, index) => index === 0 || param > t[index - 1])
      || t.every((param, index) => index === 0 || param < t[index - 1]);
    if (run.length >= 3 && monotonic) {
      smoothed = true;
      const [low, high] = layout.clamp;
      hermiteSamples(t, v, subdivisions).forEach(([param, value]) => dense.push(datum(fromParam(param), Math.max(low, Math.min(high, value)))));
    } else {
      dense.push(...run);
    }
    run = [];
  };
  data.forEach((point) => {
    if (usable(point)) {
      run.push(point);
      return;
    }
    flush();
    // The gap itself, so a disconnected trace stays disconnected.
    dense.push(point);
  });
  flush();
  return smoothed ? dense : null;
}

/** Replace each smoothable line series by its display curve and its hidden solved twin. */
export function withDisplayLines(option: EChartsOption): EChartsOption {
  if (!Array.isArray(option.series)) return option;
  let changed = false;
  const series = option.series.flatMap((entry): unknown[] => {
    const line = entry as LineSeries;
    if (line.type !== 'line' || line.areaStyle || line.step || line.smooth || !Array.isArray(line.data) || !line.data.every(isDatum)) return [entry];
    const layout = layoutOf(line, option);
    const data = layout && denseData(line.data, layout);
    if (!data) return [entry];
    changed = true;
    // Band, limit and marker overlays belong to the solved series; drawn twice
    // they would double their opacity.
    const { markArea: _markArea, markLine: _markLine, markPoint: _markPoint, ...drawn } = line;
    return [
      { ...drawn, data, silent: true, showSymbol: false, symbol: 'none', tooltip: { show: false }, lineStyle: { ...line.lineStyle, cap: 'round', join: 'round' } },
      { ...line, lineStyle: { ...line.lineStyle, opacity: 0 } },
    ];
  });
  return changed ? { ...option, series: series as EChartsOption['series'] } : option;
}
