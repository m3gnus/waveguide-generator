import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { createPortal } from 'react-dom';
import type { EChartsOption } from 'echarts';
import { jobsSocket } from '../api/jobsSocket';
import { compareSelection, fetchJobResults, type JobResults } from '../api/results';
import { useDesignStore } from '../stores/design';
import { EChart, useChartTokens, type ChartTokens } from '../results/EChart';
import { beamShapeSeries, directivityGrid, directivityIndexSeries, impedanceSeries, splSeries, type NamedResult } from '../results/mappers';
import { BalloonRenderer, ChartStub, ForwardBeamRenderer } from '../results/balloon';
import { runExportBundle } from '../results/exporters';
import type { ResultPayload } from '../results/types';
import { CHART_TYPES, MAX_RESULT_PANELS, RESULT_PANEL_COUNTS, preferencesStore, usePreferences, type ChartType } from '../prefs/preferences';
import { ResultsPreferencesSurface } from '../prefs/PreferencesSurface';
import { Icon } from './icons';
import { trapDialogFocus } from './SettingsDialog';

function frequency(value: number | undefined): string {
  if (!value) return '—';
  return value >= 1_000 ? `${(value / 1_000).toFixed(value >= 10_000 ? 1 : 2)} kHz` : `${Math.round(value)} Hz`;
}

export function splSubtitle(result: JobResults | undefined): string {
  const observation = result?.metadata?.observation;
  const record = observation && typeof observation === 'object' ? observation as Record<string, unknown> : {};
  const distance = Number(record.effective_distance_m ?? record.requested_distance_m);
  return Number.isFinite(distance) && distance > 0 ? `absolute · ${Number(distance.toPrecision(4))} m` : 'absolute · distance unspecified';
}

function labelFor(id: string, jobs: ReturnType<typeof jobsSocket.getSnapshot>['jobs']): string {
  const job = jobs.find((item) => item.id === id);
  return job?.label || `${String(job?.config_summary.formula_type ?? 'job').toLowerCase()} ${id.slice(0, 6)}`;
}

/**
 * How much chrome a chart can afford. A card in the six-panel dock is barely
 * 100px tall: spending 20px on a legend row and 27px on a repeated
 * "Frequency [Hz]" caption leaves single digits of actual plot. Axis titles and
 * padding are therefore scaled to the panel, and the identifying labels the
 * compact charts drop are carried by the card's own title chip instead.
 */
export type ChartDensity = 'compact' | 'regular' | 'full';

export function chartDensity(width: number, height: number): ChartDensity {
  if (height >= 300 && width >= 440) return 'full';
  if (height >= 172 && width >= 300) return 'regular';
  return 'compact';
}

const LABEL_FONT: Record<ChartDensity, number> = { compact: 9, regular: 10, full: 11 };
/** `top` is the band reserved for the floating title chip and the legend. */
const LINE_GRID: Record<ChartDensity, { left: number; right: number; top: number; bottom: number }> = {
  compact: { left: 30, right: 8, top: 16, bottom: 17 },
  regular: { left: 40, right: 10, top: 20, bottom: 22 },
  full: { left: 54, right: 16, top: 30, bottom: 42 },
};
const MAP_GRID: Record<ChartDensity, { left: number; right: number; top: number; bottom: number }> = {
  compact: { left: 27, right: 23, top: 16, bottom: 16 },
  regular: { left: 34, right: 32, top: 20, bottom: 22 },
  full: { left: 46, right: 46, top: 14, bottom: 38 },
};
/** Colour ramp height, kept inside the shortest card it can appear in. */
const MAP_RAMP: Record<ChartDensity, number> = { compact: 38, regular: 74, full: 150 };
/** Legend clears the hover-revealed expand/close buttons in the same band. */
const LEGEND_INSET = 46;

function axes(tokens: ChartTokens, density: ChartDensity = 'regular') {
  return {
    axisLine: { lineStyle: { color: tokens.spine ?? tokens.grid } },
    axisTick: { lineStyle: { color: tokens.muted } },
    // Log-frequency ticks crowd as soon as a card narrows; dropping the
    // colliding ones beats printing "4001,000".
    axisLabel: { color: tokens.muted, fontSize: LABEL_FONT[density], margin: density === 'compact' ? 5 : 8, hideOverlap: true },
    splitLine: { lineStyle: { color: tokens.grid, width: .7 } },
    minorSplitLine: { show: true, lineStyle: { color: tokens.gridMinor, width: .5 } },
  };
}

type CartesianSeries = { data?: Array<number[] | { value?: number[] }> };

export function frequencyBounds(series: EChartsOption['series']): [number, number] | undefined {
  const frequencies = (Array.isArray(series) ? series : [series])
    .flatMap((item) => ((item as CartesianSeries | undefined)?.data ?? []))
    .map((item) => Number(Array.isArray(item) ? item[0] : item.value?.[0]))
    .filter((value) => Number.isFinite(value) && value > 0);
  return frequencies.length ? [Math.min(...frequencies), Math.max(...frequencies)] : undefined;
}

