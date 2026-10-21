import type { ChartTokens } from './EChart';

export type ChartDensity = 'compact' | 'regular' | 'full';

export const LABEL_FONT: Record<ChartDensity, number> = { compact: 11, regular: 11, full: 12 };

export function axes(tokens: ChartTokens, density: ChartDensity = 'regular') {
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
