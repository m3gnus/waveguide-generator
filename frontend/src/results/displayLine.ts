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
 * polar chart, with finite-difference slopes, so it passes through every solved
 * sample exactly and departs from the straight segments by hundredths of a dB.
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
  const axis = axisAt(option.xAxis, series.xAxisIndex ?? 0);
  if (axis?.type === 'log') return { param: 0, value: 1, logarithmic: true, clamp: [-Infinity, Infinity] };
  if (axis?.type === 'value') return { param: 0, value: 1, logarithmic: false, clamp: [-Infinity, Infinity] };
  return null;
}

function isDatum(item: unknown): item is Datum {
  return Array.isArray(item) && item.length >= 2 && item.every((value) => value === null || typeof value === 'number');
}

/**
 * Cubic Hermite samples through `(t, v)`, `subdivisions` per interval, ending
 * on the last point. Slopes are central differences, one-sided at the ends.
 */
export function hermiteSamples(t: number[], v: number[], subdivisions: number): Array<[number, number]> {
  const count = t.length;
  if (count < 3 || subdivisions < 2) return t.map((param, index) => [param, v[index]]);
  const slopes = v.map((_value, index) => {
    const before = Math.max(0, index - 1);
    const after = Math.min(count - 1, index + 1);
    return (v[after] - v[before]) / (t[after] - t[before]);
  });
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