export function lineOption(series: EChartsOption['series'], tokens: ChartTokens, yName: string, density: ChartDensity = 'regular'): EChartsOption {
  const bounds = frequencyBounds(series);
  const inset = LINE_GRID[density];
  return {
    animationDuration: 180,
    backgroundColor: tokens.background,
    color: tokens.series,
    textStyle: { color: tokens.foreground, fontFamily: 'Inter, system-ui, sans-serif' },
    tooltip: { trigger: 'axis', confine: true, backgroundColor: tokens.background, borderColor: tokens.spine ?? tokens.grid, textStyle: { color: tokens.foreground, fontSize: 10 }, axisPointer: { type: 'cross', lineStyle: { color: tokens.muted } } },
    // Compact legends truncate rather than crowd the title chip, and scroll in
    // a single row rather than wrapping down over the plot on a narrow card.
    // The tooltip and the detail view still name every series in full.
    legend: {
      ...(density === 'compact' ? { type: 'scroll' as const, width: '46%', pageIconSize: 7, pageIconColor: tokens.muted, pageIconInactiveColor: tokens.grid, pageTextStyle: { color: tokens.muted, fontSize: 8 } } : {}),
      top: density === 'full' ? 2 : 1,
      right: density === 'full' ? 8 : LEGEND_INSET,
      textStyle: { color: tokens.muted, fontSize: density === 'compact' ? 8 : 9, ...(density === 'compact' ? { width: 32, overflow: 'truncate' as const } : {}) },
      itemWidth: density === 'compact' ? 10 : 14,
      itemHeight: 2,
      itemGap: density === 'compact' ? 6 : 10,
    },
    grid: { ...inset, containLabel: false },
    // The tick labels already read as frequencies; the caption only earns its
    // 20px on a chart big enough to spare them.
    xAxis: { type: 'log', logBase: 10, min: bounds?.[0], max: bounds?.[1], ...(density === 'full' ? { name: 'Frequency [Hz]', nameLocation: 'middle' as const, nameGap: 24, nameTextStyle: { color: tokens.muted, fontSize: 10 } } : {}), minorTick: { show: true }, ...axes(tokens, density) },
    // In-grid cards carry the unit in their title chip, which occupies exactly
    // the top-left corner an axis name would be drawn into.
    yAxis: { type: 'value', ...(density === 'full' ? { name: yName, nameGap: 10, nameTextStyle: { color: tokens.muted, fontSize: 10, align: 'left' as const } } : {}), ...axes(tokens, density) },
    dataZoom: [{ type: 'inside', xAxisIndex: 0, filterMode: 'none', zoomOnMouseWheel: 'ctrl', moveOnMouseWheel: true }],
    series,
  };
}

function splOption(items: NamedResult[], tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing'], density: ChartDensity): EChartsOption {
  return lineOption(splSeries(items, smoothing).map((series, index) => ({ ...series, lineStyle: { width: index ? 1.2 : 2, type: index ? 'dashed' : 'solid' } })), tokens, 'dB SPL', density);
}

export function heatmapFrequencyLabel(value: number): string {
  if (!Number.isFinite(value)) return '';
  if (value >= 1_000) return `${Number((value / 1_000).toPrecision(3))}k`;
  return String(Number(value.toPrecision(3)));
}

export interface InterpolatedDirectivityGrid {
  frequencies: number[];
  angles: number[];
  values: Array<Array<number | null>>;
  factor: number;
}

const MAX_INTERPOLATED_CELLS = 50_000;

/** Bilinear interpolation in log-frequency/index space for a smooth live map. */
export function interpolateDirectivityGrid(result: ResultPayload, plane: string, requestedFactor = 4): InterpolatedDirectivityGrid {
  const source = directivityGrid(result, plane);
  if (!source.frequencies.length || !source.angles.length) return { frequencies: [], angles: [], values: [], factor: 1 };
  const sourceValues = Array.from({ length: source.angles.length }, () => Array<number | null>(source.frequencies.length).fill(null));
  const columnOf = new Map(source.frequencies.map((frequency, index) => [frequency, index]));
  const rowOf = new Map(source.angles.map((angle, index) => [angle, index]));
  source.data.forEach(([frequency, angle, value]) => {
    const column = columnOf.get(frequency);
    const row = rowOf.get(angle);
    if (column !== undefined && row !== undefined) sourceValues[row][column] = value;
  });
  let factor = Math.max(1, Math.floor(requestedFactor));
  const cellCount = (candidate: number) => ((source.frequencies.length - 1) * candidate + 1) * ((source.angles.length - 1) * candidate + 1);
  while (factor > 1 && cellCount(factor) > MAX_INTERPOLATED_CELLS) factor -= 1;
  const columns = (source.frequencies.length - 1) * factor + 1;
  const rows = (source.angles.length - 1) * factor + 1;
  const interpolateAxis = (values: number[], position: number, logarithmic = false) => {
    if (values.length === 1) return values[0];
    const left = Math.min(values.length - 2, Math.floor(position / factor));
    const t = Math.min(1, (position - left * factor) / factor);
    if (logarithmic && values[left] > 0 && values[left + 1] > 0) return values[left] * ((values[left + 1] / values[left]) ** t);
    return values[left] + (values[left + 1] - values[left]) * t;
  };
  const frequencies = Array.from({ length: columns }, (_, column) => interpolateAxis(source.frequencies, column, true));
  const angles = Array.from({ length: rows }, (_, row) => interpolateAxis(source.angles, row));
  const values = Array.from({ length: rows }, (_, row) => Array.from({ length: columns }, (_, column) => {
    if (source.frequencies.length === 1 && source.angles.length === 1) return sourceValues[0][0];
    const x0 = Math.min(source.frequencies.length - 1, Math.floor(column / factor));
    const y0 = Math.min(source.angles.length - 1, Math.floor(row / factor));
    const x1 = Math.min(source.frequencies.length - 1, x0 + 1);
    const y1 = Math.min(source.angles.length - 1, y0 + 1);
    const tx = x0 === x1 ? 0 : (column - x0 * factor) / factor;
    const ty = y0 === y1 ? 0 : (row - y0 * factor) / factor;
    const samples: Array<[number | null, number]> = [
      [sourceValues[y0][x0], (1 - tx) * (1 - ty)],
      [sourceValues[y0][x1], tx * (1 - ty)],
      [sourceValues[y1][x0], (1 - tx) * ty],
      [sourceValues[y1][x1], tx * ty],
    ];
    const usable = samples.filter((sample): sample is [number, number] => sample[0] !== null && Number.isFinite(sample[0]) && sample[1] > 0);
    const weight = usable.reduce((sum, sample) => sum + sample[1], 0);
    return weight ? usable.reduce((sum, sample) => sum + sample[0] * sample[1], 0) / weight : null;
  }));
  return { frequencies, angles, values, factor };
}

