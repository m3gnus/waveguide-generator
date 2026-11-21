import type { EChartsOption } from 'echarts';
import type { ChartTokens } from './EChart';

/**
 * A chart described as data, for hornlab-plots to draw (`hornlab_plots.spec`).
 *
 * Every saved image of a chart -- the per-chart PNG and the charts in the HTML
 * report -- is drawn by hornlab-plots in the export theme. The screen keeps its
 * interactive ECharts; this module turns what a card has drawn into the
 * renderer's spec, so the image carries exactly the series, axis ranges and
 * labels on screen, and the export theme decides only the look.
 *
 * Colours travel as roles: an integer is a slot in the theme's line cycle,
 * which is the slot the screen took the colour from, so a run keeps its place
 * in the palette from screen to file while the theme owns the hue.
 */
export type ChartColorRole = number | 'accent' | 'ink' | 'muted';
export type ChartLineStyle = 'solid' | 'dashed' | 'dotted';

export interface ChartAxisSpec {
  label?: string;
  scale?: 'log' | 'linear';
  min?: number | null;
  max?: number | null;
}

export interface LineSeriesSpec {
  label: string | null;
  x: Array<number | null>;
  y: Array<number | null>;
  axis?: 0 | 1;
  color: ChartColorRole;
  style?: ChartLineStyle;
  width?: number;
  opacity?: number;
  z?: number;
  /** Indices of points drawn with a hollow marker (reconstructed, not solved). */
  markers?: number[];
}

export interface LineChartSpec {
  kind: 'lines';
  title: string;
  subtitle?: string;
  x: ChartAxisSpec;
  y: ChartAxisSpec;
  y2?: ChartAxisSpec;
  series: LineSeriesSpec[];
  bands?: Array<{ x0: number; x1: number; label?: string }>;
  hlines?: Array<{ y: number; axis?: 0 | 1 }>;
}

export interface PolarSeriesSpec {
  label: string | null;
  angle: Array<number | null>;
  r: Array<number | null>;
  color: ChartColorRole;
  style?: ChartLineStyle;
  width?: number;
  opacity?: number;
}

export interface PolarChartSpec {
  kind: 'polar';
  title: string;
  subtitle?: string;
  r: { min: number; max: number; step?: number; unit?: string };
  series: PolarSeriesSpec[];
  shade?: Array<{ from: number; to: number; label?: string }>;
}

export type ChartSpec = LineChartSpec | PolarChartSpec;

/**
 * Axis titles and a subtitle an option carries for its image only.
 *
 * A card draws its axis names only when it is large enough to spare the room,
 * and a polar card's frequency and plane live in its slider rather than the
 * option. The image always needs them, so the option builders attach them
 * under this symbol: spreading an option keeps it, and ECharts never reads a
 * symbol key.
 */
export const CHART_IMAGE_LABELS = Symbol('chart image labels');

export interface ChartImageLabels {
  y?: string;
  y2?: string;
  subtitle?: string;
}

export function withImageLabels<T extends object>(option: T, labels: ChartImageLabels): T {
  return { ...option, [CHART_IMAGE_LABELS]: labels };
}

export function imageLabelsOf(option: object): ChartImageLabels {
  return (option as { [CHART_IMAGE_LABELS]?: ChartImageLabels })[CHART_IMAGE_LABELS] ?? {};
}

/** What a drawn chart can say about its axes beyond its option: the extents on screen. */
export type AxisExtent = (axis: 'x' | 'y', index: number) => [number, number] | null;

interface SeriesLike {
  name?: string;
  type?: string;
  coordinateSystem?: string;
  yAxisIndex?: number;
  z?: number;
  data?: unknown[];
  showSymbol?: boolean;
  symbolSize?: unknown;
  lineStyle?: { color?: unknown; width?: number; type?: unknown; opacity?: number };
  itemStyle?: { color?: unknown };
  areaStyle?: unknown;
  markArea?: { data?: Array<Array<{ xAxis?: number; name?: string }>> };
  markLine?: { data?: Array<{ yAxis?: number }> };
}

