import type { ChartTokens } from './EChart';

export type ChartDensity = 'compact' | 'regular' | 'full';

export const LABEL_FONT: Record<ChartDensity, number> = { compact: 11, regular: 11, full: 12 };

export function axes(tokens: ChartTokens, density: ChartDensity = 'regular') {
  return {
    // The x axis stays on the bottom edge. With ECharts' default it followed
    // y = 0 through the middle of phase, delay and power plots, while its
    // labels stayed at the bottom and the frame was left open there.
    axisLine: { lineStyle: { color: tokens.spine ?? tokens.grid }, onZero: false },
    axisTick: { lineStyle: { color: tokens.muted } },
    // Log-frequency ticks crowd as soon as a card narrows; dropping the
    // colliding ones beats printing "4001,000".
    axisLabel: { color: tokens.muted, fontSize: LABEL_FONT[density], margin: density === 'compact' ? 5 : 8, hideOverlap: true },
    // Whole-pixel lines: at .7 and .5 they fell between device pixels and drew
    // as two soft rows each. Minor lines are off: five sub-lines per division
    // read as noise on a card-sized plot. The log axis keeps its minor ticks.
    splitLine: { lineStyle: { color: tokens.grid, width: 1 } },
    minorSplitLine: { show: false },
  };
}