type ContourSegment = [number, number, number, number];

/** Small marching-squares pass used for labeled engineering reference lines. */
export function contourSegments(values: Array<Array<number | null>>, level: number): ContourSegment[] {
  const segments: ContourSegment[] = [];
  const crossing = (a: number, b: number, x1: number, y1: number, x2: number, y2: number): [number, number] | null => {
    if (!((a < level && b >= level) || (a >= level && b < level))) return null;
    const t = (level - a) / (b - a);
    return [x1 + (x2 - x1) * t, y1 + (y2 - y1) * t];
  };
  for (let row = 0; row < values.length - 1; row += 1) {
    for (let column = 0; column < (values[row]?.length ?? 0) - 1; column += 1) {
      const topLeft = values[row][column];
      const topRight = values[row][column + 1];
      const bottomRight = values[row + 1][column + 1];
      const bottomLeft = values[row + 1][column];
      if ([topLeft, topRight, bottomRight, bottomLeft].some((value) => value === null || !Number.isFinite(value))) continue;
      const points = [
        crossing(topLeft!, topRight!, column, row, column + 1, row),
        crossing(topRight!, bottomRight!, column + 1, row, column + 1, row + 1),
        crossing(bottomLeft!, bottomRight!, column, row + 1, column + 1, row + 1),
        crossing(topLeft!, bottomLeft!, column, row, column, row + 1),
      ].filter((point): point is [number, number] => point !== null);
      if (points.length >= 2) segments.push([points[0][0], points[0][1], points[1][0], points[1][1]]);
      if (points.length === 4) segments.push([points[2][0], points[2][1], points[3][0], points[3][1]]);
    }
  }
  return segments;
}

type ContourPoint = [number, number];

/** Join marching-squares fragments into continuous paths so contour lines can
 * be rounded and anti-aliased as curves rather than drawn as tiny segments. */
export function contourPolylines(segments: ContourSegment[]): ContourPoint[][] {
  // Endpoints are matched through a hash rather than by rescanning the
  // remaining segments on every join. That scan made this quadratic: measured
  // 1.4 ms at 200 segments, 13 ms at 1,400 and 62 ms at 3,000 -- and it runs
  // once per contour level, four levels per heatmap. The bucket key quantises
  // to the same 1e-7 tolerance `close` uses, and neighbouring buckets are
  // still checked so a pair straddling a bucket boundary cannot be missed.
  const QUANTUM = 1e-7;
  const key = (x: number, y: number) => `${Math.round(x / QUANTUM)}:${Math.round(y / QUANTUM)}`;
  const close = (a: ContourPoint, b: ContourPoint) =>
    Math.abs(a[0] - b[0]) < QUANTUM && Math.abs(a[1] - b[1]) < QUANTUM;

  const buckets = new Map<string, number[]>();
  const add = (x: number, y: number, index: number) => {
    const bucket = buckets.get(key(x, y));
    if (bucket) bucket.push(index);
    else buckets.set(key(x, y), [index]);
  };
  segments.forEach((segment, index) => {
    add(segment[0], segment[1], index);
    add(segment[2], segment[3], index);
  });

  const used = new Array<boolean>(segments.length).fill(false);
  const candidatesAt = (point: ContourPoint): number[] => {
    const found: number[] = [];
    const [cx, cy] = [Math.round(point[0] / QUANTUM), Math.round(point[1] / QUANTUM)];
    for (let dx = -1; dx <= 1; dx += 1) {
      for (let dy = -1; dy <= 1; dy += 1) {
        const bucket = buckets.get(`${cx + dx}:${cy + dy}`);
        if (bucket) found.push(...bucket);
      }
    }
    return found;
  };

  const polylines: ContourPoint[][] = [];
  for (let first = 0; first < segments.length; first += 1) {
    if (used[first]) continue;
    used[first] = true;
    const segment = segments[first];
    const points: ContourPoint[] = [[segment[0], segment[1]], [segment[2], segment[3]]];
    let joined = true;
    while (joined) {
      joined = false;
      for (const end of [true, false]) {
        const anchor = end ? points.at(-1)! : points[0];
        for (const index of candidatesAt(anchor)) {
          if (used[index]) continue;
          const candidate = segments[index];
          const start: ContourPoint = [candidate[0], candidate[1]];
          const finish: ContourPoint = [candidate[2], candidate[3]];
          if (end) {
            if (close(anchor, start)) points.push(finish);
            else if (close(anchor, finish)) points.push(start);
            else continue;
          } else if (close(anchor, finish)) points.unshift(start);
          else if (close(anchor, start)) points.unshift(finish);
          else continue;
          used[index] = true;
          joined = true;
          break;
        }
        if (joined) break;
      }
    }
    polylines.push(points);
  }
  return polylines;
}

