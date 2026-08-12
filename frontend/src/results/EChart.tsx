import { Component, lazy, Suspense, useEffect, useState, type ReactNode } from 'react';
import type { EChartsOption } from 'echarts';

const LazyEChartRenderer = lazy(async () => {
  const module = await import('./EChartRenderer');
  return { default: module.EChartRenderer };
});

export class ChartErrorBoundary extends Component<
  { children: ReactNode; label: string },
  { failed: boolean }
> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  render() {
    if (this.state.failed) {
      return <div role="alert" aria-label={`${this.props.label} unavailable`} style={{ width: '100%', height: '100%', minHeight: 0 }}>
        Interactive chart unavailable. Reload to retry.
      </div>;
    }
    return this.props.children;
  }
}

export interface ChartTokens {
  background?: string;
  spine?: string;
  foreground: string;
  muted: string;
  grid: string;
  gridMinor: string;
  accent: string;
  series: string[];
  colormap: string[];
}

export function readChartTokens(): ChartTokens {
  // These values mirror hornlab_plots.style.CONSOLE_THEME and VELLUM_THEME,
  // which are the interface's own palettes rather than an independent design.
  // Keeping the live charts on the same role palette as the export gives them
  // the canonical look without replacing their responsive, inspectable canvas
  // with a rendered PNG -- so these must move together with
  // DEFAULT_CHART_THEME in server/charts/api.py and with the themes there.
  //
  // background is the panel colour, not a darker plot well. A chart ground the
  // interface uses nowhere else is what made the charts read as pasted into
  // the window instead of part of it.
  //
  // muted is the tick/label role, which both themes set equal to their text
  // colour; mirrored as-is rather than dimmed, because the export does the
  // same and parity is the point of this table.
  //
  // The colormaps are each theme's own heatmap_cmap sampled at ten stops.
  // Console reads the map on the arctic ramp -- its blue/violet/magenta travel
  // separates shallow gradients a single-hue warm ramp cannot. Vellum cannot
  // use arctic, whose floor is near-black and would make the quietest part of
  // the map the heaviest thing on a page, so it reads the Klippel ramp with
  // its white floor re-anchored to the page colour.
  const light = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light';
  return light ? {
    background: '#F1F2ED',
    foreground: '#1D1F20',
    muted: '#1D1F20',
    spine: '#1D1F20',
    grid: '#CBCDC5',
    gridMinor: 'rgba(203, 205, 197, .5)',
    accent: '#A5391B',
    series: ['#A5391B', '#3180C8', '#835F00', '#008F99', '#8B54B0', '#398D51'],
    colormap: ['#f1f2ed', '#c0d7ee', '#719fd4', '#3a71c0', '#3aa3c0', '#43c167', '#94d03f', '#e6e63a', '#ef942e', '#e11414'],
  } : {
    background: '#211F1D',
    foreground: '#ECE8E0',
    muted: '#ECE8E0',
    spine: '#ECE8E0',
    grid: '#4A453D',
    gridMinor: 'rgba(74, 69, 61, .5)',
    accent: '#E0673F',
    series: ['#E0673F', '#5D9BD9', '#AD8400', '#00A6AD', '#CA90F3', '#60B374'],
    colormap: ['#050c18', '#0a2039', '#10355f', '#205181', '#3b6a9a', '#42558e', '#4f3389', '#742369', '#a32e43', '#c84428'],
  };
}

export function useChartTokens(): ChartTokens {
  const [tokens, setTokens] = useState(readChartTokens);
  useEffect(() => {
    const observer = new MutationObserver(() => setTokens(readChartTokens()));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    return () => observer.disconnect();
  }, []);
  return tokens;
}

export function EChart({ option, label }: { option: EChartsOption; label: string }) {
  return <ChartErrorBoundary label={label}>
    <Suspense fallback={<div role="status" aria-label={`${label} loading`} style={{ width: '100%', height: '100%', minHeight: 0 }} />}>
      <LazyEChartRenderer option={option} label={label}/>
    </Suspense>
  </ChartErrorBoundary>;
}