function finite(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

function seriesList(option: EChartsOption): SeriesLike[] {
  const series = option.series;
  return (Array.isArray(series) ? series : series ? [series] : []) as SeriesLike[];
}

function firstOf<T>(value: T | T[] | undefined): T | undefined {
  return Array.isArray(value) ? value[0] : value;
}

function pointOf(item: unknown): [unknown, unknown] {
  const value = Array.isArray(item) ? item : (item as { value?: unknown[] } | null)?.value;
  return Array.isArray(value) ? [value[0], value[1]] : [null, null];
}

function numberOrNull(value: unknown): number | null {
  return finite(value) ? value : null;
}

/** The palette slot a screen colour came from, or the role it plays. */
export function colorRole(color: unknown, tokens: ChartTokens, fallbackSlot: number): ChartColorRole {
  if (typeof color !== 'string') return fallbackSlot % Math.max(1, tokens.series.length);
  const slot = tokens.series.indexOf(color);
  if (slot >= 0) return slot;
  if (color === tokens.accent) return 'accent';
  if (color === tokens.foreground) return 'ink';
  return 'muted';
}

function lineStyleOf(type: unknown): ChartLineStyle {
  if (type === 'dotted') return 'dotted';
  if (type === 'dashed' || Array.isArray(type)) return 'dashed';
  return 'solid';
}

function axisRange(extent: [number, number] | null, axis: unknown): Pick<ChartAxisSpec, 'min' | 'max'> {
  if (extent) return { min: extent[0], max: extent[1] };
  const { min, max } = (axis ?? {}) as { min?: unknown; max?: unknown };
  return { min: numberOrNull(min), max: numberOrNull(max) };
}

/** The points a series marks: where its symbol size function gives a visible symbol. */
function markedPoints(entry: SeriesLike): number[] {
  if (!entry.showSymbol || typeof entry.symbolSize !== 'function' || !Array.isArray(entry.data)) return [];
  const size = entry.symbolSize as (value: unknown, params: { dataIndex: number }) => unknown;
  return entry.data.flatMap((value, dataIndex) => (Number(size(value, { dataIndex })) > 0 ? [dataIndex] : []));
}

function lineChartSpec(option: EChartsOption, tokens: ChartTokens, title: string, subtitle: string | undefined, extent: AxisExtent | undefined, hidden: ReadonlySet<string>): LineChartSpec {
  const labels = imageLabelsOf(option);
  const xAxis = firstOf(option.xAxis as unknown) as { type?: string; min?: unknown; max?: unknown } | undefined;
  const yAxes = (Array.isArray(option.yAxis) ? option.yAxis : [option.yAxis]) as unknown[];
  const log = xAxis?.type === 'log';
  const bands: NonNullable<LineChartSpec['bands']> = [];
  const hlines: NonNullable<LineChartSpec['hlines']> = [];
  const series: LineSeriesSpec[] = [];
  seriesList(option).forEach((entry, index) => {
    const axis: 0 | 1 = entry.yAxisIndex === 1 ? 1 : 0;
    for (const [from, to] of entry.markArea?.data ?? []) {
      if (finite(from?.xAxis) && finite(to?.xAxis)) bands.push({ x0: from.xAxis, x1: to.xAxis, ...(from.name ? { label: from.name } : {}) });
    }
    for (const line of entry.markLine?.data ?? []) {
      if (finite(line?.yAxis)) hlines.push({ y: line.yAxis, axis });
    }
    if (entry.type !== 'line' || !Array.isArray(entry.data) || entry.lineStyle?.opacity === 0) return;
    if (entry.name !== undefined && hidden.has(entry.name)) return;
    const points = entry.data.map(pointOf);
    const markers = markedPoints(entry);
    series.push({
      label: entry.name ?? null,
      x: points.map(([x]) => numberOrNull(x)),
      y: points.map(([, y]) => numberOrNull(y)),
      axis,
      color: colorRole(entry.lineStyle?.color ?? entry.itemStyle?.color, tokens, index),
      style: lineStyleOf(entry.lineStyle?.type),
      width: entry.lineStyle?.width ?? 2,
      opacity: entry.lineStyle?.opacity ?? 1,
      ...(finite(entry.z) ? { z: entry.z + 2 } : {}),
      ...(markers.length ? { markers } : {}),
    });
  });
  const secondary = series.some(({ axis }) => axis === 1);
  return {
    kind: 'lines',
    title,
    ...(subtitle ? { subtitle } : {}),
    x: { label: 'Frequency [Hz]', scale: log ? 'log' : 'linear', ...axisRange(extent?.('x', 0) ?? null, xAxis) },
    y: { label: labels.y ?? '', ...axisRange(extent?.('y', 0) ?? null, yAxes[0]) },
    ...(secondary ? { y2: { label: labels.y2 ?? '', ...axisRange(extent?.('y', 1) ?? null, yAxes[1]) } } : {}),
    series,
    ...(bands.length ? { bands } : {}),
    ...(hlines.length ? { hlines } : {}),
  };
}

function polarChartSpec(option: EChartsOption, tokens: ChartTokens, title: string, subtitle: string | undefined, hidden: ReadonlySet<string>): PolarChartSpec {
  const radius = (firstOf(option.radiusAxis as unknown) ?? {}) as { min?: unknown; max?: unknown; interval?: unknown };
  const shade: NonNullable<PolarChartSpec['shade']> = [];
  const series: PolarSeriesSpec[] = [];
  seriesList(option).forEach((entry, index) => {
    if (!Array.isArray(entry.data)) return;
    const points = entry.data.map(pointOf);
    // A filled, line-less series is a shaded sector (the unmodelled rear half).
    if (entry.areaStyle && entry.lineStyle?.opacity === 0) {
      const angles = points.map(([, angle]) => angle).filter(finite);
      if (angles.length) shade.push({ from: Math.min(...angles), to: Math.max(...angles), ...(entry.name ? { label: entry.name } : {}) });
      return;
    }
    if (entry.name !== undefined && hidden.has(entry.name)) return;
    series.push({
      label: entry.name ?? null,
      angle: points.map(([, angle]) => numberOrNull(angle)),
      r: points.map(([level]) => numberOrNull(level)),
      color: colorRole(entry.lineStyle?.color ?? entry.itemStyle?.color, tokens, index),
      style: lineStyleOf(entry.lineStyle?.type),
      width: entry.lineStyle?.width ?? 2,
      opacity: entry.lineStyle?.opacity ?? 1,
    });
  });
  const min = numberOrNull(radius.min) ?? -30;
  const max = numberOrNull(radius.max) ?? 0;
  return {
    kind: 'polar',
    title,
    ...(subtitle ? { subtitle } : {}),
    r: { min, max, ...(finite(radius.interval) ? { step: radius.interval } : {}), unit: 'dB' },
    series,
    ...(shade.length ? { shade } : {}),
  };
}

/**
 * The spec for a chart a card has drawn.
 *
 * `extent` reports the axis ranges on screen, zoom included, so the image frames
 * the same part of the curves; without it the option's own bounds are used.
 * The subtitle on the option (a polar card's plane and frequency) wins over the
 * card's, because it says what this particular drawing is a slice of.
 */
export function chartSpecFromOption(
  option: EChartsOption,
  tokens: ChartTokens,
  { title, subtitle, extent, hidden = new Set() }: { title: string; subtitle?: string | null; extent?: AxisExtent; hidden?: ReadonlySet<string> },
): ChartSpec {
  const own = imageLabelsOf(option).subtitle;
  const caption = [own, subtitle].filter(Boolean).join(' · ') || undefined;
  return option.polar
    ? polarChartSpec(option, tokens, title, caption, hidden)
    : lineChartSpec(option, tokens, title, caption, extent, hidden);
}