/**
 * ECharts only draws cartesian heatmap cells on two *category* axes. On a log
 * or value axis every cell is emitted with an empty path — nothing renders, and
 * the guard that says so is compiled out of production builds, so the chart
 * fails silently. Index the cells against category axes instead. The frequency
 * axis still reads logarithmically because the sweep itself is log-spaced.
 */
export function heatmapOption(result: ResultPayload, tokens: ChartTokens, plane: string, mapReference: number, density: ChartDensity = 'regular'): EChartsOption {
  const grid = interpolateDirectivityGrid(result, plane);
  const floor = mapReference * 5;
  const inset = MAP_GRID[density];
  const cells = grid.values.flatMap((rowValues, row) => rowValues.flatMap((level, column) => level === null ? [] : [[column, row, Math.max(floor, Math.min(0, level)), level, grid.frequencies[column], grid.angles[row]]]));
  const categoryAxis = { ...axes(tokens, density), axisTick: { alignWithLabel: true }, axisLabel: { ...axes(tokens, density).axisLabel, hideOverlap: true } };
  const contourLevels = [...new Set([-3, -6, -12, mapReference])].filter((level) => level >= floor).sort((a, b) => b - a);
  const contourSeries = contourLevels.flatMap((level, contourIndex) => {
    const polylines = contourPolylines(contourSegments(grid.values, level)).filter((points) => points.length > 1);
    if (!polylines.length) return [];
    const labelIndex = polylines.reduce((best, points, index) => points.length > polylines[best].length ? index : best, 0);
    const color = contourIndex === 0 ? tokens.foreground : contourIndex === 1 ? tokens.accent : tokens.series[Math.min(contourIndex, tokens.series.length - 1)] ?? tokens.muted;
    return [{
      name: `${level} dB contour`, type: 'custom', coordinateSystem: 'cartesian2d', silent: true, z: 6, clip: true,
      data: polylines.map((_points, index) => [index, index === labelIndex ? 1 : 0]),
      renderItem: (_params: unknown, api: { value: (index: number) => unknown; coord: (value: number[]) => number[] }) => {
        const points = polylines[Number(api.value(0))].map((point) => api.coord(point));
        const middle = points[Math.floor(points.length / 2)];
        const children: Array<Record<string, unknown>> = [{ type: 'polyline', shape: { points, smooth: .22 }, style: { fill: null, stroke: color, lineWidth: level === mapReference ? 1.6 : 1.05, opacity: .92, lineCap: 'round', lineJoin: 'round', lineDash: level <= -12 ? [4, 3] : undefined } }];
        if (Number(api.value(1))) children.push({ type: 'text', style: { x: middle[0] + 3, y: middle[1] - 3, text: `${level} dB`, fill: color, font: '8px ui-monospace, monospace', backgroundColor: tokens.background, padding: [1, 2], borderRadius: 2 } });
        return { type: 'group', children };
      },
    }];
  });
  return {
    animationDuration: 180,
    backgroundColor: tokens.background,
    textStyle: { color: tokens.foreground, fontFamily: 'Inter, system-ui, sans-serif' },
    tooltip: { trigger: 'item', confine: true, backgroundColor: tokens.background, borderColor: tokens.spine ?? tokens.grid, textStyle: { color: tokens.foreground, fontSize: 10 }, formatter: (params) => {
      const [, , , level, frequencyValue, angleValue] = (Array.isArray(params) ? params[0] : params).value as number[];
      return `${heatmapFrequencyLabel(frequencyValue)}Hz · ${Number(angleValue.toFixed(2))}° · ${level.toFixed(1)} dB`;
    } },
    grid: inset,
    xAxis: { type: 'category', data: grid.frequencies.map((frequency) => heatmapFrequencyLabel(frequency)), ...(density === 'full' ? { name: 'Frequency [Hz]', nameLocation: 'middle' as const, nameGap: 22, nameTextStyle: { color: tokens.muted, fontSize: 10 } } : {}), ...categoryAxis, axisLabel: { ...categoryAxis.axisLabel, interval: Math.max(0, grid.factor * 2 - 1) } },
    yAxis: { type: 'category', data: grid.angles.map((angle) => String(Number(angle.toFixed(2)))), ...(density === 'full' ? { name: 'Angle [°]', nameGap: 10, nameTextStyle: { color: tokens.muted, fontSize: 10, align: 'left' as const } } : {}), ...categoryAxis, axisLabel: { ...categoryAxis.axisLabel, interval: Math.max(0, grid.factor * 2 - 1) } },
    visualMap: { min: floor, max: 0, dimension: 2, seriesIndex: 0, right: 2, top: 'middle', itemWidth: density === 'compact' ? 6 : 8, itemHeight: MAP_RAMP[density], text: ['0', `${floor}`], textStyle: { color: tokens.muted, fontSize: density === 'compact' ? 7 : 8 }, inRange: { color: tokens.colormap } },
    // Cartesian heatmaps do not chunk safely in ECharts: progressive mode can
    // stop after the first angle band and leave the rest of the map blank.
    series: [{ type: 'heatmap', progressive: 0, z: 1, data: cells, emphasis: { itemStyle: { borderColor: tokens.accent, borderWidth: 1.2, shadowBlur: 7, shadowColor: tokens.accent } } }, ...contourSeries] as EChartsOption['series'],
  };
}

