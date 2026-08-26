import type { EChartsOption } from 'echarts';

/**
 * The hover ruler: a faint horizontal line drawn across the plot while the
 * pointer is over the value labels in the left gutter.
 *
 * Judging whether a directivity index or a beam width is *flat* means holding a
 * level in your eye across two decades of frequency, and the gridlines only
 * offer that at their own five- or ten-unit stops. The in-grid `cross`
 * axisPointer already draws such a line, but it arrives with the tooltip box
 * over the curve being read, which is exactly the thing in the way. Hovering
 * the axis instead puts a straightedge at any level with nothing else on the
 * chart.
 *
 * Everything here is geometry over the option object, so it stays testable
 * without an ECharts instance; the component supplies the pixel-to-value
 * conversion.
 */

/** Plot-area edges in container pixels. `left` doubles as the gutter width. */
export interface ChartGutter {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

type RulerAxis = { type?: string; name?: string; data?: Array<string | number> };

function primaryYAxis(option: EChartsOption): RulerAxis | undefined {
  const axis = option.yAxis;
  return (Array.isArray(axis) ? axis[0] : axis) as RulerAxis | undefined;
}

/**
 * Where the plot area sits, or `null` when this chart has no cartesian grid to
 * rule -- the polar response, whose radius labels are inside the plot.
 *
 * Only pixel insets are handled. Every chart in the panel states its grid that
 * way (`LINE_GRID`/`MAP_GRID`), and a percentage inset would need the axis
 * model rather than the option to resolve.
 */
export function chartGutter(option: EChartsOption, width: number, height: number): ChartGutter | null {
  const grid = (Array.isArray(option.grid) ? option.grid[0] : option.grid) as
    { left?: unknown; right?: unknown; top?: unknown; bottom?: unknown } | undefined;
  if (!grid) return null;
  const { left, right, top, bottom } = grid;
  if ([left, right, top, bottom].some((inset) => typeof inset !== 'number')) return null;
  const edges = {
    left: left as number,
    right: width - (right as number),
    top: top as number,
    bottom: height - (bottom as number),
  };
  if (!(edges.right > edges.left) || !(edges.bottom > edges.top)) return null;
  return edges;
}

/** Is the pointer over the left gutter's value labels? */
export function inAxisGutter(gutter: ChartGutter, x: number, y: number): boolean {
  return x >= 0 && x <= gutter.left && y >= gutter.top && y <= gutter.bottom;
}

/**
 * Decimals that make a one-pixel move of the pointer change the readout, so the
 * number matches the line the eye is following rather than lagging it in whole
 * steps. Capped at three: past that the chip is longer than the fact it states.
 */
export function rulerDecimals(span: number, pixels: number): number {
  if (!Number.isFinite(span) || span <= 0 || pixels <= 0) return 1;
  return Math.min(3, Math.max(0, Math.ceil(-Math.log10(span / pixels))));
}

/** `Directivity index [dB]` -> `dB`. Axis names are only set at full density. */
export function axisUnit(name?: string): string {
  return /\[([^\]]+)\]\s*$/.exec(name ?? '')?.[1] ?? '';
}

/**
 * What the ruler states, or `null` when the pixel maps to nothing on the axis.
 *
 * `convert` is the chart's own pixel-to-value mapping. On the directivity map
 * both axes are categories, so it hands back an ordinal and the angle has to be
 * read out of the axis data -- the same trap the angular guides fell into.
 */
export function rulerLabel(
  option: EChartsOption,
  gutter: ChartGutter,
  y: number,
  convert: (pixel: number) => number | null | undefined,
): string | null {
  const value = convert(y);
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  const axis = primaryYAxis(option);
  const unit = axisUnit(axis?.name);
  const suffix = unit ? ` ${unit}` : '';
  if (axis?.type === 'category') {
    const label = axis.data?.[Math.round(value)];
    return label === undefined ? null : `${label}${suffix}`;
  }
  const top = convert(gutter.top);
  const bottom = convert(gutter.bottom);
  const span = typeof top === 'number' && typeof bottom === 'number' && Number.isFinite(top) && Number.isFinite(bottom)
    ? Math.abs(top - bottom)
    : 0;
  return `${value.toFixed(rulerDecimals(span, gutter.bottom - gutter.top))}${suffix}`;
}
