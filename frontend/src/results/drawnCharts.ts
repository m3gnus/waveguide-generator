import type { EChartsOption } from 'echarts';
import type { AxisExtent, ChartSpec } from './chartSpec';

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

/**
 * Things drawn on screen, keyed by the element that holds each: `register`
 * returns its own unregister, and `findIn` returns the first one inside a
 * container (matched by `selector`). One registry serves both the option-based
 * charts and the data-drawn views.
 */
function drawnRegistry<T extends object>(selector: string) {
  const entries = new WeakMap<Element, T>();
  return {
    register(element: Element, entry: T): () => void {
      entries.set(element, entry);
      return () => {
        if (entries.get(element) === entry) entries.delete(element);
      };
    },
    findIn(container: Element): T | null {
      for (const element of container.querySelectorAll(selector)) {
        const entry = entries.get(element);
        if (entry) return entry;
      }
      return null;
    },
  };
}

export const DRAWN_CHART_CLASS = 'chart-frame';

const charts = drawnRegistry<DrawnChart>(`.${DRAWN_CHART_CLASS}`);

export const registerDrawnChart = charts.register;
/** The first chart drawn inside `container`, if any. */
export const drawnChartIn = charts.findIn;

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

/**
 * A view drawn from data rather than an ECharts option (the 3D balloon and
 * the forward beam map): it hands the image its spec at its current frequency.
 */
export interface DrawnView {
  spec(title: string): ChartSpec | null;
}

export const DRAWN_VIEW_ATTRIBUTE = 'data-drawn-view';

const views = drawnRegistry<DrawnView>(`[${DRAWN_VIEW_ATTRIBUTE}]`);

export const registerDrawnView = views.register;
/** The first view drawn inside `container`, if any. */
export const drawnViewIn = views.findIn;