function impedanceOption(result: ResultPayload, tokens: ChartTokens, smoothing: ReturnType<typeof usePreferences>['smoothing'], density: ChartDensity): EChartsOption {
  return { ...lineOption(impedanceSeries(result, 'cartesian', smoothing), tokens, 'Z/ρc', density), color: [tokens.series[0], tokens.series[1]] };
}

function chartLabel(chartType: ChartType): string {
  return CHART_TYPES.find(({ id }) => id === chartType)?.label ?? chartType;
}

/**
 * Names and units for the floating title chip. Both are far shorter than the
 * menu labels, which would truncate into the legend; the unit matters most,
 * because compact charts drop their axis titles and this states them instead.
 */
const CHART_BADGES: Record<ChartType, { short: string; long?: string; unit?: string }> = {
  frequency_response: { short: 'SPL', long: 'On-axis SPL', unit: 'dB' },
  directivity_map_h: { short: 'Dir H', long: 'Directivity H', unit: 'dB' },
  directivity_map_v: { short: 'Dir V', long: 'Directivity V', unit: 'dB' },
  directivity_map: { short: 'Dir', long: 'Directivity', unit: 'dB' },
  directivity_index: { short: 'DI', long: 'Directivity index', unit: 'dB' },
  beam_shape: { short: 'Beam', long: 'Beam width', unit: '°' },
  beam_map: { short: 'Beam map' },
  balloon: { short: 'Balloon' },
  impedance: { short: 'Impedance', unit: 'Z/ρc' },
  summary: { short: 'Summary' },
};

/** Longer name when the card can carry it, short name when it cannot. */
export function chartTitle(chartType: ChartType, wide: boolean): string {
  const badge = CHART_BADGES[chartType];
  if (!badge) return chartType;
  return wide ? badge.long ?? badge.short : badge.short;
}

export function resolvedPolarStepNotice(result: JobResults): string | null {
  const grid = result.metadata?.polar_grid;
  if (!grid || typeof grid !== 'object') return null;
  const record = grid as Record<string, unknown>;
  const requested = Number(record.requested_step);
  const resolved = Number(record.resolved_step);
  if (!Number.isFinite(requested) || !Number.isFinite(resolved)) return null;
  if (Math.abs(requested - resolved) <= Math.max(1e-9, Math.abs(requested) * 1e-7)) return null;
  return `${Number(resolved.toPrecision(6))}° resolved (requested ${Number(requested.toPrecision(6))}°)`;
}

function Summary({ result }: { result: ResultPayload }) {
  const warnings = Array.isArray(result.metadata?.warnings) ? result.metadata.warnings.length : Number(result.metadata?.warning_count ?? 0);
  const cells: Array<[string, string | number]> = [
    ['Frequencies', result.frequencies.length],
    ['Range', result.frequencies.length ? `${frequency(result.frequencies[0])} – ${frequency(result.frequencies.at(-1))}` : '—'],
    ['Planes', Object.keys(result.directivity ?? {}).join(' · ') || 'none'],
    ['Balloon', result.balloon?.spl_norm_db.length ?? 0],
    ['Warnings', warnings],
    ['Contract', String(result.metadata?.result_contract_version ?? 'legacy')],
  ];
  return <dl className="result-summary">{cells.map(([label, value]) => <div key={label}><dt>{label}</dt><dd>{value}</dd></div>)}</dl>;
}

const NO_NAMED_RESULTS: NamedResult[] = [];

