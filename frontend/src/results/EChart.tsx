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
  // These values mirror hornlab_plots.style.EMBER_THEME and SEPIA_THEME, the
  // way they previously mirrored HORNLAB_THEME. Keeping the live charts on the
  // same role palette as the export gives them the canonical look without
  // replacing their responsive, inspectable canvas with a rendered PNG -- so
  // these must move together with DEFAULT_CHART_THEME in server/charts/api.py.
  //
  // muted is the tick/label role, which both themes set equal to their text
  // colour; mirrored as-is rather than dimmed, because the export does the
  // same and parity is the point of this table.
  //
  // The colormaps are the themes' own heatmap_cmap sampled at ten stops:
  // inferno for ember, magma for sepia. Both are perceptually uniform and
  // monotonic in lightness, unlike the custom ramp they replace.
  const light = typeof document !== 'undefined' && document.documentElement.dataset.theme === 'light';
  return light ? {
    background: '#FBF1DA',
    foreground: '#586E75',
    muted: '#586E75',
    spine: '#586E75',
    grid: '#93A1A1',
    gridMinor: 'rgba(147, 161, 161, .5)',
    accent: '#CB4B16',
    series: ['#268BD2', '#CB4B16', '#859900', '#6C71C4', '#B58900', '#2AA198'],
    colormap: ['#000004', '#180f3d', '#440f76', '#721f81', '#9e2f7f', '#cd4071', '#f1605d', '#fd9668', '#feca8d', '#fcfdbf'],
  } : {
    background: '#221C19',
    foreground: '#E8DDD3',
    muted: '#E8DDD3',
    spine: '#E8DDD3',
    grid: '#4A403A',
    gridMinor: 'rgba(74, 64, 58, .5)',
    accent: '#D98324',
    series: ['#3B6EA5', '#D98324', '#5FB49C', '#C77DFF', '#E8C547', '#E5657A'],
    colormap: ['#000004', '#1b0c41', '#4a0c6b', '#781c6d', '#a52c60', '#cf4446', '#ed6925', '#fb9b06', '#f7d13d', '#fcffa4'],
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
