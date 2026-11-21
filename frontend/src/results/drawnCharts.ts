import type { EChartsOption } from 'echarts';
import type { AxisExtent } from './chartSpec';

/**
 * The charts on screen, by the element that holds each one.
 *
 * A card's copy and export buttons draw the chart's image from what the card
 * has drawn -- its option and its axis extents -- rather than from its pixels,
 * so the image can come from hornlab-plots in the export theme. The renderer
 * registers itself here; a card finds its chart by looking inside its own box.
 */
export interface DrawnChart {
  /** The option as built, before display smoothing: the solved points. */
  option: EChartsOption;
  extent: AxisExtent;
  /** Series the user switched off in the legend; the image leaves them out too. */
  hidden: () => ReadonlySet<string>;
}

const drawn = new WeakMap<Element, DrawnChart>();

export const DRAWN_CHART_CLASS = 'chart-frame';

export function registerDrawnChart(element: Element, chart: DrawnChart): () => void {
  drawn.set(element, chart);
  return () => {
    if (drawn.get(element) === chart) drawn.delete(element);
  };
}

/** The first chart drawn inside `container`, if any. */
export function drawnChartIn(container: Element): DrawnChart | null {
  for (const element of container.querySelectorAll(`.${DRAWN_CHART_CLASS}`)) {
    const chart = drawn.get(element);
    if (chart) return chart;
  }
  return null;
}

/** The subset of an ECharts instance the extent reading needs. */
export interface ChartInstanceLike {
  getWidth(): number;
  getHeight(): number;
  convertFromPixel?: (finder: { xAxisIndex: number } | { yAxisIndex: number }, value: number) => unknown;
}

/**
 * The data range an axis spans on screen, zoom included.
 *
 * Read through `convertFromPixel` at the grid's edges -- the chart's own public
 * mapping, which handles the log frequency axis and a zoomed window -- rather
 * than from ECharts' internal scale objects.
 */
export function screenAxisExtent(instance: ChartInstanceLike | null, option: EChartsOption): AxisExtent {
  return (axis, index) => {
    const grid = (Array.isArray(option.grid) ? option.grid[0] : option.grid) as { left?: unknown; right?: unknown; top?: unknown; bottom?: unknown } | undefined;
    if (!instance?.convertFromPixel || !grid) return null;
    const inset = [grid.left, grid.right, grid.top, grid.bottom];
    if (!inset.every((value) => typeof value === 'number')) return null;
    const [left, right, top, bottom] = inset as number[];
    const ends = axis === 'x'
      ? [left, instance.getWidth() - right].map((pixel) => instance.convertFromPixel!({ xAxisIndex: index }, pixel))
      : [instance.getHeight() - bottom, top].map((pixel) => instance.convertFromPixel!({ yAxisIndex: index }, pixel));
    if (!ends.every((value) => typeof value === 'number' && Number.isFinite(value))) return null;
    const [from, to] = ends as number[];
    return from < to ? [from, to] : null;
  };
}