function ResultChart({ chartType, result, named, tokens, density }: { chartType: ChartType; result: ResultPayload; named: NamedResult[]; tokens: ChartTokens; density: ChartDensity }) {
  const preferences = usePreferences();
  // Only the frequency-response overlay reads the cross-job list. Keeping it in
  // the dependency array for every other chart meant a heatmap -- the most
  // expensive option to build, and the one ECharts rebuilds with notMerge --
  // was recomputed whenever any job's identity changed, which during a solve is
  // several times a second.
  const overlays = chartType === 'frequency_response' ? named : NO_NAMED_RESULTS;
  return useMemo(() => {
    if (chartType === 'frequency_response') return result.spl_on_axis?.spl?.length ? <EChart option={splOption(overlays, tokens, preferences.smoothing, density)} label="Interactive HornLab sound pressure frequency response"/> : <ChartStub reason="Frequency Response needs spl_on_axis data from a completed solve."/>;
    if (chartType === 'directivity_map_h' || chartType === 'directivity_map_v') {
      const plane = chartType.endsWith('_v') ? 'vertical' : 'horizontal';
      return result.directivity?.[plane]?.length ? <EChart option={heatmapOption(result, tokens, plane, preferences.mapReference, density)} label={`Interactive HornLab ${plane} directivity heatmap`}/> : <ChartStub reason={`Directivity Map (${plane === 'horizontal' ? 'H' : 'V'}) needs the ${plane} polar plane in the result payload.`}/>;
    }
    if (chartType === 'directivity_map') {
      const directivity = result.directivity as Record<string, unknown[]> | undefined;
      const planes = Object.keys(directivity ?? {}).filter((plane) => directivity?.[plane]?.length);
      return planes.length ? <div className="directivity-multiplane">{planes.map((plane) => <div key={plane}><span>{plane}</span><EChart option={heatmapOption(result, tokens, plane, preferences.mapReference, density)} label={`Interactive ${plane} directivity heatmap`}/></div>)}</div> : <ChartStub reason="Directivity Map needs at least one polar plane in the result payload."/>;
    }
    if (chartType === 'directivity_index') {
      const series = directivityIndexSeries(result, preferences.smoothing);
      return series.length ? <EChart option={lineOption(series, tokens, 'DI [dB]', density)} label="Interactive HornLab directivity index by frequency"/> : <ChartStub reason="Directivity Index needs the optional di result block."/>;
    }
    if (chartType === 'beam_shape') return result.beam_shape?.frequencies?.length ? <EChart option={lineOption(beamShapeSeries(result), tokens, 'Beam width [°]', density)} label="Interactive HornLab horizontal and vertical forward beam width"/> : <ChartStub reason="Forward Beam Shape needs spherical balloon sampling and a valid −6 dB contour fit."/>;
    if (chartType === 'beam_map') return <ForwardBeamRenderer result={result}/>;
    if (chartType === 'balloon') return <BalloonRenderer result={result}/>;
    if (chartType === 'impedance') return result.impedance?.frequencies?.length ? <EChart option={impedanceOption(result, tokens, preferences.smoothing, density)} label="Interactive HornLab normalized acoustic impedance by frequency"/> : <ChartStub reason="Acoustic Impedance needs the optional impedance result block."/>;
    return <Summary result={result}/>;
  }, [chartType, density, overlays, preferences.mapReference, preferences.smoothing, result, tokens]);
}

export interface CardMetrics {
  density: ChartDensity;
  /** Whether the card is wide enough for the chart's full name. */
  wide: boolean;
}

/**
 * Watches a card's box and reports only the two derived, discrete values the
 * chart cares about. Collapsing to an enum keeps drag-resizing from rebuilding
 * an ECharts option on every animation frame.
 */
export function useCardMetrics(target: React.RefObject<HTMLElement | null>): CardMetrics {
  const [metrics, setMetrics] = useState<CardMetrics>({ density: 'regular', wide: true });
  useEffect(() => {
    const element = target.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (!width || !height) return;
      const next: CardMetrics = { density: chartDensity(width, height), wide: width >= 300 };
      setMetrics((previous) => previous.density === next.density && previous.wide === next.wide ? previous : next);
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [target]);
  return metrics;
}

function ChartCard({ index, chartType, result, named, tokens }: { index: number; chartType: ChartType; result: ResultPayload; named: NamedResult[]; tokens: ChartTokens }) {
  const [expanded, setExpanded] = useState(false);
  const detail = useRef<HTMLElement>(null);
  const card = useRef<HTMLElement>(null);
  const { density, wide } = useCardMetrics(card);
  useEffect(() => {
    if (!expanded) return;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focus = requestAnimationFrame(() => detail.current?.querySelector<HTMLElement>('button, select, [tabindex]:not([tabindex="-1"])')?.focus());
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        setExpanded(false);
        return;
      }
      trapDialogFocus(detail, event);
    };
    window.addEventListener('keydown', close);
    return () => {
      cancelAnimationFrame(focus);
      window.removeEventListener('keydown', close);
      previous?.focus();
    };
  }, [expanded]);
  const polarStep = chartType.startsWith('directivity_map') ? resolvedPolarStepNotice(result) : null;
  const subtitle = chartType.startsWith('directivity_map') ? `ref ${preferencesStore.getSnapshot().mapReference} dB${polarStep ? ` · ${polarStep}` : ''}` : chartType === 'frequency_response' ? splSubtitle(result) : null;
  const unit = CHART_BADGES[chartType]?.unit;
  return <>
    <section ref={card} className={`result-card result-${index}`} data-density={density}>
      <div className="chart-placeholder" title="Hover for values · double-click for detail" onDoubleClick={() => setExpanded(true)}>
        <ResultChart chartType={chartType} result={result} named={named} tokens={tokens} density={density}/>
      </div>
      {/* Chrome floats over the plot rather than reserving a row of its own:
          a fixed header costs a quarter of a six-panel card's height. */}
      <div className="result-chrome">
        <span className="result-title">
          <b>{chartTitle(chartType, wide)}</b>
          {unit && <em>{unit}</em>}
          <Icon name="caret"/>
          <select aria-label={`Panel ${index + 1} chart type`} value={chartType} onChange={(event) => preferencesStore.setChartType(index, event.target.value as ChartType)}>{CHART_TYPES.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}</select>
        </span>
        {subtitle && density !== 'compact' && <span className="result-subtitle">{subtitle}</span>}
        <span className="result-chrome-spacer"/>
        <button className="result-card-expand" aria-label={`Expand panel ${index + 1}`} title="Open detail view" onClick={() => setExpanded(true)}><Icon name="expand"/></button>
        <button className="result-card-close" aria-label={`Close panel ${index + 1}`} title="Close chart" onClick={() => preferencesStore.closeChart(index)}><Icon name="close"/></button>
      </div>
    </section>
    {expanded && createPortal(<div className="result-detail-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setExpanded(false); }}>
      <section ref={detail} className="result-detail" role="dialog" aria-modal="true" aria-label={`${chartLabel(chartType)} detail`}>
        <header><div><b>{chartLabel(chartType)}</b>{subtitle && <span>{subtitle}</span>}</div><small>Hover to inspect · Ctrl/scroll to zoom lines</small><button aria-label="Close detail view" onClick={() => setExpanded(false)}><Icon name="close"/></button></header>
        <div className="result-detail-chart"><ResultChart chartType={chartType} result={result} named={named} tokens={tokens} density="full"/></div>
      </section>
    </div>, document.body)}
  </>;
}

export function resultLayoutClass(count: number): string {
  return `result-layout-${Math.max(0, Math.min(MAX_RESULT_PANELS, Math.floor(count)))}`;
}

export function ResultsChartGrid({ chartTypes, result, named, tokens }: {
  chartTypes: ChartType[];
  result: ResultPayload;
  named: NamedResult[];
  tokens: ChartTokens;
}) {
  if (!chartTypes.length) {
    return <div className="result-grid-empty" role="status"><b>NO CHARTS OPEN</b><span>Add a chart to rebuild the results workspace.</span><button onClick={() => preferencesStore.addChart()}>+ Add chart</button></div>;
  }
  return <div className={`result-grid ${resultLayoutClass(chartTypes.length)}`} data-chart-count={chartTypes.length}>
    {chartTypes.map((chartType, index) => <ChartCard key={`${index}-${chartType}`} index={index} chartType={chartType} result={result} named={named} tokens={tokens}/>) }
  </div>;
}

export function ResultsPanel() {
  const jobs = useSyncExternalStore(jobsSocket.subscribe, jobsSocket.getSnapshot, jobsSocket.getSnapshot).jobs;
  const selection = useSyncExternalStore(compareSelection.subscribe, compareSelection.getSnapshot, compareSelection.getSnapshot);
  const preferences = usePreferences();
  const tokens = useChartTokens();
  const [loaded, setLoaded] = useState<Record<string, ResultPayload>>({});
  const [error, setError] = useState<string | null>(null);
  const [exportStatus, setExportStatus] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);
  const [preferencesOpen, setPreferencesOpen] = useState(false);

  // Jobs arrive newest-first, so the first finished one is the latest solve.
  const latest = useMemo(() => jobs.find((job) => job.status === 'complete' && job.has_results) ?? null, [jobs]);
  useEffect(() => {
    // While following, every solve that finishes takes the primary slot as soon
    // as its results exist — the charts repaint without anyone selecting a job.
    if (selection.following) {
      if ((latest?.id ?? null) !== selection.primary) compareSelection.followLatest(latest?.id ?? null);
      return;
    }
    // A pinned result that no longer exists falls back to following again.
    if (selection.primary && jobs.some((job) => job.id === selection.primary && job.has_results)) return;
    compareSelection.followLatest(latest?.id ?? null);
  }, [jobs, latest, selection.following, selection.primary]);

  const ids = useMemo(() => [selection.primary, ...selection.overlays].filter((id): id is string => Boolean(id)), [selection]);
  useEffect(() => {
    let live = true;
    if (!ids.length) { setLoaded({}); return; }
    setError(null);
    void Promise.all(ids.map(async (id) => [id, await fetchJobResults(id) as ResultPayload] as const)).then((pairs) => { if (live) setLoaded(Object.fromEntries(pairs)); }).catch((reason) => live && setError(reason instanceof Error ? reason.message : String(reason)));
    return () => { live = false; };
  }, [ids.join('|')]);

  const primary = selection.primary ? loaded[selection.primary] : undefined;
  // Keyed on the labels themselves rather than on `jobs`. The jobs array gets a
  // new identity on every progress event of a running solve, and all this needs
  // from it is a display name that changes only when someone renames a job.
  // NUL-joined because labels contain spaces, and nothing a user can type
  // produces a NUL, so the key encodes the list unambiguously.
  const labels = ids.map((id) => labelFor(id, jobs)).join('\u0000');
  const named = useMemo(
    () => ids.flatMap((id, index) => loaded[id] ? [{ id, label: labels.split('\u0000')[index], result: loaded[id] }] : []),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `labels` is the stable projection of `jobs` this needs
    [ids, labels, loaded],
  );
  const available = useMemo(() => jobs.filter((job) => job.status === 'complete' && job.has_results && !ids.includes(job.id)), [ids, jobs]);
  const exportSelected = async () => {
    if (!primary) return;
    setExporting(true); setExportStatus(null);
    try {
      const { design, designRevision } = useDesignStore.getState();
      const result = await runExportBundle({ result: primary, design, designRevision, preferences });
      if (selection.primary && result.files.length) {
        const job = jobs.find(({ id }) => id === selection.primary);
        await jobsSocket.patchMetadata(selection.primary, { exported_files: [...new Set([...(job?.exported_files ?? []), ...result.files])] });
      }
      if (result.files.length) preferencesStore.update({ counter: Math.min(999_999, preferences.counter + 1) });
      setExportStatus(`${result.files.length} file${result.files.length === 1 ? '' : 's'} exported${result.failures.length ? ` · ${result.failures.length} failed: ${result.failures.map(({ format, reason }) => `${format} (${reason})`).join(', ')}` : ''}`);
    } catch (reason) { setExportStatus(reason instanceof Error ? reason.message : String(reason)); }
    finally { setExporting(false); }
  };

  if (!selection.primary && !jobs.some((job) => job.has_results)) return <div className="results-panel panel-scroll">
    <div className="results-toolbar"><span className="spacer"/><button className={`panel-preferences-trigger${preferencesOpen ? ' on' : ''}`} aria-label="Results preferences" aria-expanded={preferencesOpen} title="Results & export preferences" onClick={() => setPreferencesOpen((value) => !value)}><Icon name="settings"/></button></div>
    {preferencesOpen && <ResultsPreferencesSurface popover onClose={() => setPreferencesOpen(false)}/>}<div className="coming-soon" role="status"><b>NO RESULTS</b><span>Run a solve to populate result charts.</span></div>
  </div>;

  return <div className="results-panel panel-scroll">
    <div className="results-toolbar">
      <button
        className={`result-follow${selection.following ? ' on' : ''}`}
        aria-pressed={selection.following}
        title={selection.following ? 'Following the latest solve — charts repaint when results land. Click to pin this result.' : 'Pinned to a chosen result. Click to follow the latest solve again.'}
        onClick={() => selection.following ? compareSelection.setPrimary(selection.primary) : compareSelection.followLatest(latest?.id ?? null)}
      ><i/>{selection.following ? 'Latest' : 'Pinned'}</button>
      {ids.map((id, index) => <button key={id} className={`result-chip ${index ? 'muted' : ''}`} onClick={() => compareSelection.remove(id)} title={`${labelFor(id, jobs)} — remove from comparison`}><i/><span>{labelFor(id, jobs)}</span> ×</button>)}
      <select className="result-compare-add" aria-label="Add comparison result" value="" onChange={(event) => { if (event.target.value) compareSelection.toggleOverlay(event.target.value); }}><option value="">+ compare</option>{available.map((job) => <option key={job.id} value={job.id}>{labelFor(job.id, jobs)}</option>)}</select>
      <span className="spacer"/>
      <label className="result-count-control" title="Number of chart panels">Charts<select aria-label="Results panel count" value={RESULT_PANEL_COUNTS.includes(preferences.chartTypes.length as never) ? preferences.chartTypes.length : ''} onChange={(event) => preferencesStore.setChartCount(Number(event.target.value))}><option value="" disabled>{preferences.chartTypes.length}</option>{RESULT_PANEL_COUNTS.map((count) => <option key={count} value={count}>{count}</option>)}</select></label>
      <button className="toolbar-icon" disabled={preferences.chartTypes.length >= MAX_RESULT_PANELS} aria-label="Add chart" title="Add chart panel" onClick={() => preferencesStore.addChart()}><Icon name="plus"/></button>
      <button disabled={exporting || !primary || !preferences.exportFormats.length} title="Export the current result using the formats enabled in Results preferences" onClick={() => void exportSelected()}>{exporting ? 'Exporting…' : `Export (${preferences.exportFormats.length})`}</button>
      <button className={`panel-preferences-trigger${preferencesOpen ? ' on' : ''}`} aria-label="Results preferences" aria-expanded={preferencesOpen} title="Results & export preferences" onClick={() => setPreferencesOpen((value) => !value)}><Icon name="settings"/></button>
    </div>
    {preferencesOpen && <ResultsPreferencesSurface popover onClose={() => setPreferencesOpen(false)}/>}
    {(error || exportStatus) && <div className={error ? 'job-error' : ''} role="status" style={{ margin: 7, color: error ? undefined : 'var(--fg2)', fontSize: 9 }}>{error ?? exportStatus}</div>}
    {!primary
      ? <div className="coming-soon"><b>LOADING RESULTS</b><span>Fetching selected job data…</span></div>
      : <ResultsChartGrid chartTypes={preferences.chartTypes} result={primary} named={named} tokens={tokens}/>}
  </div>;
}
